"""
Conversation State Manager - 会话状态管理器

SCID 状态机的核心组件，负责维护每个会话的权威历史记录。

核心职责:
1. 维护每个 SCID 的权威历史消息列表
2. 在 IDE 回放变形消息时，使用权威历史替换
3. 管理签名的会话级缓存
4. 支持 SQLite 持久化

设计原则:
- 网关是权威状态机，不信任 IDE 回放的历史
- 每次响应后更新权威历史
- 使用内存缓存 + SQLite 持久化的双层架构
- 线程安全

Author: Claude Sonnet 4.5 (浮浮酱)
Date: 2026-01-17
"""

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from src.cache.signature_database import SignatureDatabase

log = logging.getLogger("gcli2api.state_manager")


@dataclass
class ConversationState:
    """
    会话状态数据结构

    存储单个会话的完整状态信息，包括权威历史、签名等。
    """
    scid: str                          # Server Conversation ID
    client_type: str                   # 客户端类型 ('cursor' | 'augment' | 'claude_code' | 'unknown')
    authoritative_history: List[Dict]  # 权威历史消息列表
    last_signature: Optional[str]      # 最后一个有效签名
    created_at: datetime               # 创建时间
    updated_at: datetime               # 最后更新时间
    access_count: int = 0              # 访问次数

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（用于序列化）"""
        return {
            "scid": self.scid,
            "client_type": self.client_type,
            "authoritative_history": self.authoritative_history,
            "last_signature": self.last_signature,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationState":
        """从字典创建实例（用于反序列化）"""
        return cls(
            scid=data["scid"],
            client_type=data["client_type"],
            authoritative_history=data["authoritative_history"],
            last_signature=data.get("last_signature"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            access_count=data.get("access_count", 0),
        )


class ConversationStateManager:
    """
    会话状态管理器 - SCID 状态机核心

    职责:
    1. 维护每个 SCID 的权威历史
    2. 在 IDE 回放变形消息时，使用权威历史替换
    3. 管理签名的会话级缓存

    设计原则:
    - 网关是权威状态机，不信任 IDE 回放的历史
    - 每次响应后更新权威历史
    - 支持 SQLite 持久化

    Usage:
        manager = ConversationStateManager(db)

        # 获取或创建状态
        state = manager.get_or_create_state(scid, client_type)

        # 更新权威历史
        manager.update_authoritative_history(
            scid,
            new_messages=[{"role": "user", "content": "Hello"}],
            response_message={"role": "assistant", "content": "Hi!"},
            signature="sig_123"
        )

        # 合并客户端历史
        merged = manager.merge_with_client_history(scid, client_messages)
    """

    def __init__(self, db: Optional[SignatureDatabase] = None):
        """
        初始化状态管理器

        Args:
            db: SignatureDatabase 实例，用于持久化
        """
        self._memory_cache: Dict[str, ConversationState] = {}
        self._db = db
        self._lock = threading.Lock()

        log.info("[STATE_MANAGER] ConversationStateManager initialized")

    def get_or_create_state(self, scid: str, client_type: str) -> ConversationState:
        """
        获取或创建会话状态

        优先从内存缓存获取，其次从 SQLite，最后创建新状态

        Args:
            scid: Server Conversation ID
            client_type: 客户端类型

        Returns:
            ConversationState 实例
        """
        if not scid:
            raise ValueError("scid cannot be empty")

        with self._lock:
            # 1. 尝试从内存缓存获取
            if scid in self._memory_cache:
                state = self._memory_cache[scid]
                state.access_count += 1
                log.debug(f"[STATE_MANAGER] Memory cache hit: scid={scid[:20]}...")
                return state

            # 2. 尝试从 SQLite 加载
            state = self._load_state(scid)
            if state:
                # 回填到内存缓存
                self._memory_cache[scid] = state
                state.access_count += 1
                log.info(f"[STATE_MANAGER] State loaded from SQLite: scid={scid[:20]}...")
                return state

            # 3. 创建新状态
            now = datetime.now()
            state = ConversationState(
                scid=scid,
                client_type=client_type,
                authoritative_history=[],
                last_signature=None,
                created_at=now,
                updated_at=now,
                access_count=1,
            )

            # 存储到内存缓存
            self._memory_cache[scid] = state

            # 持久化到 SQLite
            self._persist_state(state)

            log.info(f"[STATE_MANAGER] New state created: scid={scid[:20]}..., client_type={client_type}")
            return state

    def update_authoritative_history(
        self,
        scid: str,
        new_messages: List[Dict],
        response_message: Dict,
        signature: Optional[str] = None
    ) -> None:
        """
        更新权威历史

        在收到 Claude API 响应后调用，将响应消息追加到权威历史

        Args:
            scid: 会话 ID
            new_messages: 本轮新增的用户消息
            response_message: Claude 响应消息
            signature: 响应中的签名 (如果有)
        """
        if not scid:
            log.warning("[STATE_MANAGER] update_authoritative_history: scid is empty")
            return

        with self._lock:
            state = self._memory_cache.get(scid)
            if not state:
                log.warning(f"[STATE_MANAGER] State not found for scid={scid[:20]}..., cannot update history")
                return

            # 追加新消息到权威历史
            for msg in new_messages:
                # 避免重复添加
                if not self._message_exists_in_history(msg, state.authoritative_history):
                    state.authoritative_history.append(msg)

            # 追加响应消息
            if not self._message_exists_in_history(response_message, state.authoritative_history):
                state.authoritative_history.append(response_message)

            # 更新签名
            if signature:
                state.last_signature = signature

            # 更新时间戳
            state.updated_at = datetime.now()

            # 持久化到 SQLite
            self._persist_state(state)

            log.info(
                f"[STATE_MANAGER] History updated: scid={scid[:20]}..., "
                f"total_messages={len(state.authoritative_history)}, "
                f"has_signature={signature is not None}"
            )

    def get_authoritative_history(self, scid: str) -> Optional[List[Dict]]:
        """
        获取权威历史

        用于替换 IDE 回放的可能变形的历史

        Args:
            scid: 会话 ID

        Returns:
            权威历史消息列表，如果不存在则返回 None
        """
        with self._lock:
            state = self._memory_cache.get(scid)
            if state:
                log.debug(f"[STATE_MANAGER] Authoritative history retrieved: scid={scid[:20]}..., messages={len(state.authoritative_history)}")
                return state.authoritative_history.copy()

            # 尝试从 SQLite 加载
            state = self._load_state(scid)
            if state:
                self._memory_cache[scid] = state
                log.info(f"[STATE_MANAGER] Authoritative history loaded from SQLite: scid={scid[:20]}...")
                return state.authoritative_history.copy()

            return None

    def merge_with_client_history(
        self,
        scid: str,
        client_messages: List[Dict]
    ) -> List[Dict]:
        """
        合并客户端历史与权威历史

        策略:
        1. 使用位置 + role 匹配，而不是纯内容 hash
        2. 对于权威历史范围内的消息，使用权威版本（防止 IDE 变形）
        3. 对于超出权威历史的新消息，追加到结果
        4. 返回合并后的消息列表

        这是核心方法，用于处理 IDE 变形问题

        Args:
            scid: 会话 ID
            client_messages: 客户端发送的消息列表

        Returns:
            合并后的消息列表
        """
        if not scid:
            log.warning("[STATE_MANAGER] merge_with_client_history: scid is empty")
            return client_messages

        with self._lock:
            state = self._memory_cache.get(scid)

            # 如果没有权威历史，直接返回客户端消息
            if not state or not state.authoritative_history:
                log.debug(f"[STATE_MANAGER] No authoritative history for scid={scid[:20]}..., using client messages")
                return client_messages

            authoritative = state.authoritative_history
            auth_len = len(authoritative)

            # 合并策略：位置匹配
            merged = []

            # 1. 对于权威历史范围内的消息，使用权威版本
            for i in range(min(auth_len, len(client_messages))):
                auth_msg = authoritative[i]
                client_msg = client_messages[i]

                # 如果 role 匹配，使用权威版本（即使内容不同）
                if auth_msg.get("role") == client_msg.get("role"):
                    merged.append(auth_msg)
                    log.debug(
                        f"[STATE_MANAGER] Position {i}: Using authoritative version "
                        f"(role={auth_msg.get('role')})"
                    )
                else:
                    # role 不匹配，说明历史已经分叉，使用客户端版本
                    merged.append(client_msg)
                    log.warning(
                        f"[STATE_MANAGER] Position {i}: Role mismatch "
                        f"(auth={auth_msg.get('role')}, client={client_msg.get('role')}), "
                        f"using client version"
                    )

            # 2. 如果客户端有更多消息，追加新消息
            new_messages_count = 0
            if len(client_messages) > auth_len:
                for i in range(auth_len, len(client_messages)):
                    merged.append(client_messages[i])
                    new_messages_count += 1
                    log.debug(
                        f"[STATE_MANAGER] Position {i}: New message "
                        f"(role={client_messages[i].get('role')})"
                    )

            # 3. 如果权威历史有更多消息（客户端历史被截断），保留权威版本
            if auth_len > len(client_messages):
                for i in range(len(client_messages), auth_len):
                    merged.append(authoritative[i])
                    log.debug(
                        f"[STATE_MANAGER] Position {i}: Authoritative message "
                        f"(role={authoritative[i].get('role')})"
                    )

            log.info(
                f"[STATE_MANAGER] History merged: scid={scid[:20]}..., "
                f"authoritative={auth_len}, "
                f"client={len(client_messages)}, "
                f"merged={len(merged)}, "
                f"new={new_messages_count}"
            )

            return merged

    def get_last_signature(self, scid: str) -> Optional[str]:
        """
        获取会话的最后一个有效签名

        Args:
            scid: 会话 ID

        Returns:
            签名字符串，如果不存在则返回 None
        """
        with self._lock:
            state = self._memory_cache.get(scid)
            if state:
                return state.last_signature

            # 尝试从 SQLite 加载
            state = self._load_state(scid)
            if state:
                self._memory_cache[scid] = state
                return state.last_signature

            return None

    def cleanup_expired(self, max_age_hours: int = 24) -> int:
        """
        清理过期的会话状态

        Args:
            max_age_hours: 最大保留时间（小时）

        Returns:
            清理的会话数量
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)

        with self._lock:
            # 清理内存缓存
            expired_scids = [
                scid for scid, state in self._memory_cache.items()
                if state.updated_at < cutoff_time
            ]

            for scid in expired_scids:
                del self._memory_cache[scid]

            memory_cleaned = len(expired_scids)

            # 清理 SQLite
            db_cleaned = 0
            if self._db:
                db_cleaned = self._db.cleanup_expired_states()

            total_cleaned = memory_cleaned + db_cleaned

            if total_cleaned > 0:
                log.info(
                    f"[STATE_MANAGER] Cleaned up {total_cleaned} expired states "
                    f"(memory={memory_cleaned}, db={db_cleaned})"
                )

            return total_cleaned

    def _persist_state(self, state: ConversationState) -> None:
        """
        持久化状态到 SQLite

        Args:
            state: 要持久化的状态
        """
        if not self._db:
            return

        try:
            history_json = json.dumps(state.authoritative_history, ensure_ascii=False)

            success = self._db.store_conversation_state(
                scid=state.scid,
                client_type=state.client_type,
                history=history_json,
                signature=state.last_signature,
            )

            if success:
                log.debug(f"[STATE_MANAGER] State persisted: scid={state.scid[:20]}...")
            else:
                log.warning(f"[STATE_MANAGER] Failed to persist state: scid={state.scid[:20]}...")

        except Exception as e:
            log.error(f"[STATE_MANAGER] Error persisting state: {e}")

    def _load_state(self, scid: str) -> Optional[ConversationState]:
        """
        从 SQLite 加载状态

        Args:
            scid: 会话 ID

        Returns:
            ConversationState 实例，如果不存在则返回 None
        """
        if not self._db:
            return None

        try:
            data = self._db.get_conversation_state(scid)
            if not data:
                return None

            state = ConversationState(
                scid=data["scid"],
                client_type=data["client_type"],
                authoritative_history=data["authoritative_history"],
                last_signature=data.get("last_signature"),
                created_at=datetime.fromisoformat(data["created_at"]),
                updated_at=datetime.fromisoformat(data["updated_at"]),
                access_count=data.get("access_count", 0),
            )

            return state

        except Exception as e:
            log.error(f"[STATE_MANAGER] Error loading state: {e}")
            return None

    def _message_hash(self, message: Dict) -> str:
        """
        计算消息的内容 hash

        用于消息去重和匹配

        Args:
            message: 消息字典

        Returns:
            消息的 SHA256 hash
        """
        # 提取关键字段用于 hash
        key_fields = {
            "role": message.get("role"),
            "content": message.get("content"),
        }

        # 如果有 tool_calls，也包含进来
        if "tool_calls" in message:
            key_fields["tool_calls"] = message["tool_calls"]

        # 如果有 tool_call_id，也包含进来
        if "tool_call_id" in message:
            key_fields["tool_call_id"] = message["tool_call_id"]

        # 序列化并计算 hash
        content = json.dumps(key_fields, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _message_exists_in_history(self, message: Dict, history: List[Dict]) -> bool:
        """
        检查消息是否已存在于历史中

        Args:
            message: 要检查的消息
            history: 历史消息列表

        Returns:
            如果消息已存在则返回 True
        """
        msg_hash = self._message_hash(message)
        return any(self._message_hash(h) == msg_hash for h in history)

    def get_stats(self) -> Dict[str, Any]:
        """
        获取状态管理器统计信息

        Returns:
            统计信息字典
        """
        with self._lock:
            total_states = len(self._memory_cache)
            total_messages = sum(
                len(state.authoritative_history)
                for state in self._memory_cache.values()
            )

            return {
                "total_states": total_states,
                "total_messages": total_messages,
                "average_messages_per_state": total_messages / total_states if total_states > 0 else 0,
            }
