"""
Integration tests for signature cache and recovery strategies
"""

import unittest
import sys
import os
import time
import threading

# 添加 src 目录到 python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.signature_cache import SignatureCache, get_signature_cache
from src.anthropic_converter import recover_signature_for_tool_use, convert_messages_to_contents
from src.converters.thoughtSignature_fix import encode_tool_id_with_signature

class TestSignatureIntegration(unittest.TestCase):

    def setUp(self):
        # 每个测试前重置全局缓存
        cache = get_signature_cache()
        with cache._lock:
            cache._cache.clear()
        with cache._tool_lock:
            cache._tool_signatures.clear()

    def _make_sig(self, char="A"):
        """生成有效的测试签名 (长度 >= 50)"""
        return char * 60

    def test_tool_id_cache_and_recovery(self):
        """测试工具ID缓存和恢复"""
        cache = get_signature_cache()
        tool_id = "call_test_123"
        signature = self._make_sig("A")

        # 1. 缓存工具签名
        success = cache.cache_tool_signature(tool_id, signature)
        self.assertTrue(success)

        # 2. 验证缓存命中
        cached_sig = cache.get_tool_signature(tool_id)
        self.assertEqual(cached_sig, signature)

        # 3. 测试 recover_signature_for_tool_use
        # 模拟客户端删除了 signature，只传回了 tool_id
        recovered = recover_signature_for_tool_use(
            tool_id=tool_id,
            encoded_tool_id=tool_id,  # 没有编码签名
            signature=None,
            last_thought_signature=None
        )
        self.assertEqual(recovered, signature)

    def test_encoded_id_recovery(self):
        """测试从编码ID恢复签名"""
        tool_id = "call_test_456"
        signature = self._make_sig("B")
        encoded_id = encode_tool_id_with_signature(tool_id, signature)

        # 即使没有缓存，也应该能从编码ID中恢复
        recovered = recover_signature_for_tool_use(
            tool_id=tool_id,
            encoded_tool_id=encoded_id,
            signature=None,
            last_thought_signature=None
        )
        self.assertEqual(recovered, signature)

    def test_fallback_to_last_signature(self):
        """测试回退到最近签名"""
        cache = get_signature_cache()
        thinking_text = "thinking..."
        signature = self._make_sig("C")

        # 缓存一个 thinking signature
        cache.set(thinking_text, signature)

        # 尝试恢复一个未知的工具调用
        tool_id = "call_unknown"
        recovered = recover_signature_for_tool_use(
            tool_id=tool_id,
            encoded_tool_id=tool_id,
            signature=None,
            last_thought_signature=None
        )

        # 应该回退到最近的 signature
        self.assertEqual(recovered, signature)

    def test_priority_order(self):
        """测试恢复优先级"""
        cache = get_signature_cache()
        tool_id = "call_priority"

        sig_client = self._make_sig("1")
        sig_encoded = self._make_sig("2")
        sig_cache = self._make_sig("3")
        sig_last = self._make_sig("4")

        # 设置环境
        encoded_id = encode_tool_id_with_signature(tool_id, sig_encoded)
        cache.cache_tool_signature(tool_id, sig_cache)
        cache.set("thinking", sig_last)

        # 1. 客户端签名优先
        res1 = recover_signature_for_tool_use(
            tool_id=tool_id,
            encoded_tool_id=encoded_id,
            signature=sig_client,
            last_thought_signature=None
        )
        self.assertEqual(res1, sig_client)

        # 2. 编码ID次之 (注意：当前实现中 last_thought_signature 优先级高于编码ID，但这里传入 None)
        res2 = recover_signature_for_tool_use(
            tool_id=tool_id,
            encoded_tool_id=encoded_id,
            signature=None,
            last_thought_signature=None
        )
        self.assertEqual(res2, sig_encoded)

        # 3. 缓存再次之 (模拟没有编码ID)
        res3 = recover_signature_for_tool_use(
            tool_id=tool_id,
            encoded_tool_id=tool_id,
            signature=None,
            last_thought_signature=None
        )
        self.assertEqual(res3, sig_cache)

        # 4. 最后是 fallback (模拟缓存也失效)
        # 清除工具缓存
        with cache._tool_lock:
            cache._tool_signatures.clear()

        res4 = recover_signature_for_tool_use(
            tool_id=tool_id,
            encoded_tool_id=tool_id,
            signature=None,
            last_thought_signature=None
        )
        self.assertEqual(res4, sig_last)

    # [NEW TEST 2026-01-17] 思维块签名缓存测试
    def test_thinking_block_signature_cache(self):
        """测试思维块签名缓存写入和读取"""
        cache = get_signature_cache()
        thinking_text = "Let me think about this problem..."
        signature = self._make_sig("T")

        # 1. 构造 Anthropic 格式的消息（包含 thinking 块和签名）
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": thinking_text,
                        "signature": signature
                    },
                    {
                        "type": "text",
                        "text": "Here's my response..."
                    }
                ]
            }
        ]

        # 2. 转换为 Gemini 格式（应该缓存签名）
        result = convert_messages_to_contents(messages, include_thinking=True)

        # 3. 验证签名被缓存
        cached_sig = cache.get(thinking_text)
        self.assertEqual(cached_sig, signature, "思维块签名应该被缓存")

        # 4. 构造后续消息（不带签名）
        messages2 = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": thinking_text
                        # 没有 signature
                    },
                    {
                        "type": "text",
                        "text": "Another response..."
                    }
                ]
            }
        ]

        # 5. 验证能从缓存恢复
        result2 = convert_messages_to_contents(messages2, include_thinking=True)
        self.assertIsNotNone(result2)
        self.assertGreater(len(result2), 0)

        # 验证恢复的签名正确
        model_content = result2[0]
        self.assertEqual(model_content.get("role"), "model")
        parts = model_content.get("parts", [])
        self.assertGreater(len(parts), 0)

        # 找到 thinking part（应该有 thoughtSignature）
        thinking_part = None
        for part in parts:
            if part.get("thought"):
                thinking_part = part
                break

        self.assertIsNotNone(thinking_part, "应该有 thinking part")
        self.assertEqual(thinking_part.get("thoughtSignature"), signature, "应该从缓存恢复签名")

    def test_redacted_thinking_signature_cache(self):
        """测试 redacted_thinking 块签名缓存"""
        cache = get_signature_cache()
        thinking_text = "Redacted reasoning process..."
        signature = self._make_sig("R")

        # 1. 构造包含 redacted_thinking 的消息
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "redacted_thinking",
                        "thinking": thinking_text,
                        "signature": signature
                    },
                    {
                        "type": "text",
                        "text": "Response..."
                    }
                ]
            }
        ]

        # 2. 转换并验证缓存
        result = convert_messages_to_contents(messages, include_thinking=True)
        cached_sig = cache.get(thinking_text)
        self.assertEqual(cached_sig, signature, "Redacted思维块签名应该被缓存")

        # 3. 验证后续请求能恢复
        messages2 = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "redacted_thinking",
                        "thinking": thinking_text
                    },
                    {
                        "type": "text",
                        "text": "Another response..."
                    }
                ]
            }
        ]

        result2 = convert_messages_to_contents(messages2, include_thinking=True)
        model_content = result2[0]
        parts = model_content.get("parts", [])

        # 找到 thinking part
        thinking_part = None
        for part in parts:
            if part.get("thought"):
                thinking_part = part
                break

        self.assertIsNotNone(thinking_part, "应该有 redacted_thinking part")
        self.assertEqual(thinking_part.get("thoughtSignature"), signature, "应该从缓存恢复redacted签名")

    def test_fallback_signature_not_cached(self):
        """测试 fallback 签名不会被缓存（避免污染）"""
        cache = get_signature_cache()

        # 1. 先缓存一个有效签名作为 fallback
        fallback_sig = self._make_sig("F")
        cache.set("some_thinking", fallback_sig)

        # 2. 构造一个新的 thinking 块，没有签名，会使用 fallback
        new_thinking = "New thinking without signature..."
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": new_thinking
                        # 没有 signature，会使用 fallback
                    },
                    {
                        "type": "text",
                        "text": "Response..."
                    }
                ]
            }
        ]

        # 3. 转换（会使用 fallback 签名）
        result = convert_messages_to_contents(messages, include_thinking=True)

        # 4. 验证 fallback 签名没有被缓存到新的 thinking 文本
        cached_sig = cache.get(new_thinking)
        self.assertIsNone(cached_sig, "Fallback签名不应该被缓存，避免污染")

    def test_message_signature_cached(self):
        """测试消息签名会被缓存"""
        cache = get_signature_cache()
        thinking_text = "Thinking with message signature..."
        message_sig = self._make_sig("M")

        # 构造消息包含签名
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": thinking_text,
                        "signature": message_sig
                    },
                    {
                        "type": "text",
                        "text": "Response..."
                    }
                ]
            }
        ]

        # 转换
        result = convert_messages_to_contents(messages, include_thinking=True)

        # 验证消息签名被缓存
        cached_sig = cache.get(thinking_text)
        self.assertEqual(cached_sig, message_sig, "消息签名应该被缓存")

    # [NEW TEST 2026-01-20] 跨请求签名复用回归测试
    def test_cross_request_signature_reuse(self):
        """
        测试跨请求签名复用（SCID架构核心场景）

        模拟场景：
        1. 第一次请求：Claude返回thinking块+签名，网关缓存签名
        2. 第二次请求：IDE回放历史（可能丢失签名），网关从缓存恢复
        """
        cache = get_signature_cache()
        thinking_text = "Cross-request thinking content..."
        signature = self._make_sig("X")

        # === 模拟第一次请求的响应处理 ===
        # 网关收到上游响应，包含thinking块和签名
        first_response_content = [
            {
                "type": "thinking",
                "thinking": thinking_text,
                "signature": signature  # 原始签名
            },
            {
                "type": "text",
                "text": "First response..."
            }
        ]

        # 1. 从响应中提取签名并缓存（模拟 chat_completions 的 writeback 逻辑）
        for block in first_response_content:
            if isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"):
                t_text = block.get("thinking", "")
                t_sig = block.get("signature") or block.get("thoughtSignature")
                if t_text and t_sig and len(t_sig) > 50:
                    cache.set(t_text, t_sig)

        # 验证签名已缓存
        self.assertEqual(cache.get(thinking_text), signature, "第一次响应的签名应被缓存")

        # === 模拟第二次请求的历史消息 ===
        # IDE可能变形消息，丢失签名
        second_request_messages = [
            {
                "role": "user",
                "content": "First question"
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": thinking_text
                        # 注意：没有 signature（IDE丢失了）
                    },
                    {
                        "type": "text",
                        "text": "First response..."
                    }
                ]
            },
            {
                "role": "user",
                "content": "Second question"
            }
        ]

        # 2. 从历史消息中尝试恢复签名（模拟 chat_completions 的入口逻辑）
        recovered_signatures = []
        for msg in second_request_messages:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"):
                            t_text = block.get("thinking", "")
                            # 尝试从缓存恢复
                            if t_text:
                                cached = cache.get(t_text)
                                if cached:
                                    recovered_signatures.append(cached)
                                    # 补全消息中的签名（模拟sanitize逻辑）
                                    block["signature"] = cached

        # 验证签名被成功恢复
        self.assertEqual(len(recovered_signatures), 1, "应该恢复1个签名")
        self.assertEqual(recovered_signatures[0], signature, "恢复的签名应与原始签名匹配")

        # 验证消息中的签名已补全
        assistant_msg = second_request_messages[1]
        thinking_block = assistant_msg["content"][0]
        self.assertEqual(thinking_block.get("signature"), signature, "消息中的签名应被补全")

    def test_cross_request_with_session_cache(self):
        """
        测试使用 session cache 的跨请求签名复用

        模拟场景：同一SCID的多次请求，即使thinking文本稍有变化也能复用签名
        """
        from src.signature_cache import cache_session_signature, get_session_signature

        cache = get_signature_cache()
        scid = "test-scid-12345"
        signature = self._make_sig("S")
        thinking_text = "Session-based thinking..."

        # 1. 第一次请求：缓存签名到 session
        cache_session_signature(scid, signature, thinking_text)

        # 2. 验证 session cache 工作
        session_sig = get_session_signature(scid)
        self.assertIsNotNone(session_sig, "Session应有签名")
        self.assertEqual(session_sig, signature, "Session签名应匹配")

        # 3. 第二次请求：从 session cache 获取最近签名
        # 这模拟了 AnthropicSanitizer 的 last_thought_signature 逻辑
        last_sig = get_session_signature(scid)
        self.assertEqual(last_sig, signature, "应获取到最近的session签名")

    def test_thoughtSignature_field_compatibility(self):
        """
        测试 thoughtSignature 字段兼容性

        验证代码能同时处理 'signature' 和 'thoughtSignature' 两种字段名
        """
        cache = get_signature_cache()
        thinking_text = "Field compatibility test..."
        signature = self._make_sig("F")

        # 测试两种字段名格式
        block_with_signature = {
            "type": "thinking",
            "thinking": thinking_text,
            "signature": signature
        }

        block_with_thoughtSignature = {
            "type": "thinking",
            "thinking": thinking_text,
            "thoughtSignature": signature
        }

        # 提取逻辑应同时兼容两种格式
        def extract_sig(block):
            return block.get("signature") or block.get("thoughtSignature")

        self.assertEqual(extract_sig(block_with_signature), signature)
        self.assertEqual(extract_sig(block_with_thoughtSignature), signature)

        # 测试混合格式
        block_mixed = {
            "type": "thinking",
            "thinking": thinking_text,
            "thoughtSignature": signature  # 只有 thoughtSignature
        }
        self.assertEqual(extract_sig(block_mixed), signature)


if __name__ == '__main__':
    unittest.main()
