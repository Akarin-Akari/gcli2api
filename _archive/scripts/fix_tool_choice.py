#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix tool_choice format for Copilot API compatibility.

Error: "got object, want string" or "missing property 'function'"

Copilot API expects tool_choice to be either:
1. A string: "auto", "none", "required"
2. An object: {"type": "function", "function": {"name": "function_name"}}

Cursor may send non-standard formats that need normalization.
"""

gateway_path = 'src/unified_gateway_router.py'

with open(gateway_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Add normalize_tool_choice function before normalize_request_body
normalize_tool_choice_func = '''
def normalize_tool_choice(tool_choice: Any) -> Any:
    """
    Normalize tool_choice to standard OpenAI format.

    Valid formats:
    1. String: "auto", "none", "required"
    2. Object: {"type": "function", "function": {"name": "func_name"}}

    Cursor may send non-standard formats like:
    - {"type": "auto"} -> should be just "auto"
    - {"type": "function", "name": "func"} -> missing nested function object
    """
    if tool_choice is None:
        return None

    # Already a valid string
    if isinstance(tool_choice, str):
        if tool_choice in ("auto", "none", "required"):
            return tool_choice
        # Unknown string, default to auto
        log.warning(f"[Gateway] Unknown tool_choice string: {tool_choice}, defaulting to 'auto'")
        return "auto"

    # Object format
    if isinstance(tool_choice, dict):
        tc_type = tool_choice.get("type", "")

        # Case 1: {"type": "auto"} or {"type": "none"} or {"type": "required"}
        # Should be converted to just the string
        if tc_type in ("auto", "none", "required"):
            if len(tool_choice) == 1:  # Only has "type" key
                return tc_type

        # Case 2: {"type": "function", ...}
        if tc_type == "function":
            # Check if it has proper "function" nested object
            if "function" in tool_choice and isinstance(tool_choice["function"], dict):
                func_obj = tool_choice["function"]
                if "name" in func_obj:
                    # Valid format
                    return {
                        "type": "function",
                        "function": {"name": func_obj["name"]}
                    }

            # Case 2b: {"type": "function", "name": "func_name"} - missing nested function
            if "name" in tool_choice:
                return {
                    "type": "function",
                    "function": {"name": tool_choice["name"]}
                }

            # Invalid function format, log and return auto
            log.warning(f"[Gateway] Invalid tool_choice function format: {tool_choice}, defaulting to 'auto'")
            return "auto"

        # Unknown type, default to auto
        log.warning(f"[Gateway] Unknown tool_choice type: {tc_type}, defaulting to 'auto'")
        return "auto"

    # Unknown format, default to auto
    log.warning(f"[Gateway] Unknown tool_choice format: {type(tool_choice)}, defaulting to 'auto'")
    return "auto"


'''

# Insert before normalize_request_body
insert_marker = 'def normalize_request_body(body: Dict[str, Any]) -> Dict[str, Any]:'
if 'def normalize_tool_choice' in content:
    print("[SKIP] normalize_tool_choice already exists")
elif insert_marker in content:
    insert_pos = content.find(insert_marker)
    content = content[:insert_pos] + normalize_tool_choice_func + content[insert_pos:]
    print("[OK] Added normalize_tool_choice function")
else:
    print("[FAIL] Could not find insertion point")


# Now update normalize_request_body to use normalize_tool_choice
old_tool_choice_handling = '''    for field in standard_fields:
        if field in body:
            normalized[field] = body[field]'''

new_tool_choice_handling = '''    for field in standard_fields:
        if field in body:
            # Special handling for tool_choice
            if field == "tool_choice":
                normalized[field] = normalize_tool_choice(body[field])
            else:
                normalized[field] = body[field]'''

if 'normalize_tool_choice(body[field])' in content:
    print("[SKIP] tool_choice normalization already in normalize_request_body")
elif old_tool_choice_handling in content:
    content = content.replace(old_tool_choice_handling, new_tool_choice_handling)
    print("[OK] Updated normalize_request_body to use normalize_tool_choice")
else:
    print("[WARN] Could not find tool_choice handling pattern")

# Write back
with open(gateway_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n[SUCCESS] tool_choice normalization added!")
print("\nThis fix handles:")
print('  - {"type": "auto"} -> "auto"')
print('  - {"type": "function", "name": "x"} -> {"type": "function", "function": {"name": "x"}}')
print('  - Invalid formats -> "auto" (with warning)')
