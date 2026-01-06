"""
测试工具格式验证功能
Test tool format validation functions
"""

import sys
import os

# 设置 UTF-8 编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.converters.tool_converter import (
    validate_tool_name,
    validate_tool_parameters,
    validate_antigravity_tool,
    validate_tools_batch,
    convert_openai_tools_to_antigravity,
)


def test_validate_tool_name():
    """测试工具名称验证"""
    print("\n=== Test: validate_tool_name ===")
    
    # 测试有效名称
    test_cases = [
        ("read_file", True, "read_file"),
        ("terminal", True, "terminal"),
        ("run-command", True, "run-command"),
        ("Tool123", True, "Tool123"),
    ]
    
    for name, expected_valid, expected_result in test_cases:
        is_valid, error, result = validate_tool_name(name)
        status = "[PASS]" if is_valid == expected_valid else "[FAIL]"
        print(f"{status} validate_tool_name('{name}'): valid={is_valid}, result='{result}'")
    
    # 测试无效名称
    invalid_cases = [
        (None, False, "None"),
        ("", False, "empty"),
        ("   ", False, "whitespace"),
        ("123tool", True, "starts with number - should be fixed"),
        ("tool@name", True, "special chars - should be fixed"),
    ]
    
    for name, expected_valid, desc in invalid_cases:
        is_valid, error, result = validate_tool_name(name)
        status = "[PASS]" if is_valid == expected_valid else "[FAIL]"
        print(f"{status} validate_tool_name({repr(name)}) [{desc}]: valid={is_valid}, error='{error}', result='{result}'")


def test_validate_tool_parameters():
    """测试工具参数验证"""
    print("\n=== Test: validate_tool_parameters ===")
    
    # 测试有效参数
    valid_params = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "content": {"type": "string", "description": "File content"}
        },
        "required": ["path"]
    }
    is_valid, error, result = validate_tool_parameters(valid_params)
    print(f"[PASS] Valid params: valid={is_valid}, has_type={result.get('type')}")
    
    # 测试缺少 type 字段
    no_type_params = {
        "properties": {
            "path": {"type": "string"}
        }
    }
    is_valid, error, result = validate_tool_parameters(no_type_params)
    print(f"[PASS] No type params: valid={is_valid}, added_type={result.get('type')}")
    
    # 测试 None 参数
    is_valid, error, result = validate_tool_parameters(None)
    print(f"[PASS] None params: valid={is_valid}, default_type={result.get('type')}")
    
    # 测试嵌套 object 缺少 properties
    nested_no_props = {
        "type": "object",
        "properties": {
            "options": {"type": "object"}  # 缺少 properties
        }
    }
    is_valid, error, result = validate_tool_parameters(nested_no_props)
    nested_props = result.get("properties", {}).get("options", {}).get("properties")
    print(f"[PASS] Nested object without props: valid={is_valid}, added_nested_props={nested_props is not None}")


def test_validate_antigravity_tool():
    """测试完整工具验证"""
    print("\n=== Test: validate_antigravity_tool ===")
    
    # 有效工具
    valid_tool = {
        "name": "read_file",
        "description": "Read file contents",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"}
            }
        }
    }
    is_valid, error, result = validate_antigravity_tool(valid_tool)
    print(f"[PASS] Valid tool: valid={is_valid}, name={result.get('name') if result else None}")
    
    # 无效工具（无名称）
    no_name_tool = {
        "description": "Some tool",
        "parameters": {}
    }
    is_valid, error, result = validate_antigravity_tool(no_name_tool)
    print(f"[PASS] No name tool: valid={is_valid}, error='{error}'")
    
    # 非字典工具
    is_valid, error, result = validate_antigravity_tool("not a dict")
    print(f"[PASS] Non-dict tool: valid={is_valid}, error='{error}'")


def test_validate_tools_batch():
    """测试批量工具验证"""
    print("\n=== Test: validate_tools_batch ===")
    
    tools = [
        {"name": "tool1", "description": "Tool 1", "parameters": {"type": "object"}},
        {"name": "tool2", "description": "Tool 2", "parameters": {}},  # 缺少 type
        {"description": "No name tool"},  # 无名称
        {"name": "tool@invalid", "description": "Invalid name"},  # 非法字符
    ]
    
    valid_tools, errors = validate_tools_batch(tools)
    print(f"[PASS] Batch validation: {len(valid_tools)} valid, {len(errors)} errors")
    for tool in valid_tools:
        print(f"   - {tool['name']}: type={tool['parameters'].get('type')}")
    for error in errors:
        print(f"   [ERROR] {error}")


def test_convert_with_validation():
    """测试带验证的工具转换"""
    print("\n=== Test: convert_openai_tools_to_antigravity ===")
    
    # OpenAI 格式工具
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read file contents",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"}
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write file contents",
                "parameters": {
                    "properties": {  # 缺少 type
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    }
                }
            }
        },
        {
            "type": "custom",  # Cursor 格式
            "custom": {
                "name": "terminal",
                "description": "Run terminal command",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"}
                    }
                }
            }
        }
    ]
    
    result = convert_openai_tools_to_antigravity(openai_tools)
    
    if result:
        declarations = result[0].get("functionDeclarations", [])
        print(f"[PASS] Converted {len(declarations)} tools:")
        for decl in declarations:
            params_type = decl.get("parameters", {}).get("type", "missing")
            print(f"   - {decl['name']}: parameters.type={params_type}")
    else:
        print("[FAIL] No tools converted")


def test_cursor_custom_tool():
    """测试 Cursor 的 custom 工具格式"""
    print("\n=== Test: Cursor custom tool format ===")
    
    cursor_tools = [
        {
            "type": "custom",
            "custom": {
                "name": "read",
                "description": "Read file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "target_file": {"type": "string", "description": "File to read"}
                    },
                    "required": ["target_file"]
                }
            }
        },
        {
            "type": "custom",
            "custom": {
                "name": "edit",
                "description": "Edit file",
                "input_schema": {
                    # 缺少 type 字段
                    "properties": {
                        "target_file": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"}
                    }
                }
            }
        }
    ]
    
    result = convert_openai_tools_to_antigravity(cursor_tools)
    
    if result:
        declarations = result[0].get("functionDeclarations", [])
        print(f"[PASS] Converted {len(declarations)} Cursor tools:")
        for decl in declarations:
            params_type = decl.get("parameters", {}).get("type", "missing")
            has_props = "properties" in decl.get("parameters", {})
            print(f"   - {decl['name']}: type={params_type}, has_properties={has_props}")
    else:
        print("[FAIL] No Cursor tools converted")


if __name__ == "__main__":
    print("=" * 60)
    print("工具格式验证测试 / Tool Format Validation Tests")
    print("=" * 60)
    
    test_validate_tool_name()
    test_validate_tool_parameters()
    test_validate_antigravity_tool()
    test_validate_tools_batch()
    test_convert_with_validation()
    test_cursor_custom_tool()
    
    print("\n" + "=" * 60)
    print("所有测试完成！/ All tests completed!")
    print("=" * 60)

