# -*- coding: utf-8 -*-
"""
补丁脚本：添加凭证不可用错误识别

问题：
- 错误消息 "No valid antigravity credentials available for model xxx"
  不匹配任何已知的错误类型，被归类为"未知错误"
- 导致 Cursor 选 Claude 时既不降级也不路由到 Copilot

解决方案：
1. 添加 is_credential_unavailable_error 函数
2. 在 antigravity_router.py 中处理这个错误类型
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ==================== 补丁 1: fallback_manager.py ====================
FALLBACK_MANAGER_FILE = r"F:\antigravity2api\gcli2api\src\fallback_manager.py"

# 在 is_403_error 函数后添加 is_credential_unavailable_error 函数
PATCH_ADD_CREDENTIAL_ERROR_FUNC = {
    "name": "添加 is_credential_unavailable_error 函数",
    "old": '''def is_403_error(error_msg: str) -> bool:
    """判断是否是 403 错误（需要验证）"""
    status_code = get_status_code_from_error(str(error_msg))
    return status_code == 403''',
    "new": '''def is_403_error(error_msg: str) -> bool:
    """判断是否是 403 错误（需要验证）"""
    status_code = get_status_code_from_error(str(error_msg))
    return status_code == 403


def is_credential_unavailable_error(error_msg: str) -> bool:
    """
    判断是否是凭证不可用错误

    这类错误表示模型的凭证池已耗尽（所有凭证都在冷却中），
    应该触发 Gateway 层的 fallback 到 Copilot
    """
    error_lower = str(error_msg).lower()

    # 检测凭证不可用的关键词
    credential_unavailable_keywords = [
        'no valid antigravity credentials',
        'no valid credentials',
        'credentials unavailable',
        'credential pool exhausted',
    ]

    for keyword in credential_unavailable_keywords:
        if keyword in error_lower:
            return True

    return False'''
}

# ==================== 补丁 2: antigravity_router.py ====================
ROUTER_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_router.py"

# 修改 import 语句
PATCH_IMPORT = {
    "name": "添加 is_credential_unavailable_error 到 import",
    "old": '''from .fallback_manager import (
                is_quota_exhausted_error, is_retryable_error, is_403_error,
                get_cross_pool_fallback, is_haiku_model, HAIKU_FALLBACK_TARGET
            )''',
    "new": '''from .fallback_manager import (
                is_quota_exhausted_error, is_retryable_error, is_403_error,
                is_credential_unavailable_error,
                get_cross_pool_fallback, is_haiku_model, HAIKU_FALLBACK_TARGET
            )'''
}

# 在"未知错误类型"之前添加凭证不可用错误处理
PATCH_HANDLE_CREDENTIAL_ERROR = {
    "name": "添加凭证不可用错误处理",
    "old": '''            # 4. 其他错误 - 直接失败
            log.info(f"[ANTIGRAVITY] 未知错误类型，不触发降级")
            raise HTTPException(status_code=500, detail=f"Antigravity API request failed: {error_msg}")''',
    "new": '''            # 4. 凭证不可用错误 - 返回 503 让 Gateway 路由到 Copilot
            if is_credential_unavailable_error(error_msg):
                log.warning(f"[ANTIGRAVITY] 凭证不可用，返回 503 让 Gateway 路由到备用后端")
                raise HTTPException(status_code=503, detail=f"Antigravity credentials unavailable: {error_msg}")

            # 5. 其他错误 - 直接失败
            log.info(f"[ANTIGRAVITY] 未知错误类型，不触发降级")
            raise HTTPException(status_code=500, detail=f"Antigravity API request failed: {error_msg}")'''
}


def apply_patch(file_path, patch):
    """应用单个补丁"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    if patch["old"] in content:
        content = content.replace(patch["old"], patch["new"], 1)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False


def main():
    print("=" * 60)
    print("补丁：添加凭证不可用错误识别")
    print("=" * 60)

    # 备份文件
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    for file_path in [FALLBACK_MANAGER_FILE, ROUTER_FILE]:
        backup = file_path + f".bak.{timestamp}"
        shutil.copy(file_path, backup)
        print(f"[BACKUP] {file_path} -> {backup}")

    print()

    # 应用补丁 1: fallback_manager.py
    print(f"[PATCH] {FALLBACK_MANAGER_FILE}")
    if apply_patch(FALLBACK_MANAGER_FILE, PATCH_ADD_CREDENTIAL_ERROR_FUNC):
        print(f"   [OK] {PATCH_ADD_CREDENTIAL_ERROR_FUNC['name']}")
    else:
        print(f"   [SKIP] {PATCH_ADD_CREDENTIAL_ERROR_FUNC['name']} - 目标未找到")

    print()

    # 应用补丁 2: antigravity_router.py
    print(f"[PATCH] {ROUTER_FILE}")

    patches = [PATCH_IMPORT, PATCH_HANDLE_CREDENTIAL_ERROR]
    for patch in patches:
        if apply_patch(ROUTER_FILE, patch):
            print(f"   [OK] {patch['name']}")
        else:
            print(f"   [SKIP] {patch['name']} - 目标未找到")

    print()
    print("=" * 60)
    print("[SUCCESS] 补丁应用完成!")
    print()
    print("[INFO] 修改说明:")
    print("   1. 添加了 is_credential_unavailable_error() 函数")
    print("   2. 凭证不可用时返回 503 状态码")
    print("   3. Gateway 收到 503 后会尝试路由到 Copilot")
    print()
    print("[NEXT] 请重启服务并测试 Cursor 请求")
    print("=" * 60)


if __name__ == "__main__":
    main()
