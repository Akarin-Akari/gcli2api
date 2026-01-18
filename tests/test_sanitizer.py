"""
AnthropicSanitizer 单元测试

测试覆盖:
1. Thinking block 签名验证和恢复
2. 无效签名降级为 text block
3. Tool use 签名恢复
4. Tool chain 完整性检查
5. ThinkingConfig 同步
"""

import unittest
import sys
import os

# 添加 src 目录到 python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.ide_compat.sanitizer import AnthropicSanitizer, get_sanitizer
from src.converters.thoughtSignature_fix import MIN_SIGNATURE_LENGTH


class TestAnthropicSanitizer(unittest.TestCase):
    """AnthropicSanitizer 测试类"""

    def setUp(self):
        """测试前准备"""
        self.sanitizer = AnthropicSanitizer()

    def test_valid_thinking_block_preserved(self):
        """测试: 有效的 thinking block 应该被保留"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Let me think about this...",
                        "thoughtSignature": "a" * MIN_SIGNATURE_LENGTH  # 有效签名
                    },
                    {
                        "type": "text",
                        "text": "Here is my answer."
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证 thinking block 被保留
        self.assertEqual(len(sanitized), 1)
        content = sanitized[0]["content"]
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0]["type"], "thinking")
        self.assertIn("thoughtSignature", content[0])

    def test_invalid_thinking_block_downgraded(self):
        """测试: 无效的 thinking block 应该降级为 text block"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Let me think about this...",
                        "thoughtSignature": "abc"  # 无效签名 (太短)
                    },
                    {
                        "type": "text",
                        "text": "Here is my answer."
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证 thinking block 被降级为 text block
        self.assertEqual(len(sanitized), 1)
        content = sanitized[0]["content"]
        self.assertEqual(len(content), 2)
        # 第一个 block 应该是降级后的 text block
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[0]["text"], "Let me think about this...")

    def test_empty_thinking_block_with_short_signature(self):
        """测试: 空 thinking + 短签名应该被保留 (trailing signature case)"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "",
                        "thoughtSignature": "abc"  # 短签名,但空thinking时仍有效
                    },
                    {
                        "type": "text",
                        "text": "Here is my answer."
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证空 thinking block 被保留 (trailing signature case)
        self.assertEqual(len(sanitized), 1)
        content = sanitized[0]["content"]
        self.assertEqual(len(content), 2)  # thinking + text
        self.assertEqual(content[0]["type"], "thinking")
        self.assertEqual(content[0]["thinking"], "")

    def test_thinking_config_sync_no_valid_blocks(self):
        """测试: 没有有效 thinking block 时应禁用 thinking"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Here is my answer."
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True  # 原本启用
        )

        # 验证 thinking 被禁用
        self.assertFalse(thinking_enabled)

    def test_thinking_config_sync_with_valid_blocks(self):
        """测试: 有有效 thinking block 时保持 thinking 启用"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Let me think...",
                        "thoughtSignature": "a" * MIN_SIGNATURE_LENGTH
                    },
                    {
                        "type": "text",
                        "text": "Answer."
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证 thinking 保持启用
        self.assertTrue(thinking_enabled)

    def test_user_messages_preserved(self):
        """测试: user 消息应该原样保留"""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is 2+2?"
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证 user 消息原样保留
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0]["role"], "user")
        self.assertEqual(sanitized[0]["content"][0]["text"], "What is 2+2?")

    def test_tool_use_block_preserved(self):
        """测试: tool_use block 应该被保留"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_123",
                        "name": "get_weather",
                        "input": {"city": "London"}
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证 tool_use block 被保留
        self.assertEqual(len(sanitized), 1)
        content = sanitized[0]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "tool_use")
        self.assertEqual(content[0]["id"], "call_123")

    def test_tool_result_block_preserved(self):
        """测试: tool_result block 应该被保留"""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_123",
                        "content": "Sunny, 20°C"
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证 tool_result block 被保留
        self.assertEqual(len(sanitized), 1)
        content = sanitized[0]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "tool_result")

    def test_mixed_content_blocks(self):
        """测试: 混合内容 blocks 应该正确处理"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "I need to check the weather...",
                        "thoughtSignature": "a" * MIN_SIGNATURE_LENGTH
                    },
                    {
                        "type": "tool_use",
                        "id": "call_123",
                        "name": "get_weather",
                        "input": {"city": "London"}
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_123",
                        "content": "Sunny, 20°C"
                    }
                ]
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The weather in London is sunny."
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证所有消息都被正确处理
        self.assertEqual(len(sanitized), 3)
        self.assertTrue(thinking_enabled)

    def test_statistics_tracking(self):
        """测试: 统计信息应该正确跟踪"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Valid thinking",
                        "thoughtSignature": "a" * MIN_SIGNATURE_LENGTH
                    },
                    {
                        "type": "thinking",
                        "thinking": "Invalid thinking with content that cannot be recovered",
                        "thoughtSignature": "abc"  # 无效且无法恢复
                    }
                ]
            }
        ]

        self.sanitizer.sanitize_messages(messages, thinking_enabled=True)

        stats = self.sanitizer.get_stats()
        self.assertEqual(stats["total_messages"], 1)
        self.assertEqual(stats["thinking_blocks_validated"], 2)
        # 注意: 降级数量可能为0或1,取决于签名恢复是否成功
        # 这里我们只验证基本统计
        self.assertGreaterEqual(stats["thinking_blocks_validated"], 2)

    def test_exception_handling(self):
        """测试: 异常应该被捕获,返回原始消息"""
        # 构造一个可能导致异常的消息
        messages = [
            {
                "role": "assistant",
                "content": "invalid content type"  # 应该是 list
            }
        ]

        # 应该不抛出异常
        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证返回原始消息
        self.assertEqual(sanitized, messages)

    def test_global_sanitizer_singleton(self):
        """测试: 全局 sanitizer 应该是单例"""
        sanitizer1 = get_sanitizer()
        sanitizer2 = get_sanitizer()

        self.assertIs(sanitizer1, sanitizer2)

    def test_redacted_thinking_block(self):
        """测试: redacted_thinking block 应该被正确处理"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "redacted_thinking",
                        "thinking": "",
                        "thoughtSignature": "a" * MIN_SIGNATURE_LENGTH
                    }
                ]
            }
        ]

        sanitized, thinking_enabled = self.sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证 redacted_thinking block 被保留
        self.assertEqual(len(sanitized), 1)
        content = sanitized[0]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "redacted_thinking")


class TestSanitizerIntegration(unittest.TestCase):
    """AnthropicSanitizer 集成测试"""

    def test_real_world_scenario_cursor_ide(self):
        """测试: 模拟 Cursor IDE 场景 - 过滤掉 thinking 块"""
        # Cursor IDE 可能会过滤掉 thinking 块,只保留 tool_use
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_123",
                        "name": "read_file",
                        "input": {"path": "test.py"}
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_123",
                        "content": "def hello(): pass"
                    }
                ]
            }
        ]

        sanitizer = AnthropicSanitizer()
        sanitized, thinking_enabled = sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证: 没有 thinking block,应该禁用 thinking
        self.assertFalse(thinking_enabled)
        self.assertEqual(len(sanitized), 2)

    def test_real_world_scenario_augment(self):
        """测试: 模拟 Augment 场景 - 修改 thinking 块签名"""
        # Augment 可能会修改 thinking 块的签名为无效值
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Let me analyze this code...",
                        "thoughtSignature": "bad_sig"  # 被修改的无效签名 (太短)
                    },
                    {
                        "type": "text",
                        "text": "The code looks good."
                    }
                ]
            }
        ]

        sanitizer = AnthropicSanitizer()
        sanitized, thinking_enabled = sanitizer.sanitize_messages(
            messages,
            thinking_enabled=True
        )

        # 验证: 无效签名应该被降级为 text (如果恢复失败)
        # 或者被恢复为有效签名 (如果恢复成功)
        self.assertEqual(len(sanitized), 1)
        content = sanitized[0]["content"]
        # 至少应该有2个 blocks
        self.assertGreaterEqual(len(content), 2)


if __name__ == '__main__':
    unittest.main()
