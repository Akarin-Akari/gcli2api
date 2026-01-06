# -*- coding: utf-8 -*-
"""
补丁脚本：为 antigravity_anthropic_router.py 添加跨池降级参数

Claude Code 使用的是 /antigravity/v1/messages 端点，
需要在调用 API 时传递 enable_cross_pool_fallback=True
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_anthropic_router.py"

# 1. 修改 _handle_request_with_thinking_retry 中的流式调用
PATCH_THINKING_STREAM = {
    "name": "_handle_request_with_thinking_retry 流式调用",
    "old": """    try:
        if is_stream:
            return await send_antigravity_request_stream(request_body, cred_mgr)
        else:
            return await send_antigravity_request_no_stream(request_body, cred_mgr)""",
    "new": """    try:
        if is_stream:
            return await send_antigravity_request_stream(request_body, cred_mgr, enable_cross_pool_fallback=True)
        else:
            return await send_antigravity_request_no_stream(request_body, cred_mgr, enable_cross_pool_fallback=True)"""
}

# 2. 修改 _handle_request_with_thinking_retry 中的重试流式调用
PATCH_THINKING_RETRY_STREAM = {
    "name": "_handle_request_with_thinking_retry 重试流式调用",
    "old": """            # 重试请求
            if is_stream:
                return await send_antigravity_request_stream(cleaned_request_body, cred_mgr)
            else:
                return await send_antigravity_request_no_stream(cleaned_request_body, cred_mgr)""",
    "new": """            # 重试请求
            if is_stream:
                return await send_antigravity_request_stream(cleaned_request_body, cred_mgr, enable_cross_pool_fallback=True)
            else:
                return await send_antigravity_request_no_stream(cleaned_request_body, cred_mgr, enable_cross_pool_fallback=True)"""
}

# 3. 修改主流式请求调用（第 648 行附近）
PATCH_MAIN_STREAM = {
    "name": "主流式请求调用",
    "old": """                resources, cred_name, _ = await send_antigravity_request_stream(request_body, cred_mgr)""",
    "new": """                resources, cred_name, _ = await send_antigravity_request_stream(request_body, cred_mgr, enable_cross_pool_fallback=True)"""
}

# 4. 修改主非流式请求调用（第 724 行附近）
PATCH_MAIN_NO_STREAM = {
    "name": "主非流式请求调用",
    "old": """            response_data, _, _ = await send_antigravity_request_no_stream(request_body, cred_mgr)""",
    "new": """            response_data, _, _ = await send_antigravity_request_no_stream(request_body, cred_mgr, enable_cross_pool_fallback=True)"""
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
        PATCH_THINKING_STREAM,
        PATCH_THINKING_RETRY_STREAM,
        PATCH_MAIN_STREAM,
        PATCH_MAIN_NO_STREAM,
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
    print(f"   - Claude Code 使用 /antigravity/v1/messages 端点")
    print(f"   - 现在所有 API 调用都会传递 enable_cross_pool_fallback=True")
    print(f"   - 当 Claude 模型凭证不可用时，会自动降级到 Gemini 模型")
    print(f"\n[NEXT] 请重启服务并测试 Claude Code 请求")

if __name__ == "__main__":
    main()
