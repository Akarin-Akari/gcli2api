# -*- coding: utf-8 -*-
"""
Patch script for tool_cleaner.py
添加 User-Agent 检测增强功能：版本提取、更精确匹配、详细日志

使用方法：python patch_tool_cleaner.py
"""

import shutil
import os
from datetime import datetime

# 文件路径
FILE_PATH = os.path.join(os.path.dirname(__file__), "tool_cleaner.py")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_archive", "backups")

def backup_file(file_path: str) -> str:
    """创建文件备份"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"tool_cleaner.py.bak.{timestamp}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    shutil.copy2(file_path, backup_path)
    print(f"[OK] Backup created: {backup_path}")
    return backup_path


def patch_file():
    """执行补丁"""
    # 读取原文件
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 创建备份
    backup_file(FILE_PATH)

    # 查找插入点
    insert_marker = "    return cleaned_tools\n\n\ndef detect_client_type(user_agent: str) -> str:"

    if insert_marker not in content:
        print("[ERROR] Cannot find insert marker! File may have been modified.")
        print("Trying alternative marker...")

        # 尝试替代标记
        alt_marker = "    return cleaned_tools\n\n\n# ==================== User-Agent 检测增强 ===================="
        if alt_marker in content:
            print("[OK] Found existing enhanced code, skipping patch.")
            return

        print("[ERROR] Cannot find suitable insert point, please modify manually.")
        return

    # 新增的代码
    new_code = '''    return cleaned_tools


# ==================== User-Agent 检测增强 ====================

# 客户端匹配规则（按优先级排序）
# 格式: (client_type, patterns, version_regex, display_name)
CLIENT_PATTERNS: List[Tuple[str, List[str], Optional[str], str]] = [
    # 高优先级：专用 AI 编程工具（精确匹配）
    ("cursor", ["cursor/", "cursor-"], r"cursor[/-]?(\\d+(?:\\.\\d+)*)", "Cursor IDE"),
    ("cline", ["cline/", "cline-", "claude-dev", "claudedev"], r"cline[/-]?(\\d+(?:\\.\\d+)*)", "Cline"),
    ("claude_code", ["claude-code/", "claude-code-", "anthropic-claude"], r"claude-code[/-]?(\\d+(?:\\.\\d+)*)", "Claude Code"),
    ("windsurf", ["windsurf/", "windsurf-"], r"windsurf[/-]?(\\d+(?:\\.\\d+)*)", "Windsurf IDE"),
    ("aider", ["aider/", "aider-"], r"aider[/-]?(\\d+(?:\\.\\d+)*)", "Aider"),
    ("continue_dev", ["continue/", "continue-dev"], r"continue[/-]?(\\d+(?:\\.\\d+)*)", "Continue.dev"),
    ("zed", ["zed/", "zed-editor"], r"zed[/-]?(\\d+(?:\\.\\d+)*)", "Zed Editor"),
    ("copilot", ["github-copilot", "copilot/"], r"copilot[/-]?(\\d+(?:\\.\\d+)*)", "GitHub Copilot"),

    # 中优先级：通用关键词（可能误匹配，放在后面）
    ("cursor", ["cursor"], None, "Cursor IDE"),
    ("claude_code", ["claude", "anthropic"], None, "Claude Code"),

    # 低优先级：SDK 和通用客户端
    ("openai_api", ["openai-python/", "openai-node/", "openai/"], r"openai[/-]?(\\d+(?:\\.\\d+)*)", "OpenAI SDK"),
    ("openai_api", ["python-requests/", "httpx/", "aiohttp/"], r"(?:python-requests|httpx|aiohttp)[/-]?(\\d+(?:\\.\\d+)*)", "HTTP Client"),
    ("openai_api", ["node-fetch/", "axios/", "got/"], r"(?:node-fetch|axios|got)[/-]?(\\d+(?:\\.\\d+)*)", "Node.js Client"),
]


def extract_version(user_agent: str, version_regex: Optional[str]) -> str:
    """
    从 User-Agent 中提取版本号

    Args:
        user_agent: HTTP User-Agent 头
        version_regex: 版本号正则表达式

    Returns:
        版本号字符串，如果未找到则返回空字符串
    """
    if not version_regex:
        return ""

    try:
        match = re.search(version_regex, user_agent, re.IGNORECASE)
        if match:
            return match.group(1)
    except Exception:
        pass

    return ""


def detect_client_type_with_version(user_agent: str) -> Tuple[str, str, str]:
    """
    检测客户端类型并提取版本号（增强版）

    Args:
        user_agent: HTTP User-Agent 头

    Returns:
        (client_type, version, display_name)
    """
    if not user_agent:
        return "unknown", "", "Unknown Client"

    user_agent_lower = user_agent.lower()

    # 按优先级顺序匹配
    for client_type, patterns, version_regex, display_name in CLIENT_PATTERNS:
        for pattern in patterns:
            if pattern in user_agent_lower:
                version = extract_version(user_agent, version_regex) if version_regex else ""
                log.debug(f"[TOOL CLEANER] Matched client: {display_name} (type={client_type}, version={version or 'unknown'})")
                return client_type, version, display_name

    return "unknown", "", "Unknown Client"


def detect_client_type(user_agent: str) -> str:'''

    # 替换内容
    new_content = content.replace(insert_marker, new_code)

    # 写入文件
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print("[OK] Patch applied successfully!")
    print("New features added:")
    print("  - CLIENT_PATTERNS: Client matching rules (sorted by priority)")
    print("  - extract_version(): Extract version from User-Agent")
    print("  - detect_client_type_with_version(): Detect client type with version")


if __name__ == "__main__":
    print("=" * 60)
    print("Tool Cleaner Patch Script")
    print("=" * 60)
    patch_file()
    print("=" * 60)
