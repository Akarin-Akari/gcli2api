#!/usr/bin/env python3
"""
修复启动脚本卡住问题
问题：taskkill /F /IM bun.exe 会卡住
解决：使用 start /b 在后台执行
"""

import shutil
from datetime import datetime

def main():
    file_path = "F:/antigravity2api/启动全部服务.bat"

    # 备份原文件
    backup_path = f"{file_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(file_path, backup_path)
    print(f"[OK] Backup created: {backup_path}")

    # 使用 gbk 编码读取 (Windows 批处理默认编码)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        print("[INFO] File read with UTF-8 encoding")
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk") as f:
            content = f.read()
        print("[INFO] File read with GBK encoding")

    # 替换卡住的 taskkill 命令
    old_line = 'taskkill /F /IM bun.exe >nul 2>&1'
    new_lines = '''REM 使用 start /b 在后台执行 taskkill，避免卡住
start /b "" cmd /c "taskkill /F /IM bun.exe >nul 2>&1"
REM 短暂等待确保命令已发出
ping -n 2 127.0.0.1 >nul'''

    if old_line in content:
        content = content.replace(old_line, new_lines)
        print("[OK] Fixed taskkill hang issue")
    elif "start /b" in content and "taskkill" in content:
        print("[SKIP] Already fixed")
        return
    else:
        print("[ERROR] Could not find taskkill line to fix")
        return

    # 使用 utf-8 写入
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("[SUCCESS] Launcher script fixed!")

if __name__ == "__main__":
    main()
