# -*- coding: utf-8 -*-
"""
补丁脚本：实现 Cursor/Claude Code 解耦策略
- Cursor：不进行跨池降级，直接报错让 Gateway 路由到 Copilot
- Claude Code：启用跨池降级，因为 Claude Code 无法指定模型

通过 User-Agent 检测请求来源来区分策略
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============ 修改 antigravity_api.py ============
API_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_api.py"

# 1. 修改流式请求函数签名，添加 enable_cross_pool_fallback 参数
API_STREAM_SIGNATURE_PATCH = {
    "name": "流式请求：添加 enable_cross_pool_fallback 参数",
    "old": """async def send_antigravity_request_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
) -> Tuple[Any, str, Dict[str, Any]]:""",
    "new": """async def send_antigravity_request_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
    enable_cross_pool_fallback: bool = False,
) -> Tuple[Any, str, Dict[str, Any]]:"""
}

# 2. 修改流式请求的降级逻辑
API_STREAM_FALLBACK_PATCH = {
    "name": "流式请求：根据参数决定是否跨池降级",
    "old": """        # ✅ 解耦模式：不进行跨池降级，直接报错让 Gateway 路由到 Copilot
        # 用户选择 Claude 就用 Claude，选择 Gemini 就用 Gemini
        if not cred_result:
            log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
            raise Exception(f"No valid antigravity credentials available for model {model_name}")""",
    "new": """        # ✅ 根据 enable_cross_pool_fallback 参数决定降级策略
        if not cred_result:
            if enable_cross_pool_fallback:
                # Claude Code 模式：尝试跨池降级
                fallback_model = get_cross_pool_fallback(model_name, log_level="info")
                if fallback_model:
                    log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 凭证不可用，尝试降级到 {fallback_model}")
                    cred_result = await credential_manager.get_valid_credential(
                        is_antigravity=True, model_key=fallback_model
                    )
                    if cred_result:
                        # 更新请求体中的模型名
                        request_body["model"] = fallback_model
                        model_name = fallback_model  # 更新本地变量
                        log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")

            # 如果仍然没有凭证，报错
            if not cred_result:
                log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
                raise Exception(f"No valid antigravity credentials available for model {model_name}")"""
}

# 3. 修改非流式请求函数签名
API_NO_STREAM_SIGNATURE_PATCH = {
    "name": "非流式请求：添加 enable_cross_pool_fallback 参数",
    "old": """async def send_antigravity_request_no_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:""",
    "new": """async def send_antigravity_request_no_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
    enable_cross_pool_fallback: bool = False,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:"""
}

# 4. 修改非流式请求的降级逻辑
API_NO_STREAM_FALLBACK_PATCH = {
    "name": "非流式请求：根据参数决定是否跨池降级",
    "old": """        # ✅ 解耦模式：不进行跨池降级，直接报错让 Gateway 路由到 Copilot
        # 用户选择 Claude 就用 Claude，选择 Gemini 就用 Gemini
        if not cred_result:
            log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
            raise Exception(f"No valid antigravity credentials available for model {model_name}")""",
    "new": """        # ✅ 根据 enable_cross_pool_fallback 参数决定降级策略
        if not cred_result:
            if enable_cross_pool_fallback:
                # Claude Code 模式：尝试跨池降级
                fallback_model = get_cross_pool_fallback(model_name, log_level="info")
                if fallback_model:
                    log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 凭证不可用，尝试降级到 {fallback_model}")
                    cred_result = await credential_manager.get_valid_credential(
                        is_antigravity=True, model_key=fallback_model
                    )
                    if cred_result:
                        # 更新请求体中的模型名
                        request_body["model"] = fallback_model
                        model_name = fallback_model  # 更新本地变量
                        log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")

            # 如果仍然没有凭证，报错
            if not cred_result:
                log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
                raise Exception(f"No valid antigravity credentials available for model {model_name}")"""
}

def apply_api_patches():
    print(f"\n[FILE] 处理 antigravity_api.py")
    print(f"[READ] 读取文件: {API_FILE}")

    with open(API_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 备份
    backup_file = API_FILE + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy(API_FILE, backup_file)
    print(f"[BACKUP] 备份到: {backup_file}")

    applied = 0
    patches = [
        API_STREAM_SIGNATURE_PATCH,
        API_STREAM_FALLBACK_PATCH,
        API_NO_STREAM_SIGNATURE_PATCH,
        API_NO_STREAM_FALLBACK_PATCH,
    ]

    for patch in patches:
        if patch["old"] in content:
            content = content.replace(patch["old"], patch["new"], 1)
            print(f"   [OK] {patch['name']}")
            applied += 1
        else:
            print(f"   [SKIP] {patch['name']} - 目标未找到")

    # 写入
    with open(API_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    return applied

def main():
    total_applied = 0
    total_applied += apply_api_patches()

    print(f"\n[SUCCESS] 共应用 {total_applied} 个补丁!")
    print(f"\n[INFO] 解耦说明:")
    print(f"   - 添加了 enable_cross_pool_fallback 参数控制降级策略")
    print(f"   - 下一步需要在 router 中根据 User-Agent 传递该参数")

if __name__ == "__main__":
    main()
