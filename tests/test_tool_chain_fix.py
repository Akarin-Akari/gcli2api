"""
测试工具链完整性修复

验证两个关键修复:
1. functionResponse.name 为空的修复
2. 孤儿 tool_use 的过滤修复
"""

import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.anthropic_converter import _validate_and_fix_tool_chain


def test_orphan_tool_use_removal():
    """测试孤儿 tool_use 的过滤"""
    print("\n=== 测试 1: 孤儿 tool_use 过滤 ===")

    # 模拟 Cursor 发送的不完整历史消息
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "请帮我分析这段代码"}]
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_01ABC123",
                    "name": "read_file",
                    "input": {"path": "test.py"}
                }
            ]
        },
        # 注意: 缺少对应的 tool_result!
        {
            "role": "user",
            "content": [{"type": "text", "text": "继续"}]
        }
    ]

    print(f"原始消息数: {len(messages)}")
    print(f"原始 assistant 消息 content: {messages[1]['content']}")

    # 调用修复函数
    fixed_messages = _validate_and_fix_tool_chain(messages)

    print(f"修复后消息数: {len(fixed_messages)}")
    print(f"修复后 assistant 消息 content: {fixed_messages[1]['content']}")

    # 验证孤儿 tool_use 被过滤
    assert len(fixed_messages) == 3, "消息数应该保持不变"
    assert fixed_messages[1]['content'][0]['type'] == 'text', "孤儿 tool_use 应该被替换为占位符"
    assert fixed_messages[1]['content'][0]['text'] == '...', "占位符文本应该是 '...'"

    print("[PASS] 测试通过: 孤儿 tool_use 被正确过滤")


def test_complete_tool_chain():
    """测试完整的工具链不被修改"""
    print("\n=== 测试 2: 完整工具链保持不变 ===")

    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "请帮我分析这段代码"}]
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_01ABC123",
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
                    "tool_use_id": "toolu_01ABC123",
                    "content": "def test(): pass"
                }
            ]
        }
    ]

    print(f"原始消息数: {len(messages)}")

    # 调用修复函数
    fixed_messages = _validate_and_fix_tool_chain(messages)

    print(f"修复后消息数: {len(fixed_messages)}")

    # 验证消息未被修改
    assert len(fixed_messages) == len(messages), "消息数应该保持不变"
    assert fixed_messages[1]['content'][0]['type'] == 'tool_use', "tool_use 应该保留"
    assert fixed_messages[2]['content'][0]['type'] == 'tool_result', "tool_result 应该保留"

    print("[PASS] 测试通过: 完整工具链保持不变")


def test_encoded_tool_id():
    """测试编码的 tool_id (包含签名)"""
    print("\n=== 测试 3: 编码的 tool_id ===")

    # 模拟包含编码签名的 tool_id
    encoded_id = "toolu_01ABC123__sig__abcdef1234567890"

    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": encoded_id,
                    "name": "read_file",
                    "input": {"path": "test.py"}
                }
            ]
        },
        # 缺少 tool_result
    ]

    print(f"编码的 tool_id: {encoded_id}")

    # 调用修复函数
    fixed_messages = _validate_and_fix_tool_chain(messages)

    print(f"修复后 content: {fixed_messages[0]['content']}")

    # 验证孤儿 tool_use 被过滤
    assert fixed_messages[0]['content'][0]['type'] == 'text', "孤儿 tool_use 应该被替换"

    print("[PASS] 测试通过: 编码的 tool_id 被正确处理")


def test_multiple_orphan_tool_uses():
    """测试多个孤儿 tool_use"""
    print("\n=== 测试 4: 多个孤儿 tool_use ===")

    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_01ABC",
                    "name": "tool1",
                    "input": {}
                },
                {
                    "type": "tool_use",
                    "id": "toolu_02DEF",
                    "name": "tool2",
                    "input": {}
                }
            ]
        },
        # 两个 tool_use 都没有对应的 tool_result
    ]

    print(f"原始 tool_use 数量: 2")

    # 调用修复函数
    fixed_messages = _validate_and_fix_tool_chain(messages)

    print(f"修复后 content: {fixed_messages[0]['content']}")

    # 验证所有孤儿 tool_use 被过滤
    assert len(fixed_messages[0]['content']) == 1, "应该只剩一个占位符"
    assert fixed_messages[0]['content'][0]['type'] == 'text', "应该是占位符文本"

    print("[PASS] 测试通过: 多个孤儿 tool_use 被正确过滤")


if __name__ == "__main__":
    print("开始测试工具链完整性修复...")

    try:
        test_orphan_tool_use_removal()
        test_complete_tool_chain()
        test_encoded_tool_id()
        test_multiple_orphan_tool_uses()

        print("\n" + "="*50)
        print("[SUCCESS] 所有测试通过!")
        print("="*50)

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
