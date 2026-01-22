"""
IDECompatMiddleware - FastAPI 中间件

用于在请求处理前后执行 IDE 兼容逻辑:
1. 请求前: 检测客户端类型,提取 SCID
2. 请求前: 对 IDE 客户端的消息进行净化
3. 响应后: 更新权威历史 (TODO)
4. 响应后: 缓存新的签名 (TODO)

只对 Anthropic Messages API 路径生效:
- /antigravity/v1/messages
- /v1/messages

Author: Claude Sonnet 4.5 (浮浮酱)
Date: 2026-01-17
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional, Dict, Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from starlette.datastructures import Headers

from .client_detector import ClientTypeDetector, ClientInfo
from .sanitizer import AnthropicSanitizer

log = logging.getLogger("gcli2api.ide_compat.middleware")


class IDECompatMiddleware(BaseHTTPMiddleware):
    """
    IDE 兼容中间件

    职责:
    1. 请求前: 检测客户端类型,提取 SCID
    2. 请求前: 对 IDE 客户端的消息进行净化
    3. 响应后: 更新权威历史 (TODO)
    4. 响应后: 缓存新的签名 (TODO)

    对以下 API 路径生效:
    - /antigravity/v1/messages (Anthropic Native)
    - /v1/messages (Anthropic Native)
    - /antigravity/v1/chat/completions (OpenAI Compatible - Cursor/IDE)
    - /v1/chat/completions (OpenAI Compatible)
    """

    # 需要处理的路径
    # [FIX 2026-01-21] 添加 OpenAI 兼容路径，修复 Cursor 等 IDE 客户端的 thinking block 处理
    TARGET_PATHS = [
        # Anthropic Native API
        "/antigravity/v1/messages",
        "/v1/messages",
        # OpenAI Compatible API (Cursor, Windsurf 等 IDE 使用此路径)
        "/antigravity/v1/chat/completions",
        "/v1/chat/completions",
    ]

    def __init__(
        self,
        app: ASGIApp,
        sanitizer: Optional[AnthropicSanitizer] = None,
    ):
        """
        初始化中间件

        Args:
            app: ASGI 应用
            sanitizer: AnthropicSanitizer 实例 (可选,如果为 None 则使用全局实例)
        """
        super().__init__(app)
        self.sanitizer = sanitizer

        # 统计信息
        self.stats = {
            "total_requests": 0,
            "sanitized_requests": 0,
            "skipped_requests": 0,
            "errors": 0,
        }

        log.info("[IDE_COMPAT_MIDDLEWARE] Initialized")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        中间件主逻辑

        Args:
            request: FastAPI Request 对象
            call_next: 下游处理函数

        Returns:
            Response 对象
        """
        try:
            # 1. 检查是否需要处理
            if not self._should_process(request):
                self.stats["skipped_requests"] += 1
                return await call_next(request)

            self.stats["total_requests"] += 1

            # 2. 检测客户端类型
            client_info = ClientTypeDetector.detect(dict(request.headers))

            # 3. 如果是 Claude Code,直接放行
            if not client_info.needs_sanitization:
                log.debug(
                    f"[IDE_COMPAT_MIDDLEWARE] Client {client_info.display_name} "
                    f"does not need sanitization, skipping"
                )
                self.stats["skipped_requests"] += 1
                return await call_next(request)

            # 4. 读取请求体
            body = await self._read_body(request)
            if not body:
                log.warning("[IDE_COMPAT_MIDDLEWARE] Failed to read request body, skipping")
                self.stats["skipped_requests"] += 1
                return await call_next(request)

            # 5. 净化消息
            sanitized_body = await self._sanitize_request(body, client_info)

            # 6. 创建新的请求对象 (带净化后的 body)
            new_request = self._create_modified_request(request, sanitized_body)

            # 7. 调用下游处理
            response = await call_next(new_request)

            # 8. 处理响应 (更新状态)
            # TODO: 实现响应处理逻辑
            # if response.status_code == 200:
            #     await self._process_response(response, client_info, sanitized_body)

            self.stats["sanitized_requests"] += 1
            return response

        except Exception as e:
            # 中间件不能影响主流程,所有异常都捕获并记录
            log.error(f"[IDE_COMPAT_MIDDLEWARE] Error processing request: {e}", exc_info=True)
            self.stats["errors"] += 1

            # 返回原始请求的处理结果
            try:
                return await call_next(request)
            except Exception as inner_e:
                log.error(f"[IDE_COMPAT_MIDDLEWARE] Error in fallback call_next: {inner_e}", exc_info=True)
                # 如果连 call_next 都失败了,返回 500 错误
                return Response(
                    content=json.dumps({"error": "Internal server error"}),
                    status_code=500,
                    media_type="application/json"
                )

    def _should_process(self, request: Request) -> bool:
        """
        检查是否需要处理此请求

        Args:
            request: FastAPI Request 对象

        Returns:
            是否需要处理
        """
        # 只处理 POST 请求
        if request.method != "POST":
            return False

        # 检查路径
        path = request.url.path
        for target_path in self.TARGET_PATHS:
            if path == target_path or path.endswith(target_path):
                return True

        return False

    async def _read_body(self, request: Request) -> Optional[Dict[str, Any]]:
        """
        读取并解析请求体

        Args:
            request: FastAPI Request 对象

        Returns:
            解析后的 JSON 字典,如果失败则返回 None
        """
        try:
            # 读取原始请求体
            body_bytes = await request.body()

            # 解析 JSON
            body = json.loads(body_bytes.decode("utf-8"))

            return body

        except json.JSONDecodeError as e:
            log.warning(f"[IDE_COMPAT_MIDDLEWARE] Failed to parse JSON body: {e}")
            return None
        except Exception as e:
            log.error(f"[IDE_COMPAT_MIDDLEWARE] Error reading body: {e}", exc_info=True)
            return None

    async def _sanitize_request(
        self,
        body: Dict[str, Any],
        client_info: ClientInfo
    ) -> Dict[str, Any]:
        """
        净化请求

        1. 提取参数
        2. 使用 AnthropicSanitizer 净化消息
        3. 更新请求体

        Args:
            body: 原始请求体
            client_info: 客户端信息

        Returns:
            净化后的请求体
        """
        try:
            # 1. 提取参数
            messages = body.get("messages", [])
            thinking_config = body.get("thinking")
            thinking_enabled = thinking_config is not None

            # 提取 SCID (优先使用 header 中的,其次使用 body 中的)
            session_id = client_info.scid
            if not session_id:
                # 尝试从 body 中提取 (某些客户端可能在 body 中传递)
                session_id = body.get("conversation_id") or body.get("session_id")

            # 2. 获取 sanitizer 实例
            if self.sanitizer is None:
                from .sanitizer import get_sanitizer
                self.sanitizer = get_sanitizer()

            # 3. 净化消息
            sanitized_messages, final_thinking_enabled = self.sanitizer.sanitize_messages(
                messages=messages,
                thinking_enabled=thinking_enabled,
                session_id=session_id,
                last_thought_signature=None  # TODO: 从状态管理器获取
            )

            # 4. 更新请求体
            sanitized_body = body.copy()
            sanitized_body["messages"] = sanitized_messages

            # 同步 thinking 配置
            if not final_thinking_enabled and "thinking" in sanitized_body:
                log.info("[IDE_COMPAT_MIDDLEWARE] Removing thinking config due to sanitization")
                sanitized_body.pop("thinking", None)

            log.info(
                f"[IDE_COMPAT_MIDDLEWARE] Sanitized request: "
                f"client={client_info.display_name}, "
                f"messages={len(messages)}->{len(sanitized_messages)}, "
                f"thinking={thinking_enabled}->{final_thinking_enabled}"
            )

            return sanitized_body

        except Exception as e:
            log.error(f"[IDE_COMPAT_MIDDLEWARE] Error sanitizing request: {e}", exc_info=True)
            # 出错时返回原始 body
            return body

    def _create_modified_request(
        self,
        original: Request,
        new_body: Dict[str, Any]
    ) -> Request:
        """
        创建带有修改后 body 的新请求

        这是一个技巧性的实现:
        1. 将新 body 序列化为 JSON bytes
        2. 创建一个新的 Request 对象,但共享大部分属性
        3. 替换 _body 属性

        Args:
            original: 原始 Request 对象
            new_body: 新的请求体字典

        Returns:
            新的 Request 对象
        """
        try:
            # 序列化新 body
            new_body_bytes = json.dumps(new_body, ensure_ascii=False).encode("utf-8")

            # 创建新的 scope (复制原始 scope)
            scope = original.scope.copy()

            # 更新 Content-Length header
            headers = dict(original.headers)
            headers["content-length"] = str(len(new_body_bytes))

            # 创建新的 Headers 对象
            new_headers = Headers(headers)

            # 创建新的 Request 对象
            # 注意: 这里我们直接替换 _body 属性,这是一个 hack
            # 但在 Starlette 中这是安全的,因为 body() 方法会优先使用 _body
            new_request = Request(scope, receive=original.receive)
            new_request._body = new_body_bytes

            # 更新 headers
            scope["headers"] = [
                (k.encode(), v.encode())
                for k, v in headers.items()
            ]

            return new_request

        except Exception as e:
            log.error(f"[IDE_COMPAT_MIDDLEWARE] Error creating modified request: {e}", exc_info=True)
            # 出错时返回原始请求
            return original

    async def _process_response(
        self,
        response: Response,
        client_info: ClientInfo,
        request_body: Dict[str, Any]
    ) -> None:
        """
        处理响应

        TODO: 实现以下功能:
        1. 提取响应中的签名
        2. 更新权威历史
        3. 缓存签名

        Args:
            response: Response 对象
            client_info: 客户端信息
            request_body: 请求体
        """
        # TODO: 实现响应处理逻辑
        pass

    def get_stats(self) -> Dict[str, int]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return self.stats.copy()

    def reset_stats(self) -> None:
        """重置统计信息"""
        for key in self.stats:
            self.stats[key] = 0


# ============================================================================
# 便捷函数
# ============================================================================

def create_ide_compat_middleware(
    app: ASGIApp,
    sanitizer: Optional[AnthropicSanitizer] = None,
) -> IDECompatMiddleware:
    """
    创建 IDE 兼容中间件

    Args:
        app: ASGI 应用
        sanitizer: AnthropicSanitizer 实例 (可选)

    Returns:
        IDECompatMiddleware 实例
    """
    return IDECompatMiddleware(app, sanitizer=sanitizer)


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "IDECompatMiddleware",
    "create_ide_compat_middleware",
]
