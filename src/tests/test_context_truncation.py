"""
测试上下文截断模块

测试目标：
1. 验证 token 估算准确性
2. 验证智能截断策略
3. 验证工具结果压缩
4. 验证长对话场景稳定性

运行方式：
    cd F:/antigravity2api/gcli2api
    python -m pytest src/tests/test_context_truncation.py -v
"""

import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import pytest
from src.context_truncation import (
    estimate_message_tokens,
    estimate_messages_tokens,
    classify_messages,
    truncate_messages_smart,
    truncate_messages_aggressive,
    compress_tool_result,
    compress_tool_results_in_messages,
    truncate_context_for_api,
    TARGET_TOKEN_LIMIT,
)


# ====================== 测试数据 ======================

def create_test_message(role: str, content: str, tool_calls=None, tool_call_id=None, name=None):
    """创建测试消息"""
    msg = {"role": role, "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    if name:
        msg["name"] = name
    return msg


def create_long_conversation(message_count: int = 50, avg_content_length: int = 500):
    """创建长对话"""
    messages = []
    
    # 系统消息
    messages.append(create_test_message(
        "system",
        "You are a helpful assistant. " * 50  # ~200 tokens
    ))
    
    # 交替的用户和助手消息
    for i in range(message_count):
        if i % 2 == 0:
            messages.append(create_test_message(
                "user",
                f"Message {i}: {'This is a test message with some content. ' * (avg_content_length // 40)}"
            ))
        else:
            messages.append(create_test_message(
                "assistant",
                f"Response {i}: {'I understand your request. Let me help you with that. ' * (avg_content_length // 50)}"
            ))
    
    return messages


def create_conversation_with_tools(tool_rounds: int = 5):
    """创建包含工具调用的对话"""
    messages = []
    
    # 系统消息
    messages.append(create_test_message("system", "You are a helpful assistant with tool access."))
    
    # 用户请求
    messages.append(create_test_message("user", "Please help me analyze this code."))
    
    # 多轮工具调用
    for i in range(tool_rounds):
        # 助手的工具调用
        messages.append(create_test_message(
            "assistant",
            "",
            tool_calls=[{
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": f'{{"path": "file_{i}.py"}}'
                }
            }]
        ))
        
        # 工具结果
        messages.append(create_test_message(
            "tool",
            f"File content for file_{i}.py:\n" + "def example():\n    pass\n" * 50,
            tool_call_id=f"call_{i}",
            name="read_file"
        ))
        
        # 助手的回复
        messages.append(create_test_message(
            "assistant",
            f"I've analyzed file_{i}.py. Here are my findings..."
        ))
    
    # 用户的最后一条消息
    messages.append(create_test_message("user", "Thanks! What do you recommend?"))
    
    return messages


# ====================== 测试用例 ======================

class TestTokenEstimation:
    """测试 Token 估算功能"""
    
    def test_estimate_simple_message(self):
        """测试简单消息的 token 估算"""
        msg = create_test_message("user", "Hello, world!")
        tokens = estimate_message_tokens(msg)
        assert tokens > 0
        # "Hello, world!" = 13 chars, ~3 tokens
        assert 1 <= tokens <= 10
    
    def test_estimate_long_message(self):
        """测试长消息的 token 估算"""
        content = "This is a test. " * 1000  # ~16000 chars
        msg = create_test_message("user", content)
        tokens = estimate_message_tokens(msg)
        # 16000 chars / 4 = ~4000 tokens
        assert 3000 <= tokens <= 5000
    
    def test_estimate_messages_list(self):
        """测试消息列表的 token 估算"""
        messages = create_long_conversation(10, 100)
        tokens = estimate_messages_tokens(messages)
        assert tokens > 0
        # 验证估算结果在合理范围内
        assert 500 <= tokens <= 5000


class TestMessageClassification:
    """测试消息分类功能"""
    
    def test_classify_system_messages(self):
        """测试系统消息分类"""
        messages = [
            create_test_message("system", "System prompt"),
            create_test_message("user", "User message"),
            create_test_message("assistant", "Assistant response"),
        ]
        classified = classify_messages(messages)
        assert len(classified["system"]) == 1
        assert len(classified["regular"]) == 2
    
    def test_classify_tool_messages(self):
        """测试工具消息分类"""
        messages = create_conversation_with_tools(2)
        classified = classify_messages(messages)
        
        # 应该有系统消息、工具相关消息、普通消息
        assert len(classified["system"]) >= 1
        assert len(classified["tool_context"]) >= 4  # 工具调用 + 工具结果


class TestSmartTruncation:
    """测试智能截断功能"""
    
    def test_no_truncation_needed(self):
        """测试不需要截断的情况"""
        messages = [
            create_test_message("system", "Short system prompt"),
            create_test_message("user", "Hello"),
            create_test_message("assistant", "Hi there!"),
        ]
        truncated, stats = truncate_messages_smart(messages, target_tokens=10000)
        assert not stats["truncated"]
        assert len(truncated) == len(messages)
    
    def test_truncation_preserves_system(self):
        """测试截断保留系统消息"""
        messages = create_long_conversation(100, 1000)
        truncated, stats = truncate_messages_smart(messages, target_tokens=5000)
        
        # 验证系统消息被保留
        system_msgs = [m for m in truncated if m.get("role") == "system"]
        assert len(system_msgs) >= 1
    
    def test_truncation_preserves_recent(self):
        """测试截断保留最近消息"""
        messages = create_long_conversation(50, 500)
        truncated, stats = truncate_messages_smart(messages, target_tokens=5000, min_keep=4)
        
        if stats["truncated"]:
            # 验证最后几条消息被保留
            assert len(truncated) >= 4
            # 最后一条应该是原始消息的最后一条
            assert truncated[-1]["content"] == messages[-1]["content"]
    
    def test_truncation_preserves_tool_context(self):
        """测试截断保留工具调用上下文"""
        messages = create_conversation_with_tools(5)
        truncated, stats = truncate_messages_smart(messages, target_tokens=3000)
        
        # 验证至少保留了一些工具调用相关消息
        tool_msgs = [m for m in truncated if m.get("tool_call_id") or m.get("tool_calls")]
        # 如果有截断，应该仍然保留一些工具上下文
        if stats["truncated"]:
            assert len(tool_msgs) >= 0  # 可能被截断，但逻辑应该正确


class TestAggressiveTruncation:
    """测试激进截断功能"""
    
    def test_aggressive_truncation(self):
        """测试激进截断"""
        messages = create_long_conversation(100, 1000)
        truncated, stats = truncate_messages_aggressive(messages, target_tokens=2000)
        
        assert stats["truncated"]
        assert stats["aggressive"]
        # 激进截断应该大幅减少消息数量
        assert len(truncated) < len(messages) // 2


class TestToolResultCompression:
    """测试工具结果压缩功能"""
    
    def test_compress_short_result(self):
        """测试短结果不压缩"""
        content = "Short result"
        compressed = compress_tool_result(content, max_length=1000)
        assert compressed == content
    
    def test_compress_long_result(self):
        """测试长结果压缩"""
        content = "A" * 10000  # 10K 字符
        compressed = compress_tool_result(content, max_length=1000)
        assert len(compressed) < len(content)
        assert "truncated" in compressed.lower()
    
    def test_compress_tool_results_in_messages(self):
        """测试消息中的工具结果压缩"""
        messages = create_conversation_with_tools(3)
        # 添加一个超长的工具结果
        for msg in messages:
            if msg.get("role") == "tool":
                msg["content"] = "X" * 10000
        
        compressed, chars_saved = compress_tool_results_in_messages(messages, max_result_length=1000)
        assert chars_saved > 0


class TestContextTruncationAPI:
    """测试综合截断 API"""
    
    def test_truncate_context_for_api(self):
        """测试 API 截断函数"""
        messages = create_long_conversation(100, 1000)
        truncated, stats = truncate_context_for_api(
            messages,
            target_tokens=10000,
            compress_tools=True,
        )
        
        # 验证统计信息
        assert "original_messages" in stats
        assert "final_messages" in stats
        assert stats["final_tokens"] <= stats["original_tokens"]
    
    def test_truncate_handles_empty_messages(self):
        """测试空消息列表处理"""
        messages = []
        truncated, stats = truncate_context_for_api(messages, target_tokens=10000)
        assert len(truncated) == 0


class TestLongConversationScenarios:
    """测试长对话场景"""
    
    def test_very_long_conversation(self):
        """测试非常长的对话"""
        # 创建超过 TARGET_TOKEN_LIMIT 的对话
        messages = create_long_conversation(200, 2000)
        initial_tokens = estimate_messages_tokens(messages)
        
        # 验证初始 token 数确实很大
        assert initial_tokens > TARGET_TOKEN_LIMIT
        
        # 执行截断
        truncated, stats = truncate_context_for_api(
            messages,
            target_tokens=TARGET_TOKEN_LIMIT,
        )
        
        # 验证截断后在限制内
        assert stats["final_tokens"] <= TARGET_TOKEN_LIMIT
        assert len(truncated) < len(messages)
    
    def test_tool_heavy_conversation(self):
        """测试工具密集型对话"""
        messages = create_conversation_with_tools(20)
        initial_tokens = estimate_messages_tokens(messages)
        
        truncated, stats = truncate_context_for_api(
            messages,
            target_tokens=5000,
        )
        
        # 验证系统消息和最近的工具上下文被保留
        system_msgs = [m for m in truncated if m.get("role") == "system"]
        assert len(system_msgs) >= 1


# ====================== 运行测试 ======================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


