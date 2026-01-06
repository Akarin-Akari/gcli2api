"""
测试消息格式转换功能

验证：
1. Anthropic 格式 tool_use/tool_result 转换
2. 未知格式消息的兼容处理
3. Cursor planning/debug 模式消息转换
"""

import sys
import os
import unittest
import json

# 设置 UTF-8 编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 确保可以导入父目录的模块
gcli2api_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
gcli2api_src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, gcli2api_path)
sys.path.insert(0, gcli2api_src_path)

from unified_gateway_router import convert_responses_api_message, normalize_messages


class TestConvertResponsesApiMessage(unittest.TestCase):
    """测试 convert_responses_api_message 函数"""

    def test_standard_message_with_role(self):
        """测试已有 role 的标准消息"""
        msg = {"role": "user", "content": "Hello"}
        result = convert_responses_api_message(msg)
        self.assertEqual(result, msg)
        print("  ✅ Standard message with role: PASS")

    def test_type_message(self):
        """测试 type: message 格式"""
        msg = {"type": "message", "role": "user", "content": "Hello"}
        result = convert_responses_api_message(msg)
        self.assertEqual(result["role"], "user")
        self.assertEqual(result["content"], "Hello")
        print("  ✅ Type: message format: PASS")

    def test_type_function_call(self):
        """测试 type: function_call 格式 (OpenAI Responses API)"""
        msg = {
            "type": "function_call",
            "call_id": "call_123",
            "name": "test_function",
            "arguments": '{"arg1": "value1"}'
        }
        result = convert_responses_api_message(msg)
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["id"], "call_123")
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "test_function")
        print("  ✅ Type: function_call format: PASS")

    def test_type_function_call_output(self):
        """测试 type: function_call_output 格式 (OpenAI Responses API)"""
        msg = {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "Function result"
        }
        result = convert_responses_api_message(msg)
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "call_123")
        self.assertEqual(result["content"], "Function result")
        print("  ✅ Type: function_call_output format: PASS")

    def test_type_tool_use_anthropic(self):
        """测试 type: tool_use 格式 (Anthropic 格式 - Cursor planning/debug 模式)"""
        msg = {
            "type": "tool_use",
            "id": "toolu_123",
            "name": "mcp_desktop-commander_read_file",
            "input": {"path": "/test/file.txt"}
        }
        result = convert_responses_api_message(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["id"], "toolu_123")
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "mcp_desktop-commander_read_file")
        # 验证 arguments 是 JSON 字符串
        args = json.loads(result["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(args["path"], "/test/file.txt")
        print("  ✅ Type: tool_use (Anthropic format): PASS")

    def test_type_tool_result_anthropic(self):
        """测试 type: tool_result 格式 (Anthropic 格式 - Cursor planning/debug 模式)"""
        msg = {
            "type": "tool_result",
            "tool_use_id": "toolu_123",
            "content": "File content here"
        }
        result = convert_responses_api_message(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "toolu_123")
        self.assertEqual(result["content"], "File content here")
        print("  ✅ Type: tool_result (Anthropic format): PASS")

    def test_untyped_tool_call_with_call_id_and_name(self):
        """测试无 type 但有 call_id 和 name 的工具调用"""
        msg = {
            "type": "unknown_type",  # 未知类型
            "call_id": "call_456",
            "name": "grep",
            "arguments": '{"pattern": "test"}'
        }
        result = convert_responses_api_message(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["tool_calls"][0]["id"], "call_456")
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "grep")
        print("  ✅ Untyped tool call with call_id and name: PASS")

    def test_untyped_tool_result_with_output_and_call_id(self):
        """测试无 type 但有 output 和 call_id 的工具结果"""
        msg = {
            "type": "unknown_type",  # 未知类型
            "call_id": "call_456",
            "output": "grep result"
        }
        result = convert_responses_api_message(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "call_456")
        self.assertEqual(result["content"], "grep result")
        print("  ✅ Untyped tool result with output and call_id: PASS")

    def test_tool_use_with_dict_input(self):
        """测试 tool_use 输入为字典时的转换"""
        msg = {
            "type": "tool_use",
            "id": "toolu_789",
            "name": "run_terminal_cmd",
            "input": {"command": "ls -la", "is_background": False}
        }
        result = convert_responses_api_message(msg)
        self.assertIsNotNone(result)
        args = json.loads(result["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(args["command"], "ls -la")
        self.assertEqual(args["is_background"], False)
        print("  ✅ Tool use with dict input: PASS")

    def test_tool_result_with_content_array(self):
        """测试 tool_result 内容为数组时的转换"""
        msg = {
            "type": "tool_result",
            "tool_use_id": "toolu_789",
            "content": [
                {"type": "text", "text": "Line 1"},
                {"type": "text", "text": "Line 2"}
            ]
        }
        result = convert_responses_api_message(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "tool")
        self.assertIn("Line 1", result["content"])
        self.assertIn("Line 2", result["content"])
        print("  ✅ Tool result with content array: PASS")


class TestNormalizeMessages(unittest.TestCase):
    """测试 normalize_messages 函数"""

    def test_mixed_format_messages(self):
        """测试混合格式消息的规范化"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"type": "tool_use", "id": "toolu_1", "name": "grep", "input": {"pattern": "test"}},
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Found 5 matches"},
            {"role": "assistant", "content": "I found 5 matches."}
        ]
        result = normalize_messages(messages)
        
        # 验证所有消息都被正确转换
        self.assertTrue(all("role" in msg for msg in result))
        print("  ✅ Mixed format messages normalization: PASS")

    def test_cursor_planning_mode_messages(self):
        """测试 Cursor planning 模式的消息格式"""
        # 模拟 Cursor planning 模式发送的消息
        messages = [
            {"role": "user", "content": "Plan the implementation"},
            {
                "type": "tool_use",
                "id": "toolu_plan_1",
                "name": "mcp_Sequential_thinking_sequentialthinking",
                "input": {"thought": "First, I need to analyze..."}
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu_plan_1",
                "content": "Thinking recorded"
            }
        ]
        result = normalize_messages(messages)
        
        # 验证 MCP 工具调用被正确转换
        tool_call_msgs = [m for m in result if m.get("role") == "assistant" and m.get("tool_calls")]
        self.assertTrue(len(tool_call_msgs) > 0)
        print("  ✅ Cursor planning mode messages: PASS")

    def test_skip_null_messages(self):
        """测试跳过 null 消息"""
        messages = [
            {"role": "user", "content": "Hello"},
            None,
            {"role": "assistant", "content": "Hi"}
        ]
        result = normalize_messages(messages)
        self.assertEqual(len(result), 2)
        print("  ✅ Skip null messages: PASS")


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("Testing Message Conversion (Cursor Planning/Debug Mode Fix)")
    print("=" * 60 + "\n")

    print("--- TestConvertResponsesApiMessage ---")
    suite1 = unittest.TestLoader().loadTestsFromTestCase(TestConvertResponsesApiMessage)
    unittest.TextTestRunner(verbosity=0).run(suite1)

    print("\n--- TestNormalizeMessages ---")
    suite2 = unittest.TestLoader().loadTestsFromTestCase(TestNormalizeMessages)
    unittest.TextTestRunner(verbosity=0).run(suite2)

    print("\n" + "=" * 60)
    print("All message conversion tests completed!")
    print("=" * 60)

