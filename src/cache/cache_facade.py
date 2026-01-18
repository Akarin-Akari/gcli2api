"""
Cache Facade - 统一缓存门面

提供统一的缓存访问入口，隐藏底层实现细节。
支持渐进式迁移，可通过环境变量或配置切换实现。

这是 Phase 3 集成的核心组件：
- 所有缓存操作通过此门面进行
- 自动选择使用旧缓存还是新的迁移适配器
- 提供监控和状态查询接口

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-10
Version: 1.0.0
"""

import os
import logging
import threading
from typing import Optional, Dict, Any, Tuple

log = logging.getLogger("gcli2api.cache.facade")

# 环境变量控制
ENV_USE_MIGRATION_ADAPTER = "CACHE_USE_MIGRATION_ADAPTER"
ENV_MIGRATION_PHASE = "CACHE_MIGRATION_PHASE"


class CacheFacade:
    """
    缓存门面类

    统一管理缓存访问，支持：
    - 透明切换底层实现
    - 渐进式迁移控制
    - 统一的监控接口

    Usage:
        facade = CacheFacade()

        # 与原 SignatureCache 相同的接口
        facade.cache_signature(thinking_text, signature)
        sig = facade.get_cached_signature(thinking_text)

        # 迁移控制
        facade.enable_migration_adapter()
        facade.set_migration_phase("DUAL_WRITE")
    """

    def __init__(self):
        """初始化缓存门面"""
        self._lock = threading.Lock()
        self._use_migration_adapter = self._check_use_migration_adapter()
        self._legacy_cache = None
        self._migration_adapter = None

        log.info(
            f"[CACHE_FACADE] 初始化: "
            f"use_migration_adapter={self._use_migration_adapter}"
        )

    def _check_use_migration_adapter(self) -> bool:
        """
        检查是否使用迁移适配器

        [FIX 2026-01-12] 默认启用迁移适配器，与 signature_cache._is_migration_mode() 保持一致。
        这避免了便捷函数代理到 CacheFacade 后，CacheFacade 又回调便捷函数导致的递归问题。

        只有明确设置环境变量为 false/0/no/off 时才禁用。
        """
        env_value = os.environ.get(ENV_USE_MIGRATION_ADAPTER, "").lower()
        # 只有明确设置为 false 时才禁用，否则默认启用
        if env_value in ("false", "0", "no", "off"):
            return False
        return True  # 默认启用

    def _get_legacy_cache(self):
        """获取旧缓存实例（延迟初始化）"""
        if self._legacy_cache is None:
            with self._lock:
                if self._legacy_cache is None:
                    # 导入旧的 SignatureCache
                    # 使用绝对导入以支持多种运行方式
                    try:
                        from signature_cache import get_signature_cache
                    except ImportError:
                        from ..signature_cache import get_signature_cache
                    self._legacy_cache = get_signature_cache()
                    log.info("[CACHE_FACADE] 初始化旧缓存实例")
        return self._legacy_cache

    def _get_migration_adapter(self):
        """获取迁移适配器实例（延迟初始化）"""
        if self._migration_adapter is None:
            with self._lock:
                if self._migration_adapter is None:
                    from .migration import get_legacy_adapter
                    self._migration_adapter = get_legacy_adapter()
                    log.info("[CACHE_FACADE] 初始化迁移适配器")
        return self._migration_adapter

    def _get_active_cache(self):
        """获取当前活跃的缓存实现"""
        if self._use_migration_adapter:
            return self._get_migration_adapter()
        else:
            return self._get_legacy_cache()

    # ==================== 核心缓存接口 ====================

    def cache_signature(
        self,
        thinking_text: str,
        signature: str,
        model: Optional[str] = None
    ) -> bool:
        """
        缓存 signature

        Args:
            thinking_text: thinking 块的文本内容
            signature: 对应的 signature 值
            model: 可选的模型名称

        Returns:
            是否成功缓存
        """
        cache = self._get_active_cache()
        return cache.set(thinking_text, signature, model)

    def get_cached_signature(self, thinking_text: str) -> Optional[str]:
        """
        获取缓存的 signature

        Args:
            thinking_text: thinking 块的文本内容

        Returns:
            缓存的 signature，如果未命中则返回 None
        """
        cache = self._get_active_cache()
        return cache.get(thinking_text)

    def get_last_signature(self) -> Optional[str]:
        """
        获取最近缓存的 signature（用于 fallback）

        Returns:
            最近缓存的有效 signature
        """
        # 如果使用迁移适配器，它有 get_last_signature 方法
        if self._use_migration_adapter:
            cache = self._get_active_cache()
            if hasattr(cache, 'get_last_signature'):
                return cache.get_last_signature()
            return None
        else:
            # 使用旧缓存时，调用模块级函数
            try:
                from signature_cache import get_last_signature as legacy_get_last
            except ImportError:
                from ..signature_cache import get_last_signature as legacy_get_last
            return legacy_get_last()

    def get_last_signature_with_text(self) -> Optional[Tuple[str, str]]:
        """
        获取最近缓存的 signature 及其对应的 thinking 文本

        Returns:
            (signature, thinking_text) 元组
        """
        # 如果使用迁移适配器，它有 get_last_signature_with_text 方法
        if self._use_migration_adapter:
            cache = self._get_active_cache()
            if hasattr(cache, 'get_last_signature_with_text'):
                return cache.get_last_signature_with_text()
            return None
        else:
            # 使用旧缓存时，调用模块级函数
            try:
                from signature_cache import get_last_signature_with_text as legacy_get_last_with_text
            except ImportError:
                from ..signature_cache import get_last_signature_with_text as legacy_get_last_with_text
            return legacy_get_last_with_text()

    def invalidate(self, thinking_text: str) -> bool:
        """
        使指定的缓存条目失效

        Args:
            thinking_text: thinking 块的文本内容

        Returns:
            是否成功删除
        """
        cache = self._get_active_cache()
        return cache.invalidate(thinking_text)

    def clear(self) -> int:
        """
        清空所有缓存

        Returns:
            清除的条目数量
        """
        cache = self._get_active_cache()
        return cache.clear()

    def cleanup_expired(self) -> int:
        """
        清理所有过期的缓存条目

        Returns:
            清理的条目数量
        """
        cache = self._get_active_cache()
        return cache.cleanup_expired()

    def get_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息

        Returns:
            包含命中率、写入次数等统计信息的字典
        """
        cache = self._get_active_cache()
        stats = cache.get_stats()

        # 添加门面层信息
        stats["facade"] = {
            "use_migration_adapter": self._use_migration_adapter,
            "active_implementation": (
                "migration_adapter" if self._use_migration_adapter
                else "legacy_cache"
            ),
        }

        return stats

    @property
    def size(self) -> int:
        """当前缓存大小"""
        cache = self._get_active_cache()
        return cache.size

    def __len__(self) -> int:
        return self.size

    # ==================== 迁移控制接口 ====================

    def enable_migration_adapter(self) -> None:
        """启用迁移适配器"""
        with self._lock:
            if not self._use_migration_adapter:
                self._use_migration_adapter = True
                log.info("[CACHE_FACADE] 已启用迁移适配器")

    def disable_migration_adapter(self) -> None:
        """禁用迁移适配器（回退到旧缓存）"""
        with self._lock:
            if self._use_migration_adapter:
                self._use_migration_adapter = False
                log.info("[CACHE_FACADE] 已禁用迁移适配器，回退到旧缓存")

    def is_migration_adapter_enabled(self) -> bool:
        """检查是否启用了迁移适配器"""
        return self._use_migration_adapter

    def set_migration_phase(self, phase: str) -> None:
        """
        设置迁移阶段

        Args:
            phase: 阶段名称 (LEGACY_ONLY, DUAL_WRITE, NEW_PREFERRED, NEW_ONLY)
        """
        if not self._use_migration_adapter:
            log.warning(
                "[CACHE_FACADE] 迁移适配器未启用，无法设置迁移阶段。"
                "请先调用 enable_migration_adapter()"
            )
            return

        from .migration import MigrationPhase, set_migration_phase

        phase_map = {
            "LEGACY_ONLY": MigrationPhase.LEGACY_ONLY,
            "DUAL_WRITE": MigrationPhase.DUAL_WRITE,
            "NEW_PREFERRED": MigrationPhase.NEW_PREFERRED,
            "NEW_ONLY": MigrationPhase.NEW_ONLY,
        }

        if phase.upper() not in phase_map:
            log.error(f"[CACHE_FACADE] 无效的迁移阶段: {phase}")
            return

        set_migration_phase(phase_map[phase.upper()])
        log.info(f"[CACHE_FACADE] 迁移阶段已设置为: {phase.upper()}")

    def get_migration_phase(self) -> Optional[str]:
        """获取当前迁移阶段"""
        if not self._use_migration_adapter:
            return None

        adapter = self._get_migration_adapter()
        if hasattr(adapter, 'get_migration_phase'):
            return adapter.get_migration_phase().name
        return None

    def get_migration_status(self) -> Dict[str, Any]:
        """
        获取完整的迁移状态

        Returns:
            包含迁移阶段、统计信息等的字典
        """
        status = {
            "migration_adapter_enabled": self._use_migration_adapter,
            "active_implementation": (
                "migration_adapter" if self._use_migration_adapter
                else "legacy_cache"
            ),
        }

        if self._use_migration_adapter:
            adapter = self._get_migration_adapter()
            if hasattr(adapter, 'get_migration_status'):
                status["migration"] = adapter.get_migration_status()

        return status

    # ==================== Tool Cache 接口 ====================
    # [FIX 2026-01-17] 添加 Tool Cache 持久化支持

    def _get_signature_db(self):
        """获取 SignatureDatabase 实例（延迟初始化）"""
        if not hasattr(self, '_signature_db') or self._signature_db is None:
            with self._lock:
                if not hasattr(self, '_signature_db') or self._signature_db is None:
                    from .signature_database import SignatureDatabase
                    self._signature_db = SignatureDatabase()
                    log.info("[CACHE_FACADE] 初始化 SignatureDatabase 实例")
        return self._signature_db

    def cache_tool_signature(self, tool_id: str, signature: str) -> bool:
        """
        缓存工具ID与签名的映射（持久化到SQLite）

        Args:
            tool_id: 工具调用ID
            signature: thinking 签名

        Returns:
            是否成功缓存
        """
        if not tool_id or not signature:
            return False

        try:
            db = self._get_signature_db()
            result = db.set_tool_signature(tool_id, signature)
            if result:
                log.debug(f"[CACHE_FACADE] Tool signature cached: tool_id={tool_id[:20]}...")
            return result
        except Exception as e:
            log.error(f"[CACHE_FACADE] Error caching tool signature: {e}")
            return False

    def get_tool_signature(self, tool_id: str) -> Optional[str]:
        """
        获取工具ID对应的签名（从SQLite读取）

        Args:
            tool_id: 工具调用ID

        Returns:
            签名字符串，如果未找到则返回 None
        """
        if not tool_id:
            return None

        try:
            db = self._get_signature_db()
            result = db.get_tool_signature(tool_id)
            if result:
                log.debug(f"[CACHE_FACADE] Tool signature hit: tool_id={tool_id[:20]}...")
            return result
        except Exception as e:
            log.error(f"[CACHE_FACADE] Error getting tool signature: {e}")
            return None

    # ==================== Session Cache 接口 ====================
    # [FIX 2026-01-17] 添加 Session Cache 持久化支持

    def cache_session_signature(
        self,
        session_id: str,
        signature: str,
        thinking_text: str = ""
    ) -> bool:
        """
        缓存会话签名（持久化到SQLite）

        Args:
            session_id: 会话ID
            signature: thinking 签名
            thinking_text: 可选的 thinking 文本

        Returns:
            是否成功缓存
        """
        if not session_id or not signature:
            return False

        try:
            db = self._get_signature_db()
            result = db.set_session_signature(session_id, signature, thinking_text)
            if result:
                log.debug(f"[CACHE_FACADE] Session signature cached: session_id={session_id[:20]}...")
            return result
        except Exception as e:
            log.error(f"[CACHE_FACADE] Error caching session signature: {e}")
            return False

    def get_session_signature(self, session_id: str) -> Optional[Tuple[str, str]]:
        """
        获取会话签名（从SQLite读取）

        Args:
            session_id: 会话ID

        Returns:
            (signature, thinking_text) 元组，如果未找到则返回 None
        """
        if not session_id:
            return None

        try:
            db = self._get_signature_db()
            result = db.get_session_signature(session_id)
            if result:
                log.debug(f"[CACHE_FACADE] Session signature hit: session_id={session_id[:20]}...")
            return result
        except Exception as e:
            log.error(f"[CACHE_FACADE] Error getting session signature: {e}")
            return None

    def get_last_session_signature_from_db(self) -> Optional[Tuple[str, str]]:
        """
        获取最近的会话签名（从SQLite读取）

        Returns:
            (signature, thinking_text) 元组，如果未找到则返回 None
        """
        try:
            db = self._get_signature_db()
            result = db.get_last_session_signature()
            if result:
                log.debug("[CACHE_FACADE] Last session signature retrieved from DB")
            return result
        except Exception as e:
            log.error(f"[CACHE_FACADE] Error getting last session signature: {e}")
            return None

    def shutdown(self) -> None:
        """关闭缓存门面"""
        with self._lock:
            if self._migration_adapter is not None:
                if hasattr(self._migration_adapter, 'shutdown'):
                    self._migration_adapter.shutdown()
                self._migration_adapter = None

            # [FIX 2026-01-17] 关闭 SignatureDatabase 连接
            if hasattr(self, '_signature_db') and self._signature_db is not None:
                self._signature_db.close()
                self._signature_db = None

            log.info("[CACHE_FACADE] 缓存门面已关闭")


# ==================== 全局实例 ====================

_global_facade: Optional[CacheFacade] = None
_global_facade_lock = threading.Lock()


def get_cache_facade() -> CacheFacade:
    """
    获取全局缓存门面实例（线程安全的单例）

    Returns:
        全局 CacheFacade 实例
    """
    global _global_facade

    if _global_facade is None:
        with _global_facade_lock:
            if _global_facade is None:
                _global_facade = CacheFacade()
                log.info("[CACHE_FACADE] 创建全局缓存门面实例")

    return _global_facade


def reset_cache_facade() -> None:
    """重置全局缓存门面实例"""
    global _global_facade

    with _global_facade_lock:
        if _global_facade is not None:
            _global_facade.shutdown()
            _global_facade = None
            log.info("[CACHE_FACADE] 重置全局缓存门面实例")


# ==================== 便捷函数 ====================

def cache_signature(
    thinking_text: str,
    signature: str,
    model: Optional[str] = None
) -> bool:
    """缓存 signature（便捷函数）"""
    return get_cache_facade().cache_signature(thinking_text, signature, model)


def get_cached_signature(thinking_text: str) -> Optional[str]:
    """获取缓存的 signature（便捷函数）"""
    return get_cache_facade().get_cached_signature(thinking_text)


def get_last_signature() -> Optional[str]:
    """获取最近缓存的 signature（便捷函数）"""
    return get_cache_facade().get_last_signature()


def get_last_signature_with_text() -> Optional[Tuple[str, str]]:
    """获取最近缓存的 signature 及其 thinking 文本（便捷函数）"""
    return get_cache_facade().get_last_signature_with_text()


def get_cache_stats() -> Dict[str, Any]:
    """获取缓存统计信息（便捷函数）"""
    return get_cache_facade().get_stats()


def get_migration_status() -> Dict[str, Any]:
    """获取迁移状态（便捷函数）"""
    return get_cache_facade().get_migration_status()


def enable_migration() -> None:
    """启用迁移适配器（便捷函数）"""
    get_cache_facade().enable_migration_adapter()


def disable_migration() -> None:
    """禁用迁移适配器（便捷函数）"""
    get_cache_facade().disable_migration_adapter()


def set_migration_phase(phase: str) -> None:
    """设置迁移阶段（便捷函数）"""
    get_cache_facade().set_migration_phase(phase)


# ==================== Tool Cache 便捷函数 ====================
# [FIX 2026-01-17] 添加 Tool Cache 持久化便捷函数

def cache_tool_signature(tool_id: str, signature: str) -> bool:
    """缓存工具ID与签名的映射（便捷函数）"""
    return get_cache_facade().cache_tool_signature(tool_id, signature)


def get_tool_signature(tool_id: str) -> Optional[str]:
    """获取工具ID对应的签名（便捷函数）"""
    return get_cache_facade().get_tool_signature(tool_id)


# ==================== Session Cache 便捷函数 ====================
# [FIX 2026-01-17] 添加 Session Cache 持久化便捷函数

def cache_session_signature(
    session_id: str,
    signature: str,
    thinking_text: str = ""
) -> bool:
    """缓存会话签名（便捷函数）"""
    return get_cache_facade().cache_session_signature(session_id, signature, thinking_text)


def get_session_signature(session_id: str) -> Optional[Tuple[str, str]]:
    """获取会话签名（便捷函数）"""
    return get_cache_facade().get_session_signature(session_id)


def get_last_session_signature_from_db() -> Optional[Tuple[str, str]]:
    """获取最近的会话签名（便捷函数）"""
    return get_cache_facade().get_last_session_signature_from_db()
