#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Add debug logging to see what Cursor sends to the gateway
"""

file_path = 'src/unified_gateway_router.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the chat_completions function and add debug logging
old_code = '''    log.info(f"[Gateway] Received chat request at {request.url.path}")
    try:
        raw_body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # Normalize request body to standard OpenAI format
    body = normalize_request_body(raw_body)'''

new_code = '''    log.info(f"[Gateway] Received chat request at {request.url.path}")
    try:
        raw_body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # DEBUG: Log incoming messages to diagnose tool call issues
    raw_messages = raw_body.get("messages", [])
    log.info(f"[Gateway DEBUG] Incoming messages count: {len(raw_messages)}")
    for i, msg in enumerate(raw_messages[-5:]):  # Only log last 5 messages
        if isinstance(msg, dict):
            role = msg.get("role", "unknown")
            has_content = "content" in msg and msg["content"] is not None
            has_tool_calls = "tool_calls" in msg
            tool_call_id = msg.get("tool_call_id", None)
            log.info(f"[Gateway DEBUG] Message {i}: role={role}, has_content={has_content}, has_tool_calls={has_tool_calls}, tool_call_id={tool_call_id}")
            if role == "tool":
                log.info(f"[Gateway DEBUG] Tool result message: {json.dumps(msg, ensure_ascii=False)[:500]}")
            if role == "assistant" and has_tool_calls:
                log.info(f"[Gateway DEBUG] Assistant tool_calls: {json.dumps(msg.get('tool_calls', []), ensure_ascii=False)[:500]}")

    # Normalize request body to standard OpenAI format
    body = normalize_request_body(raw_body)'''

if '[Gateway DEBUG]' in content:
    print("[SKIP] Debug logging already added")
elif old_code in content:
    content = content.replace(old_code, new_code)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("[OK] Added debug logging to chat_completions")
else:
    print("[FAIL] Could not find the target code pattern")
    print("Trying to find similar pattern...")
    if "Received chat request" in content:
        print("[INFO] Found 'Received chat request' in file")
    else:
        print("[WARN] Pattern not found")
