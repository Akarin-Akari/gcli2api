#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix OpenAI Responses API format conversion to Chat Completions API format.

Problem: Cursor sends messages in OpenAI Responses API format:
  - {"type": "function_call", "call_id": "xxx", "name": "func", "arguments": "{}"}
  - {"type": "function_call_output", "call_id": "xxx", "output": "result"}

But our gateway expects Chat Completions API format:
  - {"role": "assistant", "tool_calls": [...]}
  - {"role": "tool", "tool_call_id": "xxx", "content": "result"}

This causes tool results to be dropped, leading to infinite agent loops.
"""

file_path = 'src/unified_gateway_router.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# ==================== Fix 1: Add convert_responses_api_message function ====================

convert_function = '''
def convert_responses_api_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert OpenAI Responses API format message to Chat Completions API format.

    Responses API format (what Cursor sends):
    - {"type": "message", "role": "user", "content": [...]}
    - {"type": "function_call", "call_id": "xxx", "name": "func", "arguments": "{}"}
    - {"type": "function_call_output", "call_id": "xxx", "output": "result"}

    Chat Completions API format (what backends expect):
    - {"role": "user", "content": "..."}
    - {"role": "assistant", "tool_calls": [{"id": "xxx", "type": "function", "function": {"name": "func", "arguments": "{}"}}]}
    - {"role": "tool", "tool_call_id": "xxx", "content": "result"}

    Returns:
        Converted message dict, or None if conversion not applicable
    """
    if not isinstance(msg, dict):
        return None

    msg_type = msg.get("type")

    # Already has role - standard Chat Completions format
    if "role" in msg:
        return msg

    # Type: message - extract role and content
    if msg_type == "message":
        role = msg.get("role", "user")
        content = msg.get("content", "")
        # Handle content array (multi-modal)
        if isinstance(content, list):
            # Convert Responses API content format to Chat Completions format
            converted_content = []
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "input_text":
                        converted_content.append({
                            "type": "text",
                            "text": item.get("text", "")
                        })
                    elif item_type == "output_text":
                        converted_content.append({
                            "type": "text",
                            "text": item.get("text", "")
                        })
                    elif item_type == "input_image":
                        # Handle image content
                        converted_content.append({
                            "type": "image_url",
                            "image_url": item.get("image_url", item.get("url", ""))
                        })
                    else:
                        # Keep as-is if already in correct format
                        converted_content.append(item)
                else:
                    converted_content.append(item)
            content = converted_content if converted_content else ""

        return {"role": role, "content": content}

    # Type: function_call - convert to assistant message with tool_calls
    if msg_type == "function_call":
        call_id = msg.get("call_id", msg.get("id", ""))
        name = msg.get("name", "")
        arguments = msg.get("arguments", "{}")

        # Ensure arguments is a string
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)

        log.info(f"[Gateway] Converting function_call: call_id={call_id}, name={name}")

        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments
                }
            }]
        }

    # Type: function_call_output - convert to tool message
    if msg_type == "function_call_output":
        call_id = msg.get("call_id", msg.get("id", ""))
        output = msg.get("output", "")

        # Ensure output is a string
        if isinstance(output, dict):
            output = json.dumps(output, ensure_ascii=False)
        elif isinstance(output, list):
            output = json.dumps(output, ensure_ascii=False)

        log.info(f"[Gateway] Converting function_call_output: call_id={call_id}, output_len={len(str(output))}")

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": output
        }

    # Type: reasoning - convert to assistant message (for o1/o3 models)
    if msg_type == "reasoning":
        content = msg.get("content", "")
        if isinstance(content, list):
            # Extract text from content array
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
            content = "\\n".join(texts)
        return {"role": "assistant", "content": content}

    # Unknown type - log and return None
    log.warning(f"[Gateway] Unknown Responses API message type: {msg_type}, keys: {list(msg.keys())}")
    return None

'''

# Check if already added
if 'def convert_responses_api_message' in content:
    print("[SKIP] convert_responses_api_message function already exists")
else:
    # Find position to insert - after normalize_tools function
    insert_marker = 'def normalize_messages(messages: List[Any])'
    if insert_marker in content:
        insert_pos = content.find(insert_marker)
        content = content[:insert_pos] + convert_function + '\n' + content[insert_pos:]
        print("[OK] Added convert_responses_api_message function")
    else:
        print("[FAIL] Could not find insertion point for convert_responses_api_message")


# ==================== Fix 2: Update normalize_messages to use conversion ====================

old_normalize_messages = '''def normalize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    """
    Normalize and filter messages array.
    - Remove null/None values
    - Remove invalid message objects
    - Ensure each message has required fields
    """
    normalized_messages = []

    for msg in messages:
        # Skip null/None values
        if msg is None:
            continue

        # Skip non-dict values
        if not isinstance(msg, dict):
            log.warning(f"[Gateway] Skipping non-dict message: {type(msg)}")
            continue

        # Ensure message has 'role' field
        if "role" not in msg:
            log.warning(f"[Gateway] Skipping message without role: {list(msg.keys())}")
            continue

        # Ensure message has 'content' field (can be None for tool calls)
        # But the message object itself must exist
        normalized_messages.append(msg)

    return normalized_messages'''

new_normalize_messages = '''def normalize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    """
    Normalize and filter messages array.
    - Remove null/None values
    - Remove invalid message objects
    - Convert OpenAI Responses API format to Chat Completions API format
    - Ensure each message has required fields
    - Merge consecutive assistant messages with tool_calls
    """
    normalized_messages = []
    pending_tool_calls = []  # Collect tool_calls to merge into single assistant message

    for msg in messages:
        # Skip null/None values
        if msg is None:
            continue

        # Skip non-dict values
        if not isinstance(msg, dict):
            log.warning(f"[Gateway] Skipping non-dict message: {type(msg)}")
            continue

        # Try to convert Responses API format to Chat Completions format
        if "role" not in msg and "type" in msg:
            converted = convert_responses_api_message(msg)
            if converted is None:
                log.warning(f"[Gateway] Could not convert message: {list(msg.keys())}")
                continue
            msg = converted

        # Ensure message has 'role' field after conversion
        if "role" not in msg:
            log.warning(f"[Gateway] Skipping message without role after conversion: {list(msg.keys())}")
            continue

        # Handle tool_calls merging - collect consecutive function_call messages
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            # If we have pending tool_calls, merge them
            pending_tool_calls.extend(msg.get("tool_calls", []))
            # Don't add yet - wait for non-tool_call message or end
            continue

        # If we have pending tool_calls and hit a non-assistant-with-tool_calls message
        if pending_tool_calls:
            # Flush pending tool_calls as a single assistant message
            merged_assistant = {
                "role": "assistant",
                "content": None,
                "tool_calls": pending_tool_calls
            }
            normalized_messages.append(merged_assistant)
            log.info(f"[Gateway] Merged {len(pending_tool_calls)} tool_calls into single assistant message")
            pending_tool_calls = []

        # Ensure message has 'content' field (can be None for tool calls)
        # But the message object itself must exist
        normalized_messages.append(msg)

    # Flush any remaining pending tool_calls
    if pending_tool_calls:
        merged_assistant = {
            "role": "assistant",
            "content": None,
            "tool_calls": pending_tool_calls
        }
        normalized_messages.append(merged_assistant)
        log.info(f"[Gateway] Merged {len(pending_tool_calls)} remaining tool_calls into single assistant message")

    return normalized_messages'''

if 'convert_responses_api_message(msg)' in content:
    print("[SKIP] normalize_messages already updated with conversion logic")
elif old_normalize_messages in content:
    content = content.replace(old_normalize_messages, new_normalize_messages)
    print("[OK] Updated normalize_messages with Responses API conversion")
else:
    print("[WARN] Could not find exact normalize_messages pattern")
    # Try to find partial match
    if 'Skipping message without role' in content:
        print("[INFO] Found partial match - manual update may be needed")


# Write back
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n[SUCCESS] Responses API format conversion added!")
print("\nThis fix converts:")
print("  - {type: 'function_call', ...} -> {role: 'assistant', tool_calls: [...]}")
print("  - {type: 'function_call_output', ...} -> {role: 'tool', tool_call_id: ..., content: ...}")
print("\nCursor's tool execution results will now be properly passed to the model.")
