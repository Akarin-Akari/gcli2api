# -*- coding: utf-8 -*-
"""
补丁脚本：修复 effective_model 未定义错误

在非流式请求中，第 382 行使用了 effective_model 变量，
但这个变量在之前的补丁中被删除了，需要改为使用 model_name
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_api.py"

# 修复 effective_model 未定义错误
PATCH_EFFECTIVE_MODEL = {
    "name": "非流式请求：修复 effective_model 未定义",
    "old": """        log.info(f"[ANTIGRAVITY] Using credential: {current_file} (model={effective_model}, attempt {attempt + 1}/{max_retries + 1})")

        # 构建请求头
        headers = build_antigravity_headers(access_token)

        try:
            # 发送非流式请求""",
    "new": """        log.info(f"[ANTIGRAVITY] Using credential: {current_file} (model={model_name}, attempt {attempt + 1}/{max_retries + 1})")

        # 构建请求头
        headers = build_antigravity_headers(access_token)

        try:
            # 发送非流式请求"""
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

    if PATCH_EFFECTIVE_MODEL["old"] in content:
        content = content.replace(PATCH_EFFECTIVE_MODEL["old"], PATCH_EFFECTIVE_MODEL["new"], 1)
        print(f"   [OK] {PATCH_EFFECTIVE_MODEL['name']}")
        applied += 1
    else:
        print(f"   [SKIP] {PATCH_EFFECTIVE_MODEL['name']} - 目标未找到")

    # 写入
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"\n[SUCCESS] 共应用 {applied} 个补丁!")
    print(f"\n[INFO] 补丁说明:")
    print(f"   - 将 effective_model 替换为 model_name")
    print(f"   - 修复 NameError: name 'effective_model' is not defined")

if __name__ == "__main__":
    main()
