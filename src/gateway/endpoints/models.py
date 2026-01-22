"""
Gateway 模型列表端点

包含模型列表、Augment/Bugment 兼容端点等。

从 unified_gateway_router.py 抽取的模型相关端点。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any, List
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..config import BACKENDS
from ..routing import get_sorted_backends, get_backend_base_url

# 延迟导入 log，避免循环依赖
try:
    from log import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# 延迟导入 http_client
try:
    from src.httpx_client import http_client
except ImportError:
    http_client = None

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


# ==================== 模型列表端点 ====================

@router.get("/v1/models")
@router.get("/models")  # 别名路由，兼容不同客户端配置
async def list_models(request: Request):
    """获取所有后端的模型列表（合并去重）"""
    log.debug(f"Models request received", tag="GATEWAY")
    all_models = set()

    for backend_key, backend_config in get_sorted_backends():
        try:
            # [FIX 2026-01-21] 使用辅助函数获取 base_url，支持 anyrouter 的 base_urls 格式
            base_url = get_backend_base_url(backend_config)
            if not base_url:
                log.debug(f"Skipping {backend_key}: no base_url configured", tag="GATEWAY")
                continue

            if http_client is not None:
                async with http_client.get_client(timeout=10.0) as client:
                    response = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": "Bearer dummy"}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        models = data.get("data", [])
                        for model in models:
                            model_id = model.get("id") if isinstance(model, dict) else model
                            if model_id:
                                all_models.add(model_id)
        except Exception as e:
            log.warning(f"Failed to get models from {backend_key}: {e}")

    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "owned_by": "gateway"} for m in sorted(all_models)]
    }


@router.get("/usage/api/get-models")  # Augment Code 兼容路由 - 返回对象数组
@router.get("/v1/usage/api/get-models")  # Augment Code 兼容路由（带版本号）- 返回对象数组
async def list_models_for_augment(request: Request):
    """获取所有后端的模型列表（合并去重）- Augment Code 格式（对象数组）"""
    log.debug(f"Augment models request received from {request.url.path}", tag="GATEWAY")
    # 使用字典存储模型信息，key 是 model_id，value 是完整的模型对象
    all_models_dict = {}

    for backend_key, backend_config in get_sorted_backends():
        try:
            # [FIX 2026-01-21] 使用辅助函数获取 base_url，支持 anyrouter 的 base_urls 格式
            base_url = get_backend_base_url(backend_config)
            if not base_url:
                log.debug(f"Skipping {backend_key}: no base_url configured", tag="GATEWAY")
                continue

            if http_client is not None:
                async with http_client.get_client(timeout=10.0) as client:
                    response = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": "Bearer dummy"}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        log.debug(f"Backend {backend_key} response: {data}", tag="GATEWAY")
                        models = data.get("data", [])
                        for model in models:
                            if isinstance(model, dict):
                                model_id = model.get("id", "")
                                if model_id:
                                    # 保留完整的模型信息，如果已存在则合并（优先保留更详细的信息）
                                    if model_id not in all_models_dict or len(str(model)) > len(str(all_models_dict[model_id])):
                                        all_models_dict[model_id] = model
                                    log.debug(f"Added model: {model_id} from {backend_key}", tag="GATEWAY")
                            elif isinstance(model, str):
                                # 如果上游返回的是字符串，创建基本对象
                                if model not in all_models_dict:
                                    all_models_dict[model] = {"id": model}
        except Exception as e:
            log.warning(f"Failed to get models from {backend_key}: {e}")

    # Augment Code 期望返回对象数组，每个对象包含 id、name、displayName 等字段
    model_list = []
    for model_id in sorted(all_models_dict.keys()):
        model_info = all_models_dict[model_id]
        # 构造 Augment Code 期望的格式
        augment_model = {
            "id": model_id,
            "name": model_info.get("name", model_id),
            "displayName": model_info.get("display_name") or model_info.get("displayName") or model_id,
        }
        # 保留其他可能有用的字段
        if "object" in model_info:
            augment_model["object"] = model_info["object"]
        if "owned_by" in model_info:
            augment_model["owned_by"] = model_info["owned_by"]
        if "type" in model_info:
            augment_model["type"] = model_info["type"]

        model_list.append(augment_model)

    log.debug(f"Returning {len(model_list)} models", tag="GATEWAY")
    return model_list


def _build_bugment_get_models_result() -> Dict[str, Any]:
    """
    Build an Augment-compatible `get-models` response (BackGetModelsResult).

    Bugment/VSCode uses this endpoint to populate:
    - default model
    - available models
    - feature flags (tasklist, prompt enhancer, model registry, etc.)

    Keep the payload minimal but schema-correct to avoid breaking client parsing.
    """
    # Use a stable default. The UI-selected model can override this, but the backend response
    # must provide a non-empty `default_model` or the client will fall back unpredictably.
    default_model = "gpt-4.1"

    # Provide a conservative, non-empty list for clients that expect at least one model.
    # Bugment can still load a richer model registry via `/usage/api/get-models`.
    models: List[Dict[str, Any]] = [
        {
            "name": model_id,
            "suggested_prefix_char_count": 8000,
            "suggested_suffix_char_count": 2000,
            "completion_timeout_ms": 120_000,
            "internal_name": model_id,
        }
        for model_id in ["gpt-4.1", "gpt-4", "gpt-5.1"]
    ]

    # Minimal feature flags needed to restore core agent behaviors.
    # Include both camelCase and snake_case variants for compatibility across client builds.
    feature_flags: Dict[str, Any] = {
        # Prompt enhancer
        "enablePromptEnhancer": True,
        "enable_prompt_enhancer": True,
        # Model registry (used by model selector + prompt enhancer model resolution)
        "enableModelRegistry": True,
        "enable_model_registry": True,
        # Agent auto mode & task list (enables task root initialization)
        "enableAgentAutoMode": True,
        "enable_agent_auto_mode": True,
        "vscodeTaskListMinVersion": "0.482.0",
        "vscode_task_list_min_version": "0.482.0",
        "vscodeSupportToolUseStartMinVersion": "0.485.0",
        "vscode_support_tool_use_start_min_version": "0.485.0",
    }

    return {
        "default_model": default_model,
        "models": models,
        # Do not include `languages` to allow the client to use its built-in defaults.
        "feature_flags": feature_flags,
        "user_tier": "COMMUNITY_TIER",
        "user": {"id": "local"},
    }


@router.post("/get-models")
@router.post("/v1/get-models")
async def get_models_for_bugment(
    request: Request,
    token: str = Depends(authenticate_bearer_allow_local_dummy)
):
    """Bugment/VSCode: returns BackGetModelsResult (POST /get-models)."""
    log.debug(f"Bugment get-models request received from {request.url.path}", tag="GATEWAY")
    return _build_bugment_get_models_result()
