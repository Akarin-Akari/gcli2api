# -*- coding: utf-8 -*-
"""
补丁脚本：修复凭证层面的跨池降级
当 Claude 模型被冷却时，自动尝试降级到 Gemini 模型
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_api.py"

# 需要在文件顶部添加 fallback_manager 导入
IMPORT_PATCH = {
    "name": "添加 fallback_manager 导入",
    "old": "from log import log",
    "new": """from log import log
from .fallback_manager import get_cross_pool_fallback, get_model_pool"""
}

# 修改流式请求的凭证获取逻辑
STREAM_PATCH = {
    "name": "流式请求：添加跨池降级逻辑",
    "old": """    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=model_name
        )
        if not cred_result:
            log.error("[ANTIGRAVITY] No valid credentials available")
            raise Exception("No valid antigravity credentials available")""",
    "new": """    for attempt in range(max_retries + 1):
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
            raise Exception("No valid antigravity credentials available")"""
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

    # 应用导入补丁
    if IMPORT_PATCH["old"] in content and "from .fallback_manager import" not in content:
        content = content.replace(IMPORT_PATCH["old"], IMPORT_PATCH["new"], 1)
        print(f"   [OK] {IMPORT_PATCH['name']}")
        applied += 1
    elif "from .fallback_manager import" in content:
        print(f"   [SKIP] {IMPORT_PATCH['name']} - 已存在")
    else:
        print(f"   [SKIP] {IMPORT_PATCH['name']} - 目标未找到")

    # 应用流式请求补丁
    if STREAM_PATCH["old"] in content:
        content = content.replace(STREAM_PATCH["old"], STREAM_PATCH["new"], 1)
        print(f"   [OK] {STREAM_PATCH['name']}")
        applied += 1
    else:
        print(f"   [SKIP] {STREAM_PATCH['name']} - 目标未找到或已修改")

    # 写入
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[SUCCESS] 共应用 {applied} 个补丁!")
    print(f"\n[INFO] 修改说明:")
    print(f"   - 当 Claude 模型凭证被冷却时，自动尝试降级到 Gemini 模型")
    print(f"   - 当 Gemini 模型凭证被冷却时，自动尝试降级到 Claude 模型")
    print(f"   - 只有两个池都不可用时才抛出异常")

if __name__ == "__main__":
    main()
