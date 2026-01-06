#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Add model routing by prompt markers to unified_gateway_router.py
Supports:
  - [use:model-name] - High priority
  - @model-name - Low priority
"""

import re

file_path = 'src/unified_gateway_router.py'

# Read file with UTF-8 encoding
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add 're' import if not exists
if 'import re' not in content:
    content = content.replace(
        'import json\nimport time',
        'import json\nimport re\nimport time'
    )
    print("[OK] Added 'import re'")
else:
    print("[SKIP] 'import re' already exists")

# 2. Add model routing function after RETRY_CONFIG
model_routing_code = '''

# ==================== Prompt Model Routing ====================

# Supported model names for routing
ROUTABLE_MODELS = {
    # GPT models -> Copilot
    "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-5", "gpt-5.1", "gpt-5.2",
    "o1", "o1-mini", "o1-pro", "o3", "o3-mini",
    # Claude models -> Antigravity
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-3.5-opus", "claude-3.5-sonnet", "claude-3.5-haiku",
    "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
    "claude-sonnet-4.5", "claude-opus-4.5", "claude-haiku-4.5",
    # Gemini models -> Antigravity
    "gemini-pro", "gemini-ultra",
    "gemini-2.5-pro", "gemini-2.5-flash",
    "gemini-3-pro", "gemini-3-pro-preview",
}

# Regex patterns for model markers
# Pattern 1: [use:model-name] - High priority
USE_PATTERN = re.compile(r'\\[use:([a-zA-Z0-9._-]+)\\]', re.IGNORECASE)
# Pattern 2: @model-name - Low priority (at start of message or after whitespace)
AT_PATTERN = re.compile(r'(?:^|\\s)@([a-zA-Z0-9._-]+)(?=\\s|$)', re.IGNORECASE)


def extract_model_from_prompt(messages: list) -> tuple:
    """
    Extract model name from prompt markers in messages.

    Priority:
    1. [use:model-name] - Highest priority
    2. @model-name - Lower priority

    Args:
        messages: List of message dicts with 'role' and 'content'

    Returns:
        Tuple of (extracted_model_name or None, cleaned_messages)
    """
    if not messages:
        return None, messages

    extracted_model = None
    cleaned_messages = []

    for msg in messages:
        if not isinstance(msg, dict):
            cleaned_messages.append(msg)
            continue

        content = msg.get("content", "")

        # Handle different content types
        if isinstance(content, list):
            # Multi-modal content (text + images)
            new_content = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    model, cleaned_text = _extract_and_clean(text, extracted_model)
                    if model:
                        extracted_model = model
                    new_content.append({**item, "text": cleaned_text})
                else:
                    new_content.append(item)
            cleaned_messages.append({**msg, "content": new_content})
        elif isinstance(content, str):
            model, cleaned_content = _extract_and_clean(content, extracted_model)
            if model:
                extracted_model = model
            cleaned_messages.append({**msg, "content": cleaned_content})
        else:
            cleaned_messages.append(msg)

    if extracted_model:
        log.info(f"[Gateway] Extracted model from prompt: {extracted_model}")

    return extracted_model, cleaned_messages


def _extract_and_clean(text: str, current_model: str = None) -> tuple:
    """
    Extract model marker from text and return cleaned text.

    Args:
        text: The text to search
        current_model: Currently extracted model (for priority)

    Returns:
        Tuple of (model_name or None, cleaned_text)
    """
    extracted_model = current_model
    cleaned_text = text

    # Priority 1: [use:model-name]
    use_match = USE_PATTERN.search(text)
    if use_match:
        model_name = use_match.group(1).lower()
        if model_name in ROUTABLE_MODELS or _fuzzy_match_model(model_name):
            extracted_model = model_name
            # Remove the marker from text
            cleaned_text = USE_PATTERN.sub('', cleaned_text).strip()

    # Priority 2: @model-name (only if no [use:] found)
    if not use_match:
        at_match = AT_PATTERN.search(text)
        if at_match:
            model_name = at_match.group(1).lower()
            if model_name in ROUTABLE_MODELS or _fuzzy_match_model(model_name):
                extracted_model = model_name
                # Remove the marker from text
                cleaned_text = AT_PATTERN.sub(' ', cleaned_text).strip()

    return extracted_model, cleaned_text


def _fuzzy_match_model(model_name: str) -> bool:
    """
    Fuzzy match model name against known patterns.
    Allows variations like 'gpt4o' -> 'gpt-4o', 'claude35' -> 'claude-3.5'
    """
    # Normalize: remove dashes and dots for comparison
    normalized = model_name.replace('-', '').replace('.', '').replace('_', '')

    for known_model in ROUTABLE_MODELS:
        known_normalized = known_model.replace('-', '').replace('.', '').replace('_', '')
        if normalized == known_normalized:
            return True

    # Check prefixes for model families
    model_prefixes = ['gpt', 'claude', 'gemini', 'o1', 'o3']
    for prefix in model_prefixes:
        if normalized.startswith(prefix):
            return True

    return False

'''

# Check if already added
if 'extract_model_from_prompt' in content:
    print("[SKIP] Model routing functions already exist")
else:
    # Find the position after RETRY_CONFIG
    insert_marker = 'RETRY_CONFIG = {\n    "max_retries": 3,'
    if insert_marker in content:
        # Find the end of RETRY_CONFIG block
        retry_end = content.find('}', content.find(insert_marker))
        if retry_end != -1:
            # Find the next newline after the closing brace
            next_newline = content.find('\n', retry_end)
            if next_newline != -1:
                content = content[:next_newline+1] + model_routing_code + content[next_newline+1:]
                print("[OK] Added model routing functions")
    else:
        print("[FAIL] Could not find RETRY_CONFIG marker")

# 3. Update normalize_request_body to use model extraction
old_normalize_end = '''    log.info(f"[Gateway] Normalized request: model={normalized['model']}, messages_count={len(normalized['messages'])}, stream={normalized.get('stream')}, tools_count={len(normalized.get('tools', []))}")

    return normalized'''

new_normalize_end = '''    # Extract model from prompt markers (if any)
    prompt_model, cleaned_messages = extract_model_from_prompt(normalized["messages"])
    if prompt_model:
        normalized["model"] = prompt_model
        normalized["messages"] = cleaned_messages
        log.info(f"[Gateway] Model overridden by prompt marker: {prompt_model}")

    log.info(f"[Gateway] Normalized request: model={normalized['model']}, messages_count={len(normalized['messages'])}, stream={normalized.get('stream')}, tools_count={len(normalized.get('tools', []))}")

    return normalized'''

if 'prompt_model, cleaned_messages = extract_model_from_prompt' in content:
    print("[SKIP] normalize_request_body already updated")
elif old_normalize_end in content:
    content = content.replace(old_normalize_end, new_normalize_end)
    print("[OK] Updated normalize_request_body to use model extraction")
else:
    print("[WARN] Could not find normalize_request_body end marker, trying alternative...")
    # Try alternative pattern
    alt_pattern = 'log.info(f"[Gateway] Normalized request: model={normalized'
    if alt_pattern in content:
        print("[INFO] Found alternative pattern, manual update may be needed")

# Write back with UTF-8 encoding
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n[SUCCESS] Model routing feature added!")
print("\nUsage examples:")
print("  [use:gpt-4o] Help me write code")
print("  @gpt-5.2 Analyze this problem")
print("  [use:claude-sonnet-4.5] Explain this concept")
