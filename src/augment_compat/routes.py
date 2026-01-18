# -*- coding: utf-8 -*-
"""
Routes - Augment 兼容层路由对接
==============================

/gateway/chat-stream 等路由的薄层实现。

此模块负责：
- 接收 Augment 格式请求
- 调用上游 LLM API
- 将响应转换为 NDJSON 流
- 处理 Tool Loop
"""

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from .types import (
    AugmentChatRequest,
    AugmentToolDefinition,
    ChatRequestNodeType,
    ChatResultNodeType,
    ToolResultContent,
)
from .ndjson import NDJSONStreamBuilder, ndjson_encode_line
from .nodes_bridge import StreamNodeConverter, create_stop_reason_node
from .tools_bridge import (
    convert_tools_to_openai,
    convert_tools_to_anthropic,
    parse_tool_definitions_from_request,
    normalize_tool_choice,
)
from .request_normalize import (
    is_endpoint_allowed,
    is_endpoint_blocked,
    normalize_request,
    validate_request,
    sanitize_headers,
    RequestRateLimiter,
)

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/gateway", tags=["augment-compat"])

# 限流器
_rate_limiter = RequestRateLimiter(max_requests=100, window_seconds=60)


@router.post("/chat-stream")
async def chat_stream(request: Request):
    """
    Augment chat-stream 端点。

    接收 Augment 格式请求，返回 NDJSON 流式响应。
    """
    # 获取客户端标识
    client_ip = request.client.host if request.client else "unknown"

    # 限流检查
    if not _rate_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    # 解析请求体
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 验证请求
    is_valid, error_msg = validate_request(body)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # 标准化请求
    normalized_body = normalize_request(body)

    # 创建流式响应
    return StreamingResponse(
        generate_chat_stream(normalized_body),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


async def generate_chat_stream(
    request_body: Dict[str, Any]
) -> AsyncGenerator[str, None]:
    """
    生成 chat-stream NDJSON 响应。

    这是核心的流处理函数，负责：
    1. 解析请求
    2. 调用上游 LLM
    3. 转换响应为 NDJSON
    4. 处理 Tool Loop（如果需要）

    Args:
        request_body: 标准化后的请求体

    Yields:
        NDJSON 格式的响应行
    """
    builder = NDJSONStreamBuilder()
    request_id = request_body.get("request_id", "unknown")

    try:
        # 解析工具定义
        tool_definitions = parse_tool_definitions_from_request(
            request_body.get("tool_definitions", [])
        )

        # 解析节点
        nodes = request_body.get("nodes", [])

        # 检查是否是 TOOL_RESULT 回注
        is_tool_result = any(
            node.get("type") == ChatRequestNodeType.TOOL_RESULT
            for node in nodes
        )

        # TODO: 调用实际的上游 LLM API
        # 这里需要集成到现有的 unified_gateway_router.py
        #
        # 示例流程：
        # 1. 构建上游请求
        # 2. 调用上游 API（流式）
        # 3. 转换每个 chunk 为 NDJSON node

        # 临时：返回一个模拟响应
        yield builder.text("Hello! I received your request. ")
        yield builder.text("This is a placeholder response. ")
        yield builder.text("Tool loop integration is pending.")

        # 如果有工具定义，模拟一个工具调用
        if tool_definitions and not is_tool_result:
            tool = tool_definitions[0]
            yield builder.tool_use(
                tool_id=f"toolu_{request_id[:8]}",
                tool_name=tool.name,
                tool_input={"example": "value"}
            )
            yield builder.stop("tool_use", usage={"input_tokens": 100, "output_tokens": 50})
        else:
            yield builder.stop("end_turn", usage={"input_tokens": 100, "output_tokens": 50})

    except Exception as e:
        logger.error(f"Error in chat_stream: {e}", exc_info=True)
        # 返回错误节点
        error_node = {
            "type": ChatResultNodeType.RAW_RESPONSE,
            "data": {"text": f"\n\n[Error: {str(e)}]"},
            "error": True
        }
        yield ndjson_encode_line(error_node)
        yield builder.stop("end_turn")


async def process_tool_loop(
    request_body: Dict[str, Any],
    tool_calls: List[Dict[str, Any]],
    upstream_client: Any  # 实际的上游客户端
) -> AsyncGenerator[str, None]:
    """
    处理 Tool Loop。

    当上游返回 tool_use 时，此函数负责：
    1. 输出 TOOL_USE node
    2. 等待插件执行工具并返回 TOOL_RESULT
    3. 将 TOOL_RESULT 回注到上游
    4. 继续生成响应

    Args:
        request_body: 原始请求体
        tool_calls: 工具调用列表
        upstream_client: 上游 API 客户端

    Yields:
        NDJSON 格式的响应行
    """
    builder = NDJSONStreamBuilder()

    # 输出工具调用节点
    for tc in tool_calls:
        yield builder.tool_use(
            tool_id=tc.get("id", ""),
            tool_name=tc.get("name", ""),
            tool_input=tc.get("input", {})
        )

    # 输出 stop_reason: tool_use
    yield builder.stop("tool_use")

    # 注意：Tool Loop 的后续处理依赖于插件端的行为
    # 插件会发送新的请求，包含 TOOL_RESULT 节点
    # 那时会再次进入 chat_stream 端点


def build_upstream_messages(
    nodes: List[Dict[str, Any]],
    chat_history: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    构建发送给上游的 messages 列表。

    将 Augment nodes 转换为 OpenAI/Anthropic messages 格式。

    Args:
        nodes: Augment 请求节点
        chat_history: 历史对话

    Returns:
        上游 API 格式的 messages
    """
    messages = []

    # 添加历史消息
    for msg in chat_history:
        if isinstance(msg, dict) and "role" in msg:
            messages.append(msg)

    # 转换当前节点
    for node in nodes:
        node_type = node.get("type", 0)

        if node_type == ChatRequestNodeType.TEXT:
            messages.append({
                "role": "user",
                "content": node.get("text", "")
            })

        elif node_type == ChatRequestNodeType.TOOL_RESULT:
            tool_result = node.get("tool_result", {})
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_result.get("tool_use_id", ""),
                        "content": tool_result.get("content", ""),
                        "is_error": tool_result.get("is_error", False)
                    }
                ]
            })

        elif node_type == ChatRequestNodeType.IMAGE:
            # 图片节点处理
            image_data = node.get("image_data", "")
            if image_data:
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_data
                            }
                        }
                    ]
                })

    return messages


# ============== 辅助端点 ==============

@router.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "module": "augment_compat"}


@router.post("/blocked")
async def blocked_endpoint(request: Request):
    """
    被阻断的端点统一处理。

    返回成功但不执行实际操作。
    """
    return {"success": True, "blocked": True}


# ============== 集成辅助函数 ==============

def create_augment_compat_router() -> APIRouter:
    """
    创建 Augment 兼容层路由器。

    Returns:
        配置好的 APIRouter
    """
    return router


def integrate_with_unified_gateway(app, gateway_router):
    """
    将 augment_compat 集成到现有的 unified_gateway_router。

    Args:
        app: FastAPI 应用实例
        gateway_router: 现有的网关路由器
    """
    # 注册路由
    app.include_router(router)

    # 可以在这里添加中间件或其他集成逻辑
    logger.info("Augment compatibility layer integrated successfully")
