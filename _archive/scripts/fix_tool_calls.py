#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix tool_calls format for Cursor compatibility.
Problem: Streaming tool_calls missing 'index' field causes Cursor agent looping.
"""

# ==================== Fix 1: Add index to OpenAIToolCall model ====================

models_path = 'src/models.py'

with open(models_path, 'r', encoding='utf-8') as f:
    models_content = f.read()

# Add index field to OpenAIToolCall
old_tool_call_model = '''class OpenAIToolCall(BaseModel):
    id: str
    type: str = "function"
    function: OpenAIToolFunction'''

new_tool_call_model = '''class OpenAIToolCall(BaseModel):
    index: Optional[int] = None  # Required for streaming responses
    id: str
    type: str = "function"
    function: OpenAIToolFunction'''

if 'index: Optional[int]' in models_content:
    print("[SKIP] OpenAIToolCall already has index field")
elif old_tool_call_model in models_content:
    models_content = models_content.replace(old_tool_call_model, new_tool_call_model)
    with open(models_path, 'w', encoding='utf-8') as f:
        f.write(models_content)
    print("[OK] Added index field to OpenAIToolCall model")
else:
    print("[WARN] Could not find OpenAIToolCall model pattern")


# ==================== Fix 2: Update convert_to_openai_tool_call function ====================

router_path = 'src/antigravity_router.py'

with open(router_path, 'r', encoding='utf-8') as f:
    router_content = f.read()

# Update convert_to_openai_tool_call to accept index parameter
old_convert_func = '''def convert_to_openai_tool_call(function_call: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Antigravity functionCall 转换为 OpenAI tool_call，使用 OpenAIToolCall 模型
    """
    tool_call = OpenAIToolCall(
        id=function_call.get("id", f"call_{uuid.uuid4().hex[:24]}"),
        type="function",
        function=OpenAIToolFunction(
            name=function_call.get("name", ""),
            arguments=json.dumps(function_call.get("args", {}))
        )
    )
    return model_to_dict(tool_call)'''

new_convert_func = '''def convert_to_openai_tool_call(function_call: Dict[str, Any], index: int = None) -> Dict[str, Any]:
    """
    将 Antigravity functionCall 转换为 OpenAI tool_call，使用 OpenAIToolCall 模型

    Args:
        function_call: Antigravity 格式的函数调用
        index: 工具调用索引（流式响应必需）
    """
    tool_call = OpenAIToolCall(
        index=index,
        id=function_call.get("id", f"call_{uuid.uuid4().hex[:24]}"),
        type="function",
        function=OpenAIToolFunction(
            name=function_call.get("name", ""),
            arguments=json.dumps(function_call.get("args", {}))
        )
    )
    result = model_to_dict(tool_call)
    # Remove None values for cleaner output (non-streaming doesn't need index)
    if result.get("index") is None:
        del result["index"]
    return result'''

if 'def convert_to_openai_tool_call(function_call: Dict[str, Any], index: int' in router_content:
    print("[SKIP] convert_to_openai_tool_call already updated")
elif old_convert_func in router_content:
    router_content = router_content.replace(old_convert_func, new_convert_func)
    print("[OK] Updated convert_to_openai_tool_call function")
else:
    print("[WARN] Could not find convert_to_openai_tool_call pattern")


# ==================== Fix 3: Update streaming tool_calls with index ====================

# Fix the streaming part where tool_calls are collected
old_streaming_collect = '''                # 处理工具调用
                elif "functionCall" in part:
                    tool_call = convert_to_openai_tool_call(part["functionCall"])
                    state["tool_calls"].append(tool_call)'''

new_streaming_collect = '''                # 处理工具调用
                elif "functionCall" in part:
                    tool_index = len(state["tool_calls"])
                    tool_call = convert_to_openai_tool_call(part["functionCall"], index=tool_index)
                    state["tool_calls"].append(tool_call)'''

if 'tool_index = len(state["tool_calls"])' in router_content:
    print("[SKIP] Streaming tool_calls collection already fixed")
elif old_streaming_collect in router_content:
    router_content = router_content.replace(old_streaming_collect, new_streaming_collect)
    print("[OK] Fixed streaming tool_calls collection with index")
else:
    print("[WARN] Could not find streaming tool_calls collection pattern")


# ==================== Fix 4: Update non-streaming tool_calls with index ====================

old_nonstream_collect = '''        # 处理工具调用
        elif "functionCall" in part:
            tool_calls_list.append(convert_to_openai_tool_call(part["functionCall"]))'''

new_nonstream_collect = '''        # 处理工具调用
        elif "functionCall" in part:
            tool_index = len(tool_calls_list)
            tool_calls_list.append(convert_to_openai_tool_call(part["functionCall"], index=tool_index))'''

if 'tool_index = len(tool_calls_list)' in router_content:
    print("[SKIP] Non-streaming tool_calls collection already fixed")
elif old_nonstream_collect in router_content:
    router_content = router_content.replace(old_nonstream_collect, new_nonstream_collect)
    print("[OK] Fixed non-streaming tool_calls collection with index")
else:
    print("[WARN] Could not find non-streaming tool_calls collection pattern")


# Write back router file
with open(router_path, 'w', encoding='utf-8') as f:
    f.write(router_content)

print("\n[SUCCESS] Tool calls format fixed!")
print("\nThe fix adds 'index' field to each tool_call in streaming responses,")
print("which is required by OpenAI API spec and Cursor's parser.")
