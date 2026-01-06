# -*- coding: utf-8 -*-
"""
补丁脚本：修复 antigravity_anthropic_router.py 中的降级逻辑

问题：
- 错误消息 "No valid antigravity credentials available for model xxx"
  不匹配 is_quota_exhausted_error()
- 导致 Claude Code 请求在凭证不可用时直接返回 500 错误，而不是降级到 Gemini

解决方案：
1. 添加 is_credential_unavailable_error 到 import
2. 在流式和非流式请求的错误处理中添加凭证不可用错误的处理
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_anthropic_router.py"

# 补丁 1: 添加 is_credential_unavailable_error 到 import
PATCH_IMPORT = {
    "name": "添加 is_credential_unavailable_error 到 import",
    "old": '''from .fallback_manager import (
    is_quota_exhausted_error,
    is_retryable_error,
    is_403_error,
    get_cross_pool_fallback,
    is_haiku_model,
    is_model_supported,
    HAIKU_FALLBACK_TARGET,
    COPILOT_URL,
)''',
    "new": '''from .fallback_manager import (
    is_quota_exhausted_error,
    is_retryable_error,
    is_403_error,
    is_credential_unavailable_error,
    get_cross_pool_fallback,
    is_haiku_model,
    is_model_supported,
    HAIKU_FALLBACK_TARGET,
    COPILOT_URL,
)'''
}

# 补丁 2: 在流式请求的错误处理中添加凭证不可用错误处理
PATCH_STREAM_CREDENTIAL_ERROR = {
    "name": "流式请求：添加凭证不可用错误处理",
    "old": '''            # 3. 额度用尽错误 - 触发跨池降级
            if is_quota_exhausted_error(error_msg):
                log.warning(f"[ANTHROPIC FALLBACK] 检测到额度用尽错误")
                if attempt_idx < len(models_to_try) - 1:
                    log.warning(f"[ANTHROPIC FALLBACK] 将尝试下一个降级模型")
                    continue
                else:
                    log.error(f"[ANTHROPIC FALLBACK] 所有降级模型均已尝试")
                    return _anthropic_error(status_code=429, message="All quota pools exhausted", error_type="rate_limit_error")

            # 4. 其他错误 - 直接返回
            return _anthropic_error(status_code=500, message="下游请求失败", error_type="api_error")
        else:
            # 所有模型都失败
            log.error(f"[ANTHROPIC] 所有降级模型均失败: {last_error}")
            return _anthropic_error(status_code=500, message="下游请求失败（已尝试所有降级模型）", error_type="api_error")

        async def stream_generator():''',
    "new": '''            # 3. 额度用尽错误 - 触发跨池降级
            if is_quota_exhausted_error(error_msg):
                log.warning(f"[ANTHROPIC FALLBACK] 检测到额度用尽错误")
                if attempt_idx < len(models_to_try) - 1:
                    log.warning(f"[ANTHROPIC FALLBACK] 将尝试下一个降级模型")
                    continue
                else:
                    log.error(f"[ANTHROPIC FALLBACK] 所有降级模型均已尝试")
                    return _anthropic_error(status_code=429, message="All quota pools exhausted", error_type="rate_limit_error")

            # 4. 凭证不可用错误 - 触发跨池降级（与额度用尽相同处理）
            if is_credential_unavailable_error(error_msg):
                log.warning(f"[ANTHROPIC FALLBACK] 检测到凭证不可用错误")
                if attempt_idx < len(models_to_try) - 1:
                    log.warning(f"[ANTHROPIC FALLBACK] 将尝试下一个降级模型")
                    continue
                else:
                    log.error(f"[ANTHROPIC FALLBACK] 所有降级模型均已尝试")
                    return _anthropic_error(status_code=503, message="All credentials unavailable", error_type="api_error")

            # 5. 其他错误 - 直接返回
            return _anthropic_error(status_code=500, message="下游请求失败", error_type="api_error")
        else:
            # 所有模型都失败
            log.error(f"[ANTHROPIC] 所有降级模型均失败: {last_error}")
            return _anthropic_error(status_code=500, message="下游请求失败（已尝试所有降级模型）", error_type="api_error")

        async def stream_generator():'''
}

# 补丁 3: 在非流式请求的错误处理中添加凭证不可用错误处理
PATCH_NO_STREAM_CREDENTIAL_ERROR = {
    "name": "非流式请求：添加凭证不可用错误处理",
    "old": '''            # 3. 额度用尽错误 - 触发跨池降级
            if is_quota_exhausted_error(error_msg):
                log.warning(f"[ANTHROPIC FALLBACK] 检测到额度用尽错误")
                if attempt_idx < len(models_to_try) - 1:
                    log.warning(f"[ANTHROPIC FALLBACK] 将尝试下一个降级模型")
                    continue
                else:
                    log.error(f"[ANTHROPIC FALLBACK] 所有降级模型均已尝试")
                    return _anthropic_error(status_code=429, message="All quota pools exhausted", error_type="rate_limit_error")

            # 4. 其他错误 - 直接返回
            return _anthropic_error(status_code=500, message="下游请求失败", error_type="api_error")
    else:
        # 所有模型都失败
        log.error(f"[ANTHROPIC] 所有降级模型均失败: {last_error}")
        return _anthropic_error(status_code=500, message="下游请求失败（已尝试所有降级模型）", error_type="api_error")

    anthropic_response = _convert_antigravity_response_to_anthropic_message(''',
    "new": '''            # 3. 额度用尽错误 - 触发跨池降级
            if is_quota_exhausted_error(error_msg):
                log.warning(f"[ANTHROPIC FALLBACK] 检测到额度用尽错误")
                if attempt_idx < len(models_to_try) - 1:
                    log.warning(f"[ANTHROPIC FALLBACK] 将尝试下一个降级模型")
                    continue
                else:
                    log.error(f"[ANTHROPIC FALLBACK] 所有降级模型均已尝试")
                    return _anthropic_error(status_code=429, message="All quota pools exhausted", error_type="rate_limit_error")

            # 4. 凭证不可用错误 - 触发跨池降级（与额度用尽相同处理）
            if is_credential_unavailable_error(error_msg):
                log.warning(f"[ANTHROPIC FALLBACK] 检测到凭证不可用错误")
                if attempt_idx < len(models_to_try) - 1:
                    log.warning(f"[ANTHROPIC FALLBACK] 将尝试下一个降级模型")
                    continue
                else:
                    log.error(f"[ANTHROPIC FALLBACK] 所有降级模型均已尝试")
                    return _anthropic_error(status_code=503, message="All credentials unavailable", error_type="api_error")

            # 5. 其他错误 - 直接返回
            return _anthropic_error(status_code=500, message="下游请求失败", error_type="api_error")
    else:
        # 所有模型都失败
        log.error(f"[ANTHROPIC] 所有降级模型均失败: {last_error}")
        return _anthropic_error(status_code=500, message="下游请求失败（已尝试所有降级模型）", error_type="api_error")

    anthropic_response = _convert_antigravity_response_to_anthropic_message('''
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
    patches = [
        PATCH_IMPORT,
        PATCH_STREAM_CREDENTIAL_ERROR,
        PATCH_NO_STREAM_CREDENTIAL_ERROR,
    ]

    for patch in patches:
        if patch["old"] in content:
            content = content.replace(patch["old"], patch["new"], 1)
            print(f"   [OK] {patch['name']}")
            applied += 1
        else:
            print(f"   [SKIP] {patch['name']} - 目标未找到")

    # 写入
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"\n[SUCCESS] 共应用 {applied} 个补丁!")
    print(f"\n[INFO] 补丁说明:")
    print(f"   - 添加了 is_credential_unavailable_error 到 import")
    print(f"   - 在流式和非流式请求的错误处理中添加凭证不可用错误的处理")
    print(f"   - 凭证不可用时会尝试降级到下一个模型（Gemini）")
    print(f"\n[NEXT] 请重启服务并测试 Claude Code 请求")


if __name__ == "__main__":
    main()
