"""
Bugment 状态管理器

管理 Bugment 工具状态和会话状态。

从 unified_gateway_router.py 抽取的状态管理逻辑。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any, Optional, List
import time
import threading

__all__ = [
    "BugmentStateManager",
    # 兼容函数（与原 unified_gateway_router.py 保持一致）
    "bugment_conversation_state_put",
    "bugment_conversation_state_get",
    "bugment_tool_state_put",
    "bugment_tool_state_get",
]


class BugmentStateManager:
    """
    Bugment 状态管理器 (单例模式)

    管理工具调用状态和会话状态，支持 TTL 自动清理。
    """

    _instance: Optional["BugmentStateManager"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "BugmentStateManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # 工具状态 {session_id: {tool_call_id: state}}
        self._tool_state: Dict[str, Dict[str, Any]] = {}
        # 会话状态 {session_id: state}
        self._conversation_state: Dict[str, Dict[str, Any]] = {}
        # TTL 配置
        self._tool_state_ttl = 60 * 30  # 30 分钟
        self._conversation_state_ttl = 60 * 60  # 1 小时
        # 时间戳记录
        self._tool_state_timestamps: Dict[str, float] = {}
        self._conversation_state_timestamps: Dict[str, float] = {}

    @classmethod
    def get_instance(cls) -> "BugmentStateManager":
        """获取单例实例"""
        return cls()

    def get_tool_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取工具状态

        Args:
            session_id: 会话 ID

        Returns:
            工具状态字典或 None
        """
        self._cleanup_expired()
        return self._tool_state.get(session_id)

    def set_tool_state(self, session_id: str, state: Dict[str, Any]) -> None:
        """
        设置工具状态

        Args:
            session_id: 会话 ID
            state: 状态字典
        """
        self._tool_state[session_id] = state
        self._tool_state_timestamps[session_id] = time.time()

    def get_conversation_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取会话状态

        Args:
            session_id: 会话 ID

        Returns:
            会话状态字典或 None
        """
        self._cleanup_expired()
        return self._conversation_state.get(session_id)

    def set_conversation_state(self, session_id: str, state: Dict[str, Any]) -> None:
        """
        设置会话状态

        Args:
            session_id: 会话 ID
            state: 状态字典
        """
        self._conversation_state[session_id] = state
        self._conversation_state_timestamps[session_id] = time.time()

    def clear_session(self, session_id: str) -> None:
        """
        清除会话的所有状态

        Args:
            session_id: 会话 ID
        """
        self._tool_state.pop(session_id, None)
        self._tool_state_timestamps.pop(session_id, None)
        self._conversation_state.pop(session_id, None)
        self._conversation_state_timestamps.pop(session_id, None)

    def _cleanup_expired(self) -> None:
        """清理过期的状态"""
        now = time.time()

        # 清理过期的工具状态
        expired_tool_sessions = [
            sid for sid, ts in self._tool_state_timestamps.items()
            if now - ts > self._tool_state_ttl
        ]
        for sid in expired_tool_sessions:
            self._tool_state.pop(sid, None)
            self._tool_state_timestamps.pop(sid, None)

        # 清理过期的会话状态
        expired_conv_sessions = [
            sid for sid, ts in self._conversation_state_timestamps.items()
            if now - ts > self._conversation_state_ttl
        ]
        for sid in expired_conv_sessions:
            self._conversation_state.pop(sid, None)
            self._conversation_state_timestamps.pop(sid, None)

    def get_stats(self) -> Dict[str, Any]:
        """
        获取状态统计信息

        Returns:
            统计信息字典
        """
        return {
            "tool_state_count": len(self._tool_state),
            "conversation_state_count": len(self._conversation_state),
            "tool_state_ttl": self._tool_state_ttl,
            "conversation_state_ttl": self._conversation_state_ttl,
        }


# ==================== 全局状态存储（兼容原 unified_gateway_router.py） ====================

# In-memory conversation state to preserve UI-selected model + chat_history across internal requests.
# Bugment sometimes sends internal requests (e.g. prompt enhancer) with empty `model` and/or empty
# `chat_history`. Using per-conversation state avoids falling back to an arbitrary default model.
_BUGMENT_CONVERSATION_STATE: Dict[str, Dict[str, Any]] = {}
_BUGMENT_CONVERSATION_STATE_TTL_SEC = 60 * 60  # 60 minutes

# Tool state for tracking tool calls across requests
_BUGMENT_TOOL_STATE: Dict[str, Dict[str, Any]] = {}
_BUGMENT_TOOL_STATE_TTL_SEC = 60 * 30  # 30 minutes


def _bugment_conversation_state_key(conversation_id: Optional[str]) -> str:
    """生成会话状态的键"""
    return conversation_id or "no_conversation"


def _bugment_conversation_state_prune(now_ts: Optional[float] = None) -> None:
    """清理过期的会话状态"""
    now = now_ts if isinstance(now_ts, (int, float)) else time.time()
    expired = [
        k
        for k, v in _BUGMENT_CONVERSATION_STATE.items()
        if isinstance(v, dict) and (now - float(v.get("ts", 0))) > _BUGMENT_CONVERSATION_STATE_TTL_SEC
    ]
    for k in expired:
        _BUGMENT_CONVERSATION_STATE.pop(k, None)


def bugment_conversation_state_put(
    conversation_id: Optional[str],
    *,
    model: Optional[str] = None,
    chat_history: Any = None,
) -> None:
    """
    保存会话状态

    Args:
        conversation_id: 会话 ID
        model: 模型名称
        chat_history: 聊天历史
    """
    _bugment_conversation_state_prune()
    key = _bugment_conversation_state_key(conversation_id)
    cur = _BUGMENT_CONVERSATION_STATE.get(key) if isinstance(_BUGMENT_CONVERSATION_STATE.get(key), dict) else {}
    next_state: Dict[str, Any] = dict(cur) if isinstance(cur, dict) else {}
    next_state["ts"] = time.time()
    if isinstance(model, str) and model.strip():
        next_state["model"] = model.strip()
    if isinstance(chat_history, list) and chat_history:
        next_state["chat_history"] = chat_history
    _BUGMENT_CONVERSATION_STATE[key] = next_state


def bugment_conversation_state_get(conversation_id: Optional[str]) -> Dict[str, Any]:
    """
    获取会话状态

    Args:
        conversation_id: 会话 ID

    Returns:
        会话状态字典
    """
    _bugment_conversation_state_prune()
    state = _BUGMENT_CONVERSATION_STATE.get(_bugment_conversation_state_key(conversation_id))
    return state if isinstance(state, dict) else {}


def _bugment_tool_state_key(conversation_id: Optional[str], tool_use_id: str) -> str:
    """生成工具状态的键"""
    cid = conversation_id or "no_conversation"
    return f"{cid}:{tool_use_id}"


def _bugment_tool_state_prune(now_ts: Optional[float] = None) -> None:
    """清理过期的工具状态"""
    now = now_ts if isinstance(now_ts, (int, float)) else time.time()
    expired = [
        k for k, v in _BUGMENT_TOOL_STATE.items()
        if isinstance(v, dict) and (now - float(v.get("ts", 0))) > _BUGMENT_TOOL_STATE_TTL_SEC
    ]
    for k in expired:
        _BUGMENT_TOOL_STATE.pop(k, None)


def bugment_tool_state_put(
    conversation_id: Optional[str],
    tool_use_id: str,
    *,
    tool_name: str,
    arguments_json: str,
) -> None:
    """
    保存工具调用状态

    Args:
        conversation_id: 会话 ID
        tool_use_id: 工具调用 ID
        tool_name: 工具名称
        arguments_json: 参数 JSON 字符串
    """
    _bugment_tool_state_prune()
    key = _bugment_tool_state_key(conversation_id, tool_use_id)
    _BUGMENT_TOOL_STATE[key] = {
        "ts": time.time(),
        "tool_name": tool_name,
        "arguments_json": arguments_json,
    }


def bugment_tool_state_get(
    conversation_id: Optional[str],
    tool_use_id: str,
) -> Optional[Dict[str, Any]]:
    """
    获取工具调用状态

    Args:
        conversation_id: 会话 ID
        tool_use_id: 工具调用 ID

    Returns:
        工具状态字典或 None
    """
    _bugment_tool_state_prune()
    return _BUGMENT_TOOL_STATE.get(_bugment_tool_state_key(conversation_id, tool_use_id))
