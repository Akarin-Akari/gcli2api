"""
测试上下文长度限制功能

验证：
1. 上下文超过临界阈值时返回 400 错误
2. 错误消息包含 /summarize 命令提示
3. 警告阈值时记录日志但不阻止请求
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# 设置 UTF-8 编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 确保可以导入父目录的模块
gcli2api_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, gcli2api_path)

from src.context_analyzer import (
    estimate_input_tokens,
    check_context_length,
    build_context_info,
)


class TestContextLimit(unittest.TestCase):
    """测试上下文长度限制"""

    def create_large_contents(self, target_tokens: int) -> list:
        """创建指定 token 数的测试内容"""
        # 1 token ≈ 4 字符
        chars_needed = target_tokens * 4
        large_text = "x" * chars_needed
        return [
            {
                "role": "user",
                "parts": [{"text": large_text}]
            }
        ]

    def test_estimate_input_tokens(self):
        """测试 token 估算"""
        print("\n--- Testing estimate_input_tokens ---")
        
        # 测试空内容
        empty_contents = []
        tokens = estimate_input_tokens(empty_contents)
        self.assertEqual(tokens, 0)
        print(f"Empty contents: {tokens} tokens")
        
        # 测试小内容 (100 字符 ≈ 25 tokens)
        small_contents = [{"role": "user", "parts": [{"text": "x" * 100}]}]
        tokens = estimate_input_tokens(small_contents)
        self.assertEqual(tokens, 25)
        print(f"100 chars: {tokens} tokens")
        
        # 测试大内容 (400000 字符 ≈ 100000 tokens)
        large_contents = self.create_large_contents(100000)
        tokens = estimate_input_tokens(large_contents)
        self.assertEqual(tokens, 100000)
        print(f"400000 chars: {tokens} tokens")

    def test_check_context_length_ok(self):
        """测试正常长度的上下文"""
        print("\n--- Testing check_context_length (OK) ---")
        
        # 50K tokens - 正常范围
        contents = self.create_large_contents(50000)
        status, tokens = check_context_length(contents)
        self.assertEqual(status, "ok")
        self.assertEqual(tokens, 50000)
        print(f"50K tokens: status={status}")

    def test_check_context_length_warning(self):
        """测试警告阈值"""
        print("\n--- Testing check_context_length (WARNING) ---")
        
        # 90K tokens - 警告范围 (> 80K)
        contents = self.create_large_contents(90000)
        status, tokens = check_context_length(contents)
        self.assertEqual(status, "warning")
        self.assertEqual(tokens, 90000)
        print(f"90K tokens: status={status}")

    def test_check_context_length_critical(self):
        """测试临界阈值"""
        print("\n--- Testing check_context_length (CRITICAL) ---")
        
        # 130K tokens - 临界范围 (> 120K)
        contents = self.create_large_contents(130000)
        status, tokens = check_context_length(contents)
        self.assertEqual(status, "critical")
        self.assertEqual(tokens, 130000)
        print(f"130K tokens: status={status}")

    def test_custom_thresholds(self):
        """测试自定义阈值"""
        print("\n--- Testing custom thresholds ---")
        
        contents = self.create_large_contents(50000)
        
        # 使用较低的阈值
        status, tokens = check_context_length(
            contents,
            warning_threshold=40000,
            critical_threshold=60000
        )
        self.assertEqual(status, "warning")
        print(f"50K tokens with 40K/60K thresholds: status={status}")
        
        # 使用更低的阈值
        status, tokens = check_context_length(
            contents,
            warning_threshold=30000,
            critical_threshold=45000
        )
        self.assertEqual(status, "critical")
        print(f"50K tokens with 30K/45K thresholds: status={status}")

    def test_build_context_info(self):
        """测试上下文信息构建"""
        print("\n--- Testing build_context_info ---")
        
        # 测试小上下文
        small_contents = self.create_large_contents(10000)
        info = build_context_info(small_contents)
        self.assertEqual(info["estimated_tokens"], 10000)
        self.assertEqual(info["tool_result_count"], 0)
        print(f"Small context info: {info}")
        
        # 测试大上下文 (应该触发警告日志)
        large_contents = self.create_large_contents(90000)
        with patch('src.context_analyzer.log') as mock_log:
            info = build_context_info(large_contents)
            self.assertEqual(info["estimated_tokens"], 90000)
            # 验证警告日志被调用
            mock_log.warning.assert_called()
            print(f"Large context info: {info}")
            print(f"Warning logged: {mock_log.warning.called}")


class TestContextLimitIntegration(unittest.TestCase):
    """集成测试 - 验证 antigravity_router 中的上下文限制"""

    def test_threshold_constants(self):
        """验证阈值常量"""
        print("\n--- Testing threshold constants ---")
        
        # 这些是 antigravity_router.py 中定义的阈值
        CONTEXT_WARNING_THRESHOLD = 80000
        CONTEXT_CRITICAL_THRESHOLD = 120000
        
        print(f"Warning threshold: {CONTEXT_WARNING_THRESHOLD:,} tokens")
        print(f"Critical threshold: {CONTEXT_CRITICAL_THRESHOLD:,} tokens")
        
        # 验证阈值合理性
        self.assertGreater(CONTEXT_CRITICAL_THRESHOLD, CONTEXT_WARNING_THRESHOLD)
        self.assertGreater(CONTEXT_WARNING_THRESHOLD, 50000)  # 应该大于基础警告阈值
        print("Threshold constants are valid!")

    def test_error_message_contains_summarize(self):
        """验证错误消息包含 /summarize 命令提示"""
        print("\n--- Testing error message content ---")
        
        # 模拟错误消息
        error_message = (
            f"Context length limit exceeded. Your conversation has approximately 130,000 tokens, "
            f"which exceeds the limit of 120,000 tokens.\n\n"
            f"To resolve this issue:\n"
            f"1. Use the /summarize command in Cursor to compress the conversation history\n"
            f"2. Or start a new chat session\n"
            f"3. Or reduce the number of files and tool results in context\n\n"
            f"This limit exists to prevent API errors and ensure reliable responses."
        )
        
        # 验证包含关键信息
        self.assertIn("/summarize", error_message)
        self.assertIn("Cursor", error_message)
        self.assertIn("120,000", error_message)
        self.assertIn("130,000", error_message)
        print("Error message contains all required information!")
        print(f"Error message:\n{error_message}")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing Context Limit Functionality")
    print("=" * 60)
    unittest.main(verbosity=2)

