"""
Tests for thoughtSignature_fix module
"""

import unittest
import sys
import os

# 添加 src 目录到 python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.converters.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature,
    has_valid_thoughtsignature,
    sanitize_thinking_block,
    remove_trailing_unsigned_thinking,
    filter_invalid_thinking_blocks,
    THOUGHT_SIGNATURE_SEPARATOR,
    SKIP_SIGNATURE_VALIDATOR
)

class TestThoughtSignatureFix(unittest.TestCase):

    def test_encode_tool_id_with_signature(self):
        """测试工具ID编码"""
        tool_id = "call_123"
        signature = "abc123"

        encoded = encode_tool_id_with_signature(tool_id, signature)
        self.assertEqual(encoded, f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}")

        # 无签名时返回原ID
        encoded_none = encode_tool_id_with_signature(tool_id, None)
        self.assertEqual(encoded_none, tool_id)

        # 占位符签名时返回原ID
        encoded_skip = encode_tool_id_with_signature(tool_id, SKIP_SIGNATURE_VALIDATOR)
        self.assertEqual(encoded_skip, tool_id)

    def test_decode_tool_id_and_signature(self):
        """测试工具ID解码"""
        tool_id = "call_123"
        signature = "abc123"
        encoded = f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}"

        original, decoded_sig = decode_tool_id_and_signature(encoded)
        self.assertEqual(original, tool_id)
        self.assertEqual(decoded_sig, signature)

        # 无签名时返回原ID和None
        original_none, sig_none = decode_tool_id_and_signature(tool_id)
        self.assertEqual(original_none, tool_id)
        self.assertIsNone(sig_none)

    def test_round_trip(self):
        """测试往返编码解码"""
        tool_id = "call_abc123"
        signature = "sig_xyz789"

        encoded = encode_tool_id_with_signature(tool_id, signature)
        decoded_id, decoded_sig = decode_tool_id_and_signature(encoded)

        self.assertEqual(decoded_id, tool_id)
        self.assertEqual(decoded_sig, signature)

    def test_has_valid_thoughtsignature(self):
        """测试签名验证"""
        # 有效签名
        valid_block = {
            "type": "thinking",
            "thinking": "Let me think...",
            "thoughtSignature": "a" * 50  # 足够长度
        }
        self.assertTrue(has_valid_thoughtsignature(valid_block))

        # 无效签名（太短）
        invalid_block = {
            "type": "thinking",
            "thinking": "Let me think...",
            "thoughtSignature": "short"  # 太短
        }
        self.assertFalse(has_valid_thoughtsignature(invalid_block))

        # 尾部签名（空thinking + 任意签名）
        trailing_block = {
            "type": "thinking",
            "thinking": "",
            "thoughtSignature": "short_but_ok_for_trailing"
        }
        self.assertTrue(has_valid_thoughtsignature(trailing_block))

        # 非 thinking 块
        text_block = {"type": "text", "text": "hello"}
        self.assertTrue(has_valid_thoughtsignature(text_block))

    def test_sanitize_thinking_block(self):
        """测试思维块清理"""
        block = {
            "type": "thinking",
            "thinking": "Let me think...",
            "thoughtSignature": "sig123",
            "cache_control": "no-cache",  # 额外字段
            "extra_field": "should_be_removed"
        }

        sanitized = sanitize_thinking_block(block)
        self.assertNotIn("cache_control", sanitized)
        self.assertNotIn("extra_field", sanitized)
        self.assertEqual(sanitized["thoughtSignature"], "sig123")
        self.assertEqual(sanitized["thinking"], "Let me think...")

    def test_remove_trailing_unsigned_thinking(self):
        """测试移除尾部无签名块"""
        blocks = [
            {"type": "text", "text": "msg1"},
            {"type": "thinking", "thinking": "valid", "thoughtSignature": "a"*20},
            {"type": "thinking", "thinking": "invalid", "thoughtSignature": "short"},
            {"type": "thinking", "thinking": "invalid2"}
        ]

        remove_trailing_unsigned_thinking(blocks)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[1]["thinking"], "valid")

    def test_filter_invalid_thinking_blocks(self):
        """测试过滤无效思维块"""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "msg1"},
                    {"type": "thinking", "thinking": "valid", "thoughtSignature": "a"*20},
                    {"type": "thinking", "thinking": "invalid_converted_to_text", "thoughtSignature": "short"},
                    {"type": "thinking", "thinking": "invalid_converted_to_text_2"}
                ]
            }
        ]

        filter_invalid_thinking_blocks(messages)

        content = messages[0]["content"]
        self.assertEqual(len(content), 4)
        self.assertEqual(content[0]["type"], "text")

        # 有效的保留
        self.assertEqual(content[1]["type"], "thinking")

        # 无效的转换为 text
        self.assertEqual(content[2]["type"], "text")
        self.assertEqual(content[2]["text"], "invalid_converted_to_text")

        self.assertEqual(content[3]["type"], "text")
        self.assertEqual(content[3]["text"], "invalid_converted_to_text_2")

if __name__ == '__main__':
    unittest.main()
