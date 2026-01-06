# -*- coding: utf-8 -*-
"""
Patch script for message_converter.py and antigravity_router.py
Feature: Encourage model to use Sequential Thinking tool when internal thinking is disabled

This patch:
1. Modifies openai_messages_to_antigravity_contents to accept recommend_sequential_thinking param
2. Adds logic to check for sequential_thinking tool and inject prompt
3. Updates antigravity_router.py to pass this parameter when appropriate

Usage: python patch_sequential_thinking.py
"""

import os
import re
from datetime import datetime

# File paths
MESSAGE_CONVERTER_PATH = os.path.join(os.path.dirname(__file__), "converters", "message_converter.py")
ROUTER_PATH = os.path.join(os.path.dirname(__file__), "antigravity_router.py")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_archive", "backups")

def backup_file(file_path: str) -> str:
    """Create file backup"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.basename(file_path)
    backup_name = f"{filename}.bak.{timestamp}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    import shutil
    shutil.copy2(file_path, backup_path)
    print(f"[OK] Backup created: {backup_path}")
    return backup_path

def patch_message_converter():
    """Patch message_converter.py"""
    print(f"Patching {MESSAGE_CONVERTER_PATH}...")
    backup_file(MESSAGE_CONVERTER_PATH)

    with open(MESSAGE_CONVERTER_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add Prompt Constant
    prompt_code = '''
SEQUENTIAL_THINKING_PROMPT = """
[IMPORTANT: Thinking Capability Redirection]
Internal thinking/reasoning models are currently disabled or limited.
For complex tasks requiring step-by-step analysis, planning, or reasoning, you MUST use the 'sequentialthinking' (or 'sequential_thinking') tool.
Do NOT attempt to output <think> tags or raw reasoning text. Delegate all reasoning steps to the tool.
"""
'''
    if "SEQUENTIAL_THINKING_PROMPT" not in content:
        # Insert after TOOL_FORMAT_REMINDER_AFTER_ERROR_TEMPLATE
        content = content.replace('"""\n\n\ndef openai_messages_to_antigravity_contents', '"""\n' + prompt_code + '\n\ndef openai_messages_to_antigravity_contents')
        print("[OK] Added SEQUENTIAL_THINKING_PROMPT")
    else:
        print("[SKIP] SEQUENTIAL_THINKING_PROMPT already exists")

    # 2. Update function signature
    if "recommend_sequential_thinking: bool = False" not in content:
        content = content.replace(
            "tools: Optional[List[Any]] = None\n) -> List[Dict[str, Any]]:",
            "tools: Optional[List[Any]] = None,\n    recommend_sequential_thinking: bool = False\n) -> List[Dict[str, Any]]:"
        )
        print("[OK] Updated function signature")
    else:
        print("[SKIP] Function signature already updated")

    # 3. Add logic to check tool and inject prompt
    # We need to insert this logic at the beginning of the function

    logic_code = '''    from .tool_converter import extract_tool_params_summary

    # Check for sequential thinking tool
    has_sequential_tool = False
    if recommend_sequential_thinking and tools:
        for tool in tools:
            name = ""
            if isinstance(tool, dict):
                if "function" in tool:
                    name = tool["function"].get("name", "")
                else:
                    name = tool.get("name", "")
            elif hasattr(tool, "function"):
                name = getattr(tool.function, "name", "")
            elif hasattr(tool, "name"):
                name = getattr(tool, "name", "")

            if name and "sequential" in name.lower() and "thinking" in name.lower():
                has_sequential_tool = True
                break
'''

    if "Check for sequential thinking tool" not in content:
        content = content.replace(
            '    from .tool_converter import extract_tool_params_summary',
            logic_code
        )
        print("[OK] Added tool check logic")
    else:
        print("[SKIP] Tool check logic already exists")

    # 4. Inject prompt into system message
    injection_code = '''            if has_tools:
                # 提取工具参数摘要（从传入的 tools 参数中提取）'''

    new_injection_code = '''            # Inject Sequential Thinking prompt if recommended and available
            if has_sequential_tool:
                content = content + SEQUENTIAL_THINKING_PROMPT
                log.info("[ANTIGRAVITY] Injected Sequential Thinking prompt into system message")

            if has_tools:
                # 提取工具参数摘要（从传入的 tools 参数中提取）'''

    if "Injected Sequential Thinking prompt" not in content:
        content = content.replace(injection_code, new_injection_code)
        print("[OK] Added prompt injection logic")
    else:
        print("[SKIP] Prompt injection logic already exists")

    with open(MESSAGE_CONVERTER_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    return True

def patch_router():
    """Patch antigravity_router.py"""
    print(f"Patching {ROUTER_PATH}...")
    backup_file(ROUTER_PATH)

    with open(ROUTER_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Find where to insert the recommendation logic
    # Look for: messages = strip_thinking_from_openai_messages(messages)
    # This happens when enable_thinking is disabled

    insert_idx = -1
    call_idx = -1

    for i, line in enumerate(lines):
        if "contents = openai_messages_to_antigravity_contents(" in line:
            call_idx = i
        if "log.info(f\"[ANTIGRAVITY] Thinking 已禁用，已清理历史消息中的 thinking 内容块" in line:
            insert_idx = i + 1

    if call_idx == -1:
        print("[ERROR] Cannot find openai_messages_to_antigravity_contents call")
        return False

    # 1. Insert recommendation logic
    # We want to insert it before the conversion call
    # But we need to make sure we have access to 'model' and 'enable_thinking'

    # Let's insert it right before the conversion call
    if insert_idx == -1 or insert_idx > call_idx:
        insert_idx = call_idx

    logic_lines = [
        '\n',
        '    # 决定是否推荐 Sequential Thinking\n',
        '    # 条件：是 Thinking 模型，但 Thinking 被禁用（例如因为历史消息缺少 signature）\n',
        '    recommend_sequential = is_thinking_model(model) and not enable_thinking\n',
        '    if recommend_sequential:\n',
        '        log.info(f"[ANTIGRAVITY] Thinking disabled for {model}, recommending Sequential Thinking tool if available")\n',
        '\n'
    ]

    # Check if already inserted
    if "recommend_sequential =" not in "".join(lines[call_idx-10:call_idx]):
        for line in reversed(logic_lines):
            lines.insert(call_idx, line)
        print("[OK] Added recommendation logic")
        # Update call_idx because we inserted lines
        call_idx += len(logic_lines)
    else:
        print("[SKIP] Recommendation logic already exists")

    # 2. Update function call
    # We need to add recommend_sequential_thinking=recommend_sequential

    # The call might span multiple lines
    call_end_idx = call_idx
    while ")" not in lines[call_end_idx]:
        call_end_idx += 1

    call_block = "".join(lines[call_idx:call_end_idx+1])

    if "recommend_sequential_thinking=" not in call_block:
        # Replace the last argument or the closing parenthesis
        if "tools=tools" in call_block:
            new_call_block = call_block.replace("tools=tools", "tools=tools, recommend_sequential_thinking=recommend_sequential")
        else:
            # Fallback regex replacement
            new_call_block = re.sub(r'\)\s*$', ', recommend_sequential_thinking=recommend_sequential)', call_block.rstrip()) + '\n'

        # Replace lines
        lines[call_idx:call_end_idx+1] = [new_call_block]
        print("[OK] Updated function call parameters")
    else:
        print("[SKIP] Function call parameters already updated")

    # 3. Handle the second call in the fallback logic (if any)
    # There is another call inside:
    # messages = strip_thinking_from_openai_messages(messages)
    # contents = openai_messages_to_antigravity_contents(messages, enable_thinking=False, tools=tools)

    # Search for other calls
    for i in range(call_end_idx + 1, len(lines)):
        if "contents = openai_messages_to_antigravity_contents(" in lines[i]:
            # This is likely the fallback call when thinking signature is missing
            # We should also pass recommend_sequential=True here because enable_thinking is False
            line = lines[i]
            if "recommend_sequential_thinking=" not in line:
                if "tools=tools" in line:
                    lines[i] = line.replace("tools=tools", "tools=tools, recommend_sequential_thinking=True") # Force True here as we know thinking failed
                    print(f"[OK] Updated fallback function call at line {i+1}")

    with open(ROUTER_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return True

if __name__ == "__main__":
    print("=" * 60)
    print("Sequential Thinking Recommendation Patch")
    print("=" * 60)

    try:
        if patch_message_converter() and patch_router():
            print("\n[SUCCESS] All patches applied successfully!")
        else:
            print("\n[WARNING] Some patches may have failed or were skipped.")
    except Exception as e:
        print(f"\n[ERROR] An error occurred: {e}")
        import traceback
        traceback.print_exc()
