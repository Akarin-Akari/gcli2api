"""
三层签名缓存架构升级测试套件

测试内容：
1. Session Cache 功能测试
2. Tool Loop Recovery 功能测试
3. 6层签名恢复策略测试

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-17
"""

import pytest
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSessionCache:
    """Session Cache 功能测试"""

    def test_generate_session_fingerprint_with_user_message(self):
        """测试基于用户消息生成会话指纹"""
        from src.signature_cache import generate_session_fingerprint

        messages = [
            {"role": "user", "content": "Hello, world!"}
        ]
        fingerprint = generate_session_fingerprint(messages)

        assert fingerprint is not None
        assert len(fingerprint) == 16  # MD5 前16位
        assert fingerprint.isalnum()

    def test_generate_session_fingerprint_with_system_message(self):
        """测试基于系统消息生成会话指纹"""
        from src.signature_cache import generate_session_fingerprint

        messages = [
            {"role": "system", "content": "You are a helpful assistant."}
        ]
        fingerprint = generate_session_fingerprint(messages)

        assert fingerprint is not None
        assert len(fingerprint) == 16

    def test_generate_session_fingerprint_empty_messages(self):
        """测试空消息列表"""
        from src.signature_cache import generate_session_fingerprint

        fingerprint = generate_session_fingerprint([])
        # 空消息列表返回空字符串或 None
        assert fingerprint is None or fingerprint == ""

    def test_generate_session_fingerprint_consistency(self):
        """测试相同消息生成相同指纹"""
        from src.signature_cache import generate_session_fingerprint

        messages = [{"role": "user", "content": "Test message"}]

        fp1 = generate_session_fingerprint(messages)
        fp2 = generate_session_fingerprint(messages)

        assert fp1 == fp2

    def test_cache_and_get_session_signature(self):
        """测试 Session 签名缓存和获取"""
        from src.signature_cache import (
            cache_session_signature,
            get_session_signature,
            reset_signature_cache
        )

        # 重置缓存
        reset_signature_cache()

        session_id = "test_session_12345"
        signature = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"

        # 缓存签名
        result = cache_session_signature(session_id, signature)
        assert result is True

        # 获取签名
        cached = get_session_signature(session_id)
        assert cached == signature

    def test_get_session_signature_with_text(self):
        """测试获取 Session 签名及文本"""
        from src.signature_cache import (
            cache_session_signature,
            get_session_signature_with_text,
            reset_signature_cache
        )

        reset_signature_cache()

        session_id = "test_session_67890"
        signature = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
        thinking_text = "Let me think about this..."

        cache_session_signature(session_id, signature, thinking_text)

        result = get_session_signature_with_text(session_id)
        assert result is not None
        assert result[0] == signature
        assert result[1] == thinking_text


class TestToolLoopRecovery:
    """Tool Loop Recovery 功能测试"""

    def test_analyze_conversation_state_empty(self):
        """测试空消息列表"""
        from src.converters.tool_loop_recovery import analyze_conversation_state

        state = analyze_conversation_state([])

        assert state.in_tool_loop is False
        assert state.has_thinking is False
        assert state.last_assistant_index == -1

    def test_analyze_conversation_state_with_tool_result(self):
        """测试包含 tool_result 的对话"""
        from src.converters.tool_loop_recovery import analyze_conversation_state

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tool_1", "name": "test"}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tool_1", "content": "result"}
            ]}
        ]

        state = analyze_conversation_state(messages)

        assert state.in_tool_loop is True
        assert state.last_assistant_index == 1
        assert len(state.pending_tool_results) == 1

    def test_analyze_conversation_state_with_thinking(self):
        """测试包含 thinking 块的对话"""
        from src.converters.tool_loop_recovery import analyze_conversation_state

        messages = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "Let me think..."},
                {"type": "text", "text": "Hello"}
            ]}
        ]

        state = analyze_conversation_state(messages)

        assert state.has_thinking is True
        assert state.last_assistant_index == 0

    def test_detect_thinking_stripped(self):
        """测试检测 thinking 块被过滤"""
        from src.converters.tool_loop_recovery import detect_thinking_stripped

        # 有 tool_use 但没有 thinking
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tool_1", "name": "test"}
            ]}
        ]

        assert detect_thinking_stripped(messages) is True

        # 有 tool_use 也有 thinking
        messages_with_thinking = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "tool_use", "id": "tool_1", "name": "test"}
            ]}
        ]

        assert detect_thinking_stripped(messages_with_thinking) is False

    def test_close_tool_loop_for_thinking(self):
        """测试关闭断裂的工具循环"""
        from src.converters.tool_loop_recovery import close_tool_loop_for_thinking

        # 模拟断裂的工具循环
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tool_1", "name": "test"}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tool_1", "content": "result"}
            ]}
        ]

        original_len = len(messages)
        recovered = close_tool_loop_for_thinking(messages)

        assert recovered is True
        assert len(messages) == original_len + 2  # 注入了2条消息

    def test_no_recovery_needed(self):
        """测试不需要恢复的情况"""
        from src.converters.tool_loop_recovery import close_tool_loop_for_thinking

        # 正常对话，不需要恢复
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Hi there!"}
            ]}
        ]

        original_len = len(messages)
        recovered = close_tool_loop_for_thinking(messages)

        assert recovered is False
        assert len(messages) == original_len


class TestSignatureRecovery:
    """6层签名恢复策略测试"""

    def test_is_valid_signature(self):
        """测试签名验证"""
        from src.converters.signature_recovery import is_valid_signature

        # 有效签名
        valid_sig = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
        assert is_valid_signature(valid_sig) is True

        # 无效签名 - 太短
        assert is_valid_signature("short") is False

        # 无效签名 - 空
        assert is_valid_signature("") is False
        assert is_valid_signature(None) is False

        # 无效签名 - 占位符
        assert is_valid_signature("skip_thought_signature_validator") is False

    def test_recover_signature_for_thinking_client_priority(self):
        """测试 thinking 签名恢复 - 客户端优先"""
        from src.converters.signature_recovery import (
            recover_signature_for_thinking,
            RecoverySource
        )

        client_sig = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"

        result = recover_signature_for_thinking(
            thinking_text="Test thinking",
            client_signature=client_sig
        )

        assert result.success is True
        assert result.source == RecoverySource.CLIENT
        assert result.signature == client_sig

    def test_recover_signature_for_thinking_context_priority(self):
        """测试 thinking 签名恢复 - 上下文优先"""
        from src.converters.signature_recovery import (
            recover_signature_for_thinking,
            RecoverySource
        )

        context_sig = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"

        result = recover_signature_for_thinking(
            thinking_text="Test thinking",
            client_signature=None,
            context_signature=context_sig
        )

        assert result.success is True
        assert result.source == RecoverySource.CONTEXT
        assert result.signature == context_sig

    def test_recover_signature_for_thinking_placeholder_fallback(self):
        """测试 thinking 签名恢复 - 占位符回退"""
        from src.converters.signature_recovery import (
            recover_signature_for_thinking,
            RecoverySource,
            SKIP_SIGNATURE_VALIDATOR
        )
        from src.signature_cache import reset_signature_cache

        # 重置缓存确保没有缓存
        reset_signature_cache()

        result = recover_signature_for_thinking(
            thinking_text="Test thinking",
            use_placeholder_fallback=True
        )

        assert result.source == RecoverySource.PLACEHOLDER
        assert result.signature == SKIP_SIGNATURE_VALIDATOR

    def test_recover_signature_for_tool_use_encoded_id(self):
        """测试工具调用签名恢复 - 从编码ID解码"""
        from src.converters.signature_recovery import (
            recover_signature_for_tool_use,
            RecoverySource
        )

        signature = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
        encoded_id = f"call_123__thought__{signature}"

        result = recover_signature_for_tool_use(
            tool_id="call_123",
            encoded_tool_id=encoded_id
        )

        assert result.success is True
        assert result.source == RecoverySource.ENCODED_TOOL_ID
        assert result.signature == signature

    def test_recover_signature_for_tool_use_tool_cache(self):
        """测试工具调用签名恢复 - 工具ID缓存"""
        from src.converters.signature_recovery import (
            recover_signature_for_tool_use,
            RecoverySource
        )
        from src.signature_cache import cache_tool_signature, reset_signature_cache

        reset_signature_cache()

        tool_id = "call_456"
        signature = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"

        # 先缓存
        cache_tool_signature(tool_id, signature)

        # 再恢复
        result = recover_signature_for_tool_use(
            tool_id=tool_id,
            encoded_tool_id=tool_id  # 没有编码签名
        )

        assert result.success is True
        assert result.source == RecoverySource.TOOL_CACHE
        assert result.signature == signature


class TestIntegration:
    """集成测试"""

    def test_full_workflow(self):
        """测试完整工作流程"""
        from src.signature_cache import (
            generate_session_fingerprint,
            cache_session_signature,
            cache_signature,
            cache_tool_signature,
            get_session_signature,
            get_cached_signature,
            get_tool_signature,
            reset_signature_cache
        )

        reset_signature_cache()

        # 1. 生成会话指纹
        messages = [{"role": "user", "content": "Hello"}]
        session_id = generate_session_fingerprint(messages)
        assert session_id is not None

        # 2. 缓存签名到多个层
        signature = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
        thinking_text = "Let me think..."
        tool_id = "call_789"

        cache_session_signature(session_id, signature, thinking_text)
        cache_signature(thinking_text, signature)
        cache_tool_signature(tool_id, signature)

        # 3. 验证所有层都能获取
        assert get_session_signature(session_id) == signature
        assert get_cached_signature(thinking_text) == signature
        assert get_tool_signature(tool_id) == signature


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
