"""
Antigravity Service

目标：
- 让网关调用本地 Antigravity 逻辑时不走 127.0.0.1 HTTP 回环
- 复用现有 `src.antigravity_router.chat_completions` 的完整行为，避免产生与官方分叉的业务逻辑
"""

from __future__ import annotations

from typing import Any, Mapping

from starlette.datastructures import Headers


class _InMemoryRequest:
    """
    最小化 Request 适配器（duck typing）。

    `src.antigravity_router.chat_completions` 仅依赖：
    - request.headers（具备 .get/.keys 等接口，且大小写不敏感）
    - await request.json()
    """

    def __init__(self, *, headers: Mapping[str, str], body: Any):
        self.headers = Headers(headers)
        self._body = body

    async def json(self) -> Any:
        return self._body


async def handle_openai_chat_completions(*, body: dict, headers: Mapping[str, str]):
    """
    直调本地 Antigravity 的 OpenAI ChatCompletions 入口（不经网络）。

    返回值保持与 FastAPI handler 一致（JSONResponse / StreamingResponse / HTTPException）。
    """
    # 延迟导入：避免 import 时加载 router 带来不必要副作用/循环依赖
    from src import antigravity_router

    req = _InMemoryRequest(headers=headers, body=body)
    # token 在该 handler 内只用于 Depends 校验，业务逻辑不依赖它；这里直调无需重复鉴权
    return await antigravity_router.chat_completions(req, token="internal")

