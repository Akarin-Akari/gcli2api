"""
Gateway OpenAI 格式端点

包含 /v1/chat/completions 等 OpenAI 兼容端点。

从 unified_gateway_router.py 抽取的 OpenAI 格式端点。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any, AsyncGenerator
import json
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse, JSONResponse

from ..normalization import normalize_request_body
from ..proxy import route_request_with_fallback

# 延迟导入 log，避免循环依赖
try:
    from log import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# 延迟导入认证依赖
try:
    from src.auth import authenticate_bearer, authenticate_bearer_allow_local_dummy
except ImportError:
    # 提供默认的认证函数
    async def authenticate_bearer():
        return "dummy"
    async def authenticate_bearer_allow_local_dummy():
        return "dummy"

router = APIRouter()

__all__ = ["router", "convert_sse_to_augment_ndjson"]


# ==================== OpenAI Chat Completions 端点 ====================

@router.post("/v1/chat/completions")
@router.post("/chat/completions")  # 别名路由，兼容 Base URL 为 /gateway 的客户端
async def chat_completions(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """统一聊天完成端点 - 自动路由到最佳后端"""
    log.info(f"Chat request received", tag="GATEWAY")
    
    # 检测客户端类型（用于日志和后续处理）
    try:
        from src.ide_compat import ClientTypeDetector
        client_info = ClientTypeDetector.detect(dict(request.headers))
    except Exception as e:
        log.warning(f"Failed to detect client type: {e}", tag="GATEWAY")
        client_info = None
    
    try:
        raw_body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # DEBUG: Log incoming messages to diagnose tool call issues
    raw_messages = raw_body.get("messages", [])
    log.debug(f" Incoming messages count: {len(raw_messages)}")
    for i, msg in enumerate(raw_messages[-5:]):  # Only log last 5 messages
        if isinstance(msg, dict):
            role = msg.get("role", "unknown")
            has_content = "content" in msg and msg["content"] is not None
            has_tool_calls = "tool_calls" in msg
            tool_call_id = msg.get("tool_call_id", None)
            log.debug(f" Message {i}: role={role}, has_content={has_content}, has_tool_calls={has_tool_calls}, tool_call_id={tool_call_id}")
            if role == "tool":
                log.debug(f" Tool result message: {json.dumps(msg, ensure_ascii=False)[:500]}")
            if role == "assistant" and has_tool_calls:
                log.debug(f" Assistant tool_calls: {json.dumps(msg.get('tool_calls', []), ensure_ascii=False)[:500]}")

    # Normalize request body to standard OpenAI format
    body = normalize_request_body(raw_body)

    model = body.get("model", "")
    stream = body.get("stream", False)

    headers = dict(request.headers)

    result = await route_request_with_fallback(
        endpoint="/chat/completions",
        method="POST",
        headers=headers,
        body=body,
        model=model,
        stream=stream,
    )

    if stream and hasattr(result, "__anext__"):
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    return JSONResponse(content=result)


# ==================== SSE 到 NDJSON 转换 ====================

async def convert_sse_to_augment_ndjson(sse_stream: AsyncGenerator) -> AsyncGenerator[str, None]:
    """
    将 SSE 格式流转换为 Augment Code 期望的 NDJSON 格式流

    OpenAI SSE 格式: data: {"choices":[{"delta":{"content":"你好"}}]}\n\n
    Augment NDJSON 格式: {"text":"你好"}\n

    Args:
        sse_stream: SSE 格式的异步生成器（可能返回 bytes 或 str）

    Yields:
        Augment NDJSON 格式的字符串（每行一个 {"text": "..."} 对象）
    """
    buffer = ""

    async for chunk in sse_stream:
        if not chunk:
            continue

        # 处理字节类型，转换为字符串
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="ignore")
        elif not isinstance(chunk, str):
            chunk = str(chunk)

        # 将 chunk 添加到缓冲区
        buffer += chunk

        # 按行处理缓冲区（SSE 格式以 \n\n 分隔事件）
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()

            # 跳过空行
            if not line:
                continue

            # 检查是否是 SSE 格式的 data: 行
            if line.startswith("data: "):
                # 提取 JSON 数据
                json_str = line[6:].strip()  # 移除 "data: " 前缀

                # 跳过 [DONE] 标记
                if json_str == "[DONE]":
                    continue

                # 验证是否是有效的 JSON
                try:
                    # 解析 OpenAI 格式的 JSON
                    json_obj = json.loads(json_str)

                    # 提取 content 字段转换为 Augment 格式
                    # OpenAI: {"choices":[{"delta":{"content":"xxx"}}]}
                    # Augment: {"text":"xxx"}
                    if "choices" in json_obj and len(json_obj["choices"]) > 0:
                        choice = json_obj["choices"][0]

                        # 处理流式响应的 delta
                        if "delta" in choice:
                            delta = choice["delta"]

                            # NOTE:
                            # When upstream chooses to call tools, OpenAI streaming returns `delta.tool_calls`
                            # (often with no `delta.content`). If we drop these deltas, the VSCode client will
                            # look like it "ended immediately" when a tool is attempted.
                            tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else None
                            if isinstance(tool_calls, list) and tool_calls:
                                try:
                                    log.warning(
                                        f"[TOOL CALL] Upstream returned tool_calls (count={len(tool_calls)}), "
                                        f"first={json.dumps(tool_calls[0], ensure_ascii=False)[:500]}",
                                        tag="GATEWAY",
                                    )
                                except Exception:
                                    log.warning("[TOOL CALL] Upstream returned tool_calls (unable to dump)", tag="GATEWAY")

                                # Emit a visible message so the user isn't left with an empty response.
                                augment_obj = {
                                    "text": (
                                        "\n[Gateway] 上游模型触发了工具调用(tool_calls)，但当前网关尚未实现将 tool_calls "
                                        "转换/执行为 Augment 工具链的逻辑，因此工具步骤无法继续。"
                                    )
                                }
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"

                            if "content" in delta and delta["content"] is not None:
                                augment_obj = {"text": delta["content"]}
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"

                        # 处理完整响应的 message
                        elif "message" in choice:
                            message = choice["message"]
                            if "content" in message and message["content"] is not None:
                                augment_obj = {"text": message["content"]}
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"

                        # 处理 finish_reason
                        if "finish_reason" in choice and choice["finish_reason"] is not None:
                            # Augment 不需要 finish_reason，跳过
                            if choice["finish_reason"] in ("tool_calls", "function_call"):
                                log.warning(f"[TOOL CALL] finish_reason={choice['finish_reason']}", tag="GATEWAY")
                            continue

                except json.JSONDecodeError:
                    # 如果不是有效的 JSON，记录警告但继续处理
                    log.warning(f"Invalid JSON in SSE stream: {json_str[:100]}")
                    continue
            elif line.startswith(":"):
                # SSE 注释行，跳过
                continue
            elif line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
                # 其他 SSE 字段，跳过
                continue
