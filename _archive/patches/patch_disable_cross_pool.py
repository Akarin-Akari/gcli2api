# -*- coding: utf-8 -*-
"""
补丁脚本：禁用跨池降级，实现 Claude/Gemini 解耦
- Claude 用尽 → 直接报错 → Gateway 路由到 Copilot
- Gemini 用尽 → 直接报错 → Gateway 路由到 Copilot
- 用户可以自由选择使用哪个模型池
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_api.py"

# 移除流式请求的跨池降级逻辑
STREAM_PATCH = {
    "name": "流式请求：禁用跨池降级",
    "old": """    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=model_name
        )

        # ✅ 跨池降级：如果当前模型被冷却，尝试降级到另一个池的模型
        effective_model = model_name
        if not cred_result:
            fallback_model = get_cross_pool_fallback(model_name, log_level="info")
            if fallback_model:
                log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 凭证不可用，尝试降级到 {fallback_model}")
                cred_result = await credential_manager.get_valid_credential(
                    is_antigravity=True, model_key=fallback_model
                )
                if cred_result:
                    effective_model = fallback_model
                    # 更新请求体中的模型名
                    request_body["model"] = fallback_model
                    log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")

        if not cred_result:
            log.error("[ANTIGRAVITY] No valid credentials available (including fallback)")
            raise Exception("No valid antigravity credentials available")""",
    "new": """    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=model_name
        )

        # ✅ 解耦模式：不进行跨池降级，直接报错让 Gateway 路由到 Copilot
        # 用户选择 Claude 就用 Claude，选择 Gemini 就用 Gemini
        if not cred_result:
            log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
            raise Exception(f"No valid antigravity credentials available for model {model_name}")"""
}

# 移除非流式请求的跨池降级逻辑
NO_STREAM_PATCH = {
    "name": "非流式请求：禁用跨池降级",
    "old": """    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=model_name
        )

        # ✅ 跨池降级：如果当前模型被冷却，尝试降级到另一个池的模型
        effective_model = model_name
        if not cred_result:
            fallback_model = get_cross_pool_fallback(model_name, log_level="info")
            if fallback_model:
                log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 凭证不可用，尝试降级到 {fallback_model}")
                cred_result = await credential_manager.get_valid_credential(
                    is_antigravity=True, model_key=fallback_model
                )
                if cred_result:
                    effective_model = fallback_model
                    # 更新请求体中的模型名
                    request_body["model"] = fallback_model
                    log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")

        if not cred_result:
            log.error("[ANTIGRAVITY] No valid credentials available (including fallback)")
            raise Exception("No valid antigravity credentials available")""",
    "new": """    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=model_name
        )

        # ✅ 解耦模式：不进行跨池降级，直接报错让 Gateway 路由到 Copilot
        # 用户选择 Claude 就用 Claude，选择 Gemini 就用 Gemini
        if not cred_result:
            log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
            raise Exception(f"No valid antigravity credentials available for model {model_name}")"""
}

def main():
    print(f"[READ] 读取文件: {TARGET_FILE}")

    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 备份
    backup_file = TARGET_FILE + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy(TARGET_FILE, backup_file)
    print(f"[BACKUP] 备份到: {backup_file}")

    applied = 0

    # 应用流式请求补丁
    if STREAM_PATCH["old"] in content:
        content = content.replace(STREAM_PATCH["old"], STREAM_PATCH["new"], 1)
        print(f"   [OK] {STREAM_PATCH['name']}")
        applied += 1
    else:
        print(f"   [SKIP] {STREAM_PATCH['name']} - 目标未找到")

    # 应用非流式请求补丁
    if NO_STREAM_PATCH["old"] in content:
        content = content.replace(NO_STREAM_PATCH["old"], NO_STREAM_PATCH["new"], 1)
        print(f"   [OK] {NO_STREAM_PATCH['name']}")
        applied += 1
    else:
        print(f"   [SKIP] {NO_STREAM_PATCH['name']} - 目标未找到")

    # 写入
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[SUCCESS] 共应用 {applied} 个补丁!")
    print(f"\n[INFO] 解耦说明:")
    print(f"   - Claude 模型额度用尽 → 直接报错 → Gateway 路由到 Copilot")
    print(f"   - Gemini 模型额度用尽 → 直接报错 → Gateway 路由到 Copilot")
    print(f"   - 用户可以自由选择使用哪个模型，不会被强制切换到另一个池")

if __name__ == "__main__":
    main()
