#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
修复 cache_facade.py 中的迁移模式检查逻辑

问题：CacheFacade._check_use_migration_adapter() 默认返回 False，
与 signature_cache._is_migration_mode() 默认返回 True 不一致，
导致调用链中出现递归或缓存读写不一致问题。

修复：修改 _check_use_migration_adapter() 默认返回 True，
只有明确设置为 false 时才禁用。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-12
"""

import os
import shutil
from datetime import datetime

# 目标文件路径
TARGET_FILE = r"F:\antigravity2api\gcli2api\src\cache\cache_facade.py"

# 旧代码
OLD_CODE = '''    def _check_use_migration_adapter(self) -> bool:
        """检查是否使用迁移适配器"""
        env_value = os.environ.get(ENV_USE_MIGRATION_ADAPTER, "").lower()
        return env_value in ("true", "1", "yes", "on")'''

# 新代码
NEW_CODE = '''    def _check_use_migration_adapter(self) -> bool:
        """
        检查是否使用迁移适配器

        [FIX 2026-01-12] 默认启用迁移适配器，与 signature_cache._is_migration_mode() 保持一致。
        这避免了便捷函数代理到 CacheFacade 后，CacheFacade 又回调便捷函数导致的递归问题。

        只有明确设置环境变量为 false/0/no/off 时才禁用。
        """
        env_value = os.environ.get(ENV_USE_MIGRATION_ADAPTER, "").lower()
        # 只有明确设置为 false 时才禁用，否则默认启用
        if env_value in ("false", "0", "no", "off"):
            return False
        return True  # 默认启用'''


def main():
    print(f"[FIX] 开始修复 {TARGET_FILE}")

    # 读取文件内容
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查新代码是否已存在
    if "[FIX 2026-01-12] 默认启用迁移适配器" in content:
        print("[INFO] 修复已完成，无需重复修复。")
        return True

    # 检查旧代码是否存在
    if OLD_CODE not in content:
        print("[ERROR] 未找到需要替换的代码段！")
        # 显示当前的 _check_use_migration_adapter 方法
        for i, line in enumerate(content.split('\n')):
            if '_check_use_migration_adapter' in line:
                print(f"[DEBUG] 第 {i+1} 行: {line}")
        return False

    # 创建备份
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET_FILE + f".bak_{timestamp}"
    shutil.copy2(TARGET_FILE, backup_path)
    print(f"[BACKUP] 已创建备份: {backup_path}")

    # 替换代码
    new_content = content.replace(OLD_CODE, NEW_CODE)

    # 写入新内容
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 修复完成！")
    print("[INFO] CacheFacade._check_use_migration_adapter() 现在默认返回 True")

    # 验证
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        verify_content = f.read()

    if "return True  # 默认启用" in verify_content:
        print("[VERIFY] 验证通过！")
        return True
    else:
        print("[ERROR] 验证失败！")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
