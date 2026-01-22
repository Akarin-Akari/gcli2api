"""
Gateway Anthropic 格式端点

包含 /v1/messages 等 Anthropic 兼容端点。

从 unified_gateway_router.py 抽取的 Anthropic 格式端点。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any
import json
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse, JSONResponse

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

__all__ = ["router"]


# ==================== Anthropic Messages 端点 ====================

@router.post("/v1/messages")
@router.post("/messages")  # 别名路由，兼容 Base URL 为 /gateway 的客户端
async def anthropic_messages(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """Anthropic Messages API 兼容端点"""
    log.info(f"Messages request received", tag="GATEWAY")
    
    # 检测客户端类型（用于日志和后续处理）
    try:
        from src.ide_compat import ClientTypeDetector
        client_info = ClientTypeDetector.detect(dict(request.headers))
    except Exception as e:
        log.warning(f"Failed to detect client type: {e}", tag="GATEWAY")
        client_info = None
    
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    model = body.get("model", "")
    stream = body.get("stream", False)

    headers = dict(request.headers)

    result = await route_request_with_fallback(
        endpoint="/messages",
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
            }
        )

    return JSONResponse(content=result)


@router.post("/v1/messages/count_tokens")
@router.post("/messages/count_tokens")  # 别名路由，兼容 Base URL 为 /gateway 的客户端
async def anthropic_messages_count_tokens(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """
    Anthropic Messages API 兼容的 token 计数端点。

    Claude CLI 在执行 /context 命令时会调用此端点来统计 token 使用量。
    这是一个辅助端点，不消耗配额，只返回估算的 token 数量。
    """
    log.info(f"Count tokens request received", tag="GATEWAY")

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # 简单估算 token 数量
    input_tokens = 0

    try:
        messages = body.get("messages", [])
        system_prompt = body.get("system", "")

        # 粗略估算：每4个字符约等于1个token（对于混合中英文）
        total_chars = len(system_prompt) if isinstance(system_prompt, str) else 0

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # 多模态内容
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            total_chars += len(item.get("text", ""))
                        elif item.get("type") == "image":
                            # 图片大约消耗 1000 tokens
                            total_chars += 4000

        # 粗略估算
        input_tokens = max(1, total_chars // 4)

    except Exception as e:
        log.warning(f"Token estimation failed: {e}", tag="GATEWAY")
        input_tokens = 100  # 默认值

    log.debug(f"Estimated input tokens: {input_tokens}", tag="GATEWAY")

    return JSONResponse(content={"input_tokens": input_tokens})
