#!/usr/bin/env python3
"""
修复 Copilot API content type 不支持问题

问题：Cursor 发送的消息 content 数组中包含非标准类型
错误：type has to be either 'image_url' or 'text'
解决：清理 content 数组，只保留 text 和 image_url 类型
"""

import re

# 添加 content 清理函数
CONTENT_SANITIZER_CODE = '''

def sanitize_message_content(content: Any) -> Any:
    """
    清理消息 content，确保只包含 Copilot 支持的类型。

    Copilot API 只支持:
    - string: 纯文本内容
    - array: 包含 {"type": "text", "text": "..."} 或 {"type": "image_url", ...} 的数组

    Cursor 可能发送的非标准类型:
    - {"type": "tool_result", ...}
    - {"type": "tool_use", ...}
    - {"type": "thinking", ...}
    - 等等
    """
    if content is None:
        return None

    # 字符串直接返回
    if isinstance(content, str):
        return content

    # 数组类型需要过滤
    if isinstance(content, list):
        sanitized = []
        for item in content:
            if not isinstance(item, dict):
                # 非 dict 项转为文本
                if item is not None:
                    sanitized.append({"type": "text", "text": str(item)})
                continue

            item_type = item.get("type", "")

            # 支持的类型直接保留
            if item_type == "text":
                # 确保有 text 字段
                if "text" in item and item["text"]:
                    sanitized.append({"type": "text", "text": str(item["text"])})
            elif item_type == "image_url":
                # 保留图片类型
                sanitized.append(item)
            else:
                # 不支持的类型，尝试提取文本内容
                extracted_text = None

                # 尝试从各种字段提取文本
                for field in ["text", "content", "output", "result", "data", "message"]:
                    if field in item and item[field]:
                        if isinstance(item[field], str):
                            extracted_text = item[field]
                            break
                        elif isinstance(item[field], dict):
                            # 嵌套的内容
                            nested = item[field]
                            for nf in ["text", "content", "output"]:
                                if nf in nested and isinstance(nested[nf], str):
                                    extracted_text = nested[nf]
                                    break

                if extracted_text:
                    log.debug(f"[Gateway] Converted {item_type} to text: {extracted_text[:100]}...")
                    sanitized.append({"type": "text", "text": extracted_text})
                else:
                    # 无法提取，记录警告并跳过
                    log.warning(f"[Gateway] Dropping unsupported content type: {item_type}")

        # 如果清理后为空，返回 None
        if not sanitized:
            return None

        # 如果只有一个纯文本项，直接返回字符串
        if len(sanitized) == 1 and sanitized[0].get("type") == "text":
            return sanitized[0].get("text")

        return sanitized

    # 其他类型尝试转为字符串
    return str(content)

'''

def main():
    file_path = "F:/antigravity2api/gcli2api/src/unified_gateway_router.py"

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 添加 sanitize_message_content 函数（在 normalize_messages 之前）
    if "def sanitize_message_content" not in content:
        # 找到 normalize_messages 函数并在其前面插入
        marker = "def normalize_messages(messages: List[Any])"
        if marker in content:
            content = content.replace(marker, CONTENT_SANITIZER_CODE + "\n" + marker)
            print("[OK] Added sanitize_message_content function")
        else:
            print("[ERROR] Could not find normalize_messages function")
            return
    else:
        print("[SKIP] sanitize_message_content already exists")

    # 2. 在 normalize_messages 中应用 content 清理
    # 找到添加消息到列表的地方，在添加前清理 content
    old_append = "normalized_messages.append(msg)"
    new_append = '''# 清理 content 以确保 Copilot 兼容性
        if "content" in msg:
            msg = {**msg, "content": sanitize_message_content(msg["content"])}
        normalized_messages.append(msg)'''

    if old_append in content and "sanitize_message_content(msg" not in content:
        # 只替换第一个出现的位置（在主循环中）
        content = content.replace(old_append, new_append, 1)
        print("[OK] Added content sanitization in normalize_messages")
    elif "sanitize_message_content(msg" in content:
        print("[SKIP] Content sanitization already applied")
    else:
        print("[WARNING] Could not find append statement to modify")

    # 保存文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("\n[SUCCESS] Content type sanitization added!")
    print("\nThis fix handles:")
    print("  - Filters out unsupported content types (tool_result, thinking, etc.)")
    print("  - Converts extractable content to text type")
    print("  - Ensures only 'text' and 'image_url' types are sent to Copilot")

if __name__ == "__main__":
    main()
