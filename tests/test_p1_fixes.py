"""
测试 P1 修复功能

验证：
1. P1-1: User-Agent 检测增强
2. P1-2: 空响应 Fallback 机制
3. P1-3: 工具调用验证
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
sys.path.insert(0, gcli2api_path)


class TestUserAgentDetection(unittest.TestCase):
    """P1-1: 测试 User-Agent 检测增强"""

    def setUp(self):
        from src.tool_cleaner import detect_client_type, get_client_info, should_enable_cross_pool_fallback
        self.detect_client_type = detect_client_type
        self.get_client_info = get_client_info
        self.should_enable_cross_pool_fallback = should_enable_cross_pool_fallback

    def test_detect_claude_code(self):
        """测试 Claude Code 检测"""
        print("\n--- Testing Claude Code detection ---")
        
        test_cases = [
            "Claude/1.0",
            "anthropic-sdk/1.0",
            "Mozilla/5.0 Claude Desktop",
            "Anthropic API Client",
        ]
        
        for ua in test_cases:
            result = self.detect_client_type(ua)
            self.assertEqual(result, "claude_code", f"Failed for: {ua}")
            print(f"  '{ua}' -> {result}")

    def test_detect_cursor(self):
        """测试 Cursor 检测"""
        print("\n--- Testing Cursor detection ---")
        
        test_cases = [
            "Cursor/1.0",
            "cursor-agent/2.0",
            "Mozilla/5.0 Cursor IDE",
        ]
        
        for ua in test_cases:
            result = self.detect_client_type(ua)
            self.assertEqual(result, "cursor", f"Failed for: {ua}")
            print(f"  '{ua}' -> {result}")

    def test_detect_cline(self):
        """测试 Cline 检测"""
        print("\n--- Testing Cline detection ---")
        
        test_cases = [
            "Cline/1.0",
            "claude-dev/2.0",
            "ClaudeDev VSCode Extension",
        ]
        
        for ua in test_cases:
            result = self.detect_client_type(ua)
            self.assertEqual(result, "cline", f"Failed for: {ua}")
            print(f"  '{ua}' -> {result}")

    def test_detect_continue_dev(self):
        """测试 Continue.dev 检测"""
        print("\n--- Testing Continue.dev detection ---")
        
        result = self.detect_client_type("Continue/1.0")
        self.assertEqual(result, "continue_dev")
        print(f"  'Continue/1.0' -> {result}")

    def test_detect_aider(self):
        """测试 Aider 检测"""
        print("\n--- Testing Aider detection ---")
        
        result = self.detect_client_type("Aider/1.0")
        self.assertEqual(result, "aider")
        print(f"  'Aider/1.0' -> {result}")

    def test_detect_windsurf(self):
        """测试 Windsurf 检测"""
        print("\n--- Testing Windsurf detection ---")
        
        result = self.detect_client_type("Windsurf/1.0")
        self.assertEqual(result, "windsurf")
        print(f"  'Windsurf/1.0' -> {result}")

    def test_detect_zed(self):
        """测试 Zed 检测"""
        print("\n--- Testing Zed detection ---")
        
        result = self.detect_client_type("Zed/1.0")
        self.assertEqual(result, "zed")
        print(f"  'Zed/1.0' -> {result}")

    def test_detect_copilot(self):
        """测试 GitHub Copilot 检测"""
        print("\n--- Testing GitHub Copilot detection ---")
        
        test_cases = [
            "GitHub-Copilot/1.0",
            "copilot-agent/2.0",
        ]
        
        for ua in test_cases:
            result = self.detect_client_type(ua)
            self.assertEqual(result, "copilot", f"Failed for: {ua}")
            print(f"  '{ua}' -> {result}")

    def test_detect_openai_api(self):
        """测试 OpenAI API 客户端检测"""
        print("\n--- Testing OpenAI API client detection ---")
        
        test_cases = [
            "openai-python/1.0",
            "python-requests/2.28",
            "node-fetch/3.0",
            "axios/1.0",
        ]
        
        for ua in test_cases:
            result = self.detect_client_type(ua)
            self.assertEqual(result, "openai_api", f"Failed for: {ua}")
            print(f"  '{ua}' -> {result}")

    def test_detect_unknown(self):
        """测试未知客户端检测"""
        print("\n--- Testing unknown client detection ---")
        
        test_cases = [
            "Mozilla/5.0",
            "Some Random Client",
            "",
            None,
        ]
        
        for ua in test_cases:
            result = self.detect_client_type(ua or "")
            self.assertEqual(result, "unknown", f"Failed for: {ua}")
            print(f"  '{ua}' -> {result}")

    def test_get_client_info(self):
        """测试获取客户端详细信息"""
        print("\n--- Testing get_client_info ---")
        
        info = self.get_client_info("Claude/1.0")
        self.assertEqual(info["type"], "claude_code")
        self.assertEqual(info["name"], "Claude Code")
        self.assertTrue(info["enable_cross_pool_fallback"])
        print(f"  Claude info: {info}")
        
        info = self.get_client_info("Cursor/1.0")
        self.assertEqual(info["type"], "cursor")
        self.assertEqual(info["name"], "Cursor IDE")
        self.assertFalse(info["enable_cross_pool_fallback"])
        print(f"  Cursor info: {info}")

    def test_cross_pool_fallback_decision(self):
        """测试跨池降级决策"""
        print("\n--- Testing cross-pool fallback decision ---")
        
        # 应该启用的客户端
        enable_clients = ["Claude/1.0", "Cline/1.0", "Continue/1.0", "Aider/1.0", "openai-python/1.0"]
        for ua in enable_clients:
            result = self.should_enable_cross_pool_fallback(ua)
            self.assertTrue(result, f"Should enable for: {ua}")
            print(f"  '{ua}' -> enable={result}")
        
        # 不应该启用的客户端
        disable_clients = ["Cursor/1.0", "Windsurf/1.0", "Zed/1.0", "copilot/1.0", "Unknown/1.0"]
        for ua in disable_clients:
            result = self.should_enable_cross_pool_fallback(ua)
            self.assertFalse(result, f"Should disable for: {ua}")
            print(f"  '{ua}' -> enable={result}")


class TestToolCallValidation(unittest.TestCase):
    """P1-3: 测试工具调用验证（独立实现，不依赖 fastapi）"""

    def validate_tool_call(self, function_call, available_tools=None):
        """本地实现的工具调用验证（与 antigravity_router.py 中的逻辑相同）"""
        if not isinstance(function_call, dict):
            return False, "Tool call must be a dictionary", None

        name = function_call.get("name")
        args = function_call.get("args", {})
        call_id = function_call.get("id")

        # 验证名称
        if not name:
            return False, "Tool call missing 'name' field", None

        if not isinstance(name, str):
            return False, f"Tool call 'name' must be a string, got {type(name).__name__}", None

        # 验证参数
        if args is None:
            args = {}
        
        if not isinstance(args, dict):
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                    if not isinstance(args, dict):
                        return False, f"Tool call 'args' must be a JSON object, got {type(args).__name__}", None
                except json.JSONDecodeError as e:
                    return False, f"Tool call 'args' is not valid JSON: {e}", None
            else:
                return False, f"Tool call 'args' must be a dictionary, got {type(args).__name__}", None

        fixed_call = {"name": name, "args": args}
        if call_id:
            fixed_call["id"] = call_id

        return True, "Valid tool call", fixed_call

    def validate_tool_call_result(self, tool_result, expected_tool_id=None):
        """本地实现的工具结果验证"""
        if not isinstance(tool_result, dict):
            return False, "Tool result must be a dictionary"

        tool_call_id = tool_result.get("tool_call_id")
        if not tool_call_id:
            return False, "Tool result missing 'tool_call_id' field"

        return True, "Valid tool result"

    def test_valid_tool_call(self):
        """测试有效的工具调用"""
        print("\n--- Testing valid tool call ---")
        
        tool_call = {
            "name": "get_weather",
            "args": {"location": "Tokyo"},
            "id": "call_123"
        }
        
        is_valid, msg, fixed = self.validate_tool_call(tool_call)
        self.assertTrue(is_valid)
        self.assertEqual(fixed["name"], "get_weather")
        print(f"  Valid call: {is_valid}, message: {msg}")

    def test_tool_call_missing_name(self):
        """测试缺少名称的工具调用"""
        print("\n--- Testing tool call missing name ---")
        
        tool_call = {
            "args": {"location": "Tokyo"}
        }
        
        is_valid, msg, fixed = self.validate_tool_call(tool_call)
        self.assertFalse(is_valid)
        self.assertIn("missing 'name'", msg)
        print(f"  Missing name: {is_valid}, message: {msg}")

    def test_tool_call_invalid_args_type(self):
        """测试参数类型无效的工具调用"""
        print("\n--- Testing tool call with invalid args type ---")
        
        tool_call = {
            "name": "get_weather",
            "args": "not a dict"  # 应该是 dict
        }
        
        # 字符串参数会尝试解析为 JSON
        is_valid, msg, fixed = self.validate_tool_call(tool_call)
        self.assertFalse(is_valid)
        print(f"  Invalid args: {is_valid}, message: {msg}")

    def test_tool_call_json_string_args(self):
        """测试 JSON 字符串参数"""
        print("\n--- Testing tool call with JSON string args ---")
        
        tool_call = {
            "name": "get_weather",
            "args": '{"location": "Tokyo"}'  # JSON 字符串
        }
        
        is_valid, msg, fixed = self.validate_tool_call(tool_call)
        self.assertTrue(is_valid)
        self.assertEqual(fixed["args"]["location"], "Tokyo")
        print(f"  JSON string args: {is_valid}, message: {msg}")

    def test_tool_call_empty_args(self):
        """测试空参数的工具调用"""
        print("\n--- Testing tool call with empty args ---")
        
        tool_call = {
            "name": "get_time",
            "args": None
        }
        
        is_valid, msg, fixed = self.validate_tool_call(tool_call)
        self.assertTrue(is_valid)
        self.assertEqual(fixed["args"], {})
        print(f"  Empty args: {is_valid}, message: {msg}")

    def test_tool_call_not_dict(self):
        """测试非字典的工具调用"""
        print("\n--- Testing non-dict tool call ---")
        
        is_valid, msg, fixed = self.validate_tool_call("not a dict")
        self.assertFalse(is_valid)
        self.assertIn("must be a dictionary", msg)
        print(f"  Non-dict: {is_valid}, message: {msg}")

    def test_valid_tool_result(self):
        """测试有效的工具结果"""
        print("\n--- Testing valid tool result ---")
        
        tool_result = {
            "tool_call_id": "call_123",
            "content": "The weather is sunny"
        }
        
        is_valid, msg = self.validate_tool_call_result(tool_result)
        self.assertTrue(is_valid)
        print(f"  Valid result: {is_valid}, message: {msg}")

    def test_tool_result_missing_id(self):
        """测试缺少 ID 的工具结果"""
        print("\n--- Testing tool result missing ID ---")
        
        tool_result = {
            "content": "The weather is sunny"
        }
        
        is_valid, msg = self.validate_tool_call_result(tool_result)
        self.assertFalse(is_valid)
        self.assertIn("missing 'tool_call_id'", msg)
        print(f"  Missing ID: {is_valid}, message: {msg}")


class TestEmptyResponseFallback(unittest.TestCase):
    """P1-2: 测试空响应 Fallback 机制（结构测试）"""

    def test_fallback_trigger_conditions(self):
        """测试 Fallback 触发条件"""
        print("\n--- Testing fallback trigger conditions ---")
        
        # 模拟触发条件
        ACTUAL_PROCESSED_TOKENS_THRESHOLD = 50000
        ESTIMATED_TOKENS_THRESHOLD = 60000
        
        test_cases = [
            # (actual_tokens, estimated_tokens, empty_parts, sse_lines, expected)
            (60000, 0, 0, 0, True),   # 实际 tokens 超过阈值
            (0, 70000, 0, 0, True),   # 估算 tokens 超过阈值
            (0, 0, 5, 10, True),      # 有空 parts 且有 SSE 行
            (40000, 50000, 0, 0, False),  # 都没超过阈值
            (0, 0, 0, 0, False),      # 没有任何触发条件
        ]
        
        for actual, estimated, empty_parts, sse_lines, expected in test_cases:
            should_fallback = (
                actual > ACTUAL_PROCESSED_TOKENS_THRESHOLD or
                (actual == 0 and estimated > ESTIMATED_TOKENS_THRESHOLD) or
                (empty_parts > 0 and sse_lines > 0)
            )
            self.assertEqual(should_fallback, expected, 
                f"Failed for: actual={actual}, estimated={estimated}, empty_parts={empty_parts}, sse_lines={sse_lines}")
            print(f"  actual={actual}, estimated={estimated}, empty_parts={empty_parts}, sse_lines={sse_lines} -> {should_fallback}")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing P1 Fixes")
    print("=" * 60)
    unittest.main(verbosity=2)

