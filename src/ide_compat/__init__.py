"""
IDE 兼容层 - 确保与各种 IDE 客户端的兼容性

该模块提供了一系列工具来处理不同 IDE 客户端的特殊行为和限制。

核心组件:
- client_detector.py: ClientTypeDetector - 客户端类型检测器
- sanitizer.py: AnthropicSanitizer - 消息净化器,确保消息符合 Anthropic API 规范
- hash_cache.py: ContentHashCache - 内容哈希缓存,用于通过内容 hash 快速查找签名
- state_manager.py: ConversationStateManager - SCID 状态机核心，维护权威历史
"""

from .client_detector import ClientType, ClientInfo, ClientTypeDetector
from .sanitizer import AnthropicSanitizer
from .middleware import IDECompatMiddleware, create_ide_compat_middleware
from .hash_cache import ContentHashCache, HashCacheEntry, HashCacheStats
from .state_manager import ConversationState, ConversationStateManager

__all__ = [
    "ClientType",
    "ClientInfo",
    "ClientTypeDetector",
    "AnthropicSanitizer",
    "IDECompatMiddleware",
    "create_ide_compat_middleware",
    "ContentHashCache",
    "HashCacheEntry",
    "HashCacheStats",
    "ConversationState",
    "ConversationStateManager",
]
