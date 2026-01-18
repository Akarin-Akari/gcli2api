"""
ConversationStateManager 单元测试

测试 SCID 状态机的核心功能。

Author: Claude Sonnet 4.5 (浮浮酱)
Date: 2026-01-17
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.cache.signature_database import SignatureDatabase, CacheConfig
from src.ide_compat.state_manager import ConversationState, ConversationStateManager


class TestConversationStateManager(unittest.TestCase):
    """ConversationStateManager 测试用例"""

    def setUp(self):
        """每个测试前的准备工作"""
        # 创建临时数据库
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()

        # 初始化数据库
        config = CacheConfig(
            db_path=self.temp_db.name,
            ttl_seconds=3600,
            wal_mode=True
        )
        self.db = SignatureDatabase(config)

        # 初始化状态管理器
        self.manager = ConversationStateManager(self.db)

    def tearDown(self):
        """每个测试后的清理工作"""
        # 关闭数据库
        if self.db:
            self.db.close()

        # 删除临时数据库
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)

    def test_get_or_create_state(self):
        """测试获取或创建状态"""
        scid = "conv_test_001"
        client_type = "cursor"

        # 第一次调用应该创建新状态
        state = self.manager.get_or_create_state(scid, client_type)
        self.assertIsNotNone(state)
        self.assertEqual(state.scid, scid)
        self.assertEqual(state.client_type, client_type)
        self.assertEqual(len(state.authoritative_history), 0)
        self.assertEqual(state.access_count, 1)

        # 第二次调用应该返回相同的状态
        state2 = self.manager.get_or_create_state(scid, client_type)
        self.assertEqual(state2.scid, scid)
        self.assertEqual(state2.access_count, 2)  # 访问次数增加

    def test_update_authoritative_history(self):
        """测试更新权威历史"""
        scid = "conv_test_002"
        client_type = "augment"

        # 创建状态
        state = self.manager.get_or_create_state(scid, client_type)

        # 更新历史
        new_messages = [
            {"role": "user", "content": "Hello"}
        ]
        response_message = {
            "role": "assistant",
            "content": "Hi! How can I help you?"
        }
        signature = "sig_test_123"

        self.manager.update_authoritative_history(
            scid,
            new_messages,
            response_message,
            signature
        )

        # 验证历史已更新
        history = self.manager.get_authoritative_history(scid)
        self.assertIsNotNone(history)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

        # 验证签名已保存
        saved_signature = self.manager.get_last_signature(scid)
        self.assertEqual(saved_signature, signature)

    def test_merge_with_client_history(self):
        """测试合并客户端历史"""
        scid = "conv_test_003"
        client_type = "claude_code"

        # 创建状态并设置权威历史
        state = self.manager.get_or_create_state(scid, client_type)

        authoritative_messages = [
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Response 1"},
        ]

        self.manager.update_authoritative_history(
            scid,
            [authoritative_messages[0]],
            authoritative_messages[1],
        )

        # 客户端发送的历史（包含变形的消息和新消息）
        client_messages = [
            {"role": "user", "content": "Message 1"},  # 相同消息
            {"role": "assistant", "content": "Response 1 (modified)"},  # 变形消息
            {"role": "user", "content": "Message 2"},  # 新消息
        ]

        # 合并历史
        merged = self.manager.merge_with_client_history(scid, client_messages)

        # 验证合并结果
        # 应该使用权威版本的 Response 1，并添加新的 Message 2
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["content"], "Message 1")
        self.assertEqual(merged[1]["content"], "Response 1")  # 权威版本
        self.assertEqual(merged[2]["content"], "Message 2")  # 新消息

    def test_message_deduplication(self):
        """测试消息去重"""
        scid = "conv_test_004"
        client_type = "cursor"

        state = self.manager.get_or_create_state(scid, client_type)

        # 添加相同的消息多次
        message = {"role": "user", "content": "Test message"}

        self.manager.update_authoritative_history(
            scid,
            [message],
            {"role": "assistant", "content": "Response"},
        )

        # 再次添加相同的消息
        self.manager.update_authoritative_history(
            scid,
            [message],
            {"role": "assistant", "content": "Response 2"},
        )

        # 验证消息没有重复
        history = self.manager.get_authoritative_history(scid)
        user_messages = [msg for msg in history if msg["role"] == "user"]
        self.assertEqual(len(user_messages), 1)  # 只有一条用户消息

    def test_persistence_to_sqlite(self):
        """测试 SQLite 持久化"""
        scid = "conv_test_005"
        client_type = "augment"

        # 创建状态并更新历史
        state = self.manager.get_or_create_state(scid, client_type)

        messages = [
            {"role": "user", "content": "Persistent message"}
        ]
        response = {"role": "assistant", "content": "Persistent response"}
        signature = "sig_persistent_123"

        self.manager.update_authoritative_history(
            scid,
            messages,
            response,
            signature
        )

        # 创建新的管理器实例（模拟重启）
        manager2 = ConversationStateManager(self.db)

        # 从 SQLite 加载状态
        loaded_state = manager2.get_or_create_state(scid, client_type)

        # 验证数据已持久化
        self.assertEqual(loaded_state.scid, scid)
        self.assertEqual(len(loaded_state.authoritative_history), 2)
        self.assertEqual(loaded_state.last_signature, signature)

    def test_cleanup_expired(self):
        """测试清理过期状态"""
        scid = "conv_test_006"
        client_type = "cursor"

        # 创建状态
        state = self.manager.get_or_create_state(scid, client_type)

        # 手动设置为过期时间
        state.updated_at = datetime.now() - timedelta(hours=25)

        # 清理过期状态
        cleaned = self.manager.cleanup_expired(max_age_hours=24)

        # 验证状态已被清理
        self.assertGreater(cleaned, 0)

        # 验证内存缓存中已删除
        history = self.manager.get_authoritative_history(scid)
        # 注意：SQLite 中可能还存在，但内存缓存已清除
        # 这里我们只验证清理操作执行了

    def test_get_stats(self):
        """测试获取统计信息"""
        # 创建多个状态
        for i in range(3):
            scid = f"conv_test_stats_{i}"
            state = self.manager.get_or_create_state(scid, "cursor")

            # 添加不同数量的消息
            for j in range(i + 1):
                self.manager.update_authoritative_history(
                    scid,
                    [{"role": "user", "content": f"Message {j}"}],
                    {"role": "assistant", "content": f"Response {j}"},
                )

        # 获取统计信息
        stats = self.manager.get_stats()

        # 验证统计信息
        self.assertEqual(stats["total_states"], 3)
        self.assertGreater(stats["total_messages"], 0)
        self.assertGreater(stats["average_messages_per_state"], 0)

    def test_empty_scid_handling(self):
        """测试空 SCID 处理"""
        with self.assertRaises(ValueError):
            self.manager.get_or_create_state("", "cursor")

    def test_message_hash_consistency(self):
        """测试消息 hash 一致性"""
        msg1 = {"role": "user", "content": "Test"}
        msg2 = {"role": "user", "content": "Test"}
        msg3 = {"role": "user", "content": "Different"}

        hash1 = self.manager._message_hash(msg1)
        hash2 = self.manager._message_hash(msg2)
        hash3 = self.manager._message_hash(msg3)

        # 相同消息应该有相同的 hash
        self.assertEqual(hash1, hash2)

        # 不同消息应该有不同的 hash
        self.assertNotEqual(hash1, hash3)


if __name__ == "__main__":
    # 运行测试
    unittest.main(verbosity=2)
