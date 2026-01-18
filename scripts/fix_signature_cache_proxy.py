#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 signature_cache.py 便捷函数未代理到迁移门面的问题

问题根因：
    便捷函数 (cache_signature, get_cached_signature, get_cache_stats,
    get_last_signature, get_last_signature_with_text) 直接调用 get_signature_cache()，
    完全绕过了 CacheFacade 迁移逻辑，导致即使启用了 DUAL_WRITE 模式，
    所有调用仍然走旧的纯内存缓存。

修复方案：
    在每个便捷函数中添加迁移模式检查，如果启用了迁移模式，
    则代理调用到 CacheFacade。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-12
"""

import os
import shutil
from datetime import datetime

# 目标文件路径
TARGET_FILE = r"F:\antigravity2api\gcli2api\src\signature_cache.py"

# 备份文件路径
BACKUP_SUFFIX = f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def create_backup(file_path: str) -> str:
    """创建备份文件"""
    backup_path = file_path + BACKUP_SUFFIX
    shutil.copy2(file_path, backup_path)
    print(f"[备份成功] {backup_path}")
    return backup_path


def read_file(file_path: str) -> str:
    """读取文件内容"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()


def write_file(file_path: str, content: str) -> None:
    """写入文件内容"""
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"[写入成功] {file_path}")


# ============================================================
# 需要替换的便捷函数定义 - 原版本
# ============================================================

OLD_CACHE_SIGNATURE = '''# 便捷函数
def cache_signature(thinking_text: str, signature: str, model: Optional[str] = None) -> bool:
    """
    缓存 signature（便捷函数）

    Args:
        thinking_text: thinking 块的文本内容
        signature: 对应的 signature 值
        model: 可选的模型名称

    Returns:
        是否成功缓存
    """
    return get_signature_cache().set(thinking_text, signature, model)'''

OLD_GET_CACHED_SIGNATURE = '''def get_cached_signature(thinking_text: str) -> Optional[str]:
    """
    获取缓存的 signature（便捷函数）

    Args:
        thinking_text: thinking 块的文本内容

    Returns:
        缓存的 signature，如果未命中则返回 None
    """
    return get_signature_cache().get(thinking_text)'''

OLD_GET_CACHE_STATS = '''def get_cache_stats() -> Dict[str, Any]:
    """
    获取缓存统计信息（便捷函数）

    Returns:
        缓存统计信息字典
    """
    return get_signature_cache().get_stats()'''

OLD_GET_LAST_SIGNATURE = '''def get_last_signature() -> Optional[str]:
    """
    获取最近缓存的 signature（用于 fallback）

    当 Cursor 不保留历史消息中的 thinking 内容时，
    可以使用最近缓存的 signature 作为 fallback，
    从而保持 thinking 模式的连续性。

    Returns:
        最近缓存的有效 signature，如果没有则返回 None
    """
    cache = get_signature_cache()
    with cache._lock:
        if not cache._cache:
            log.debug("[SIGNATURE_CACHE] get_last_signature: 缓存为空")
            return None

        # OrderedDict 保持插入顺序，最后一个是最近添加的
        # 从后往前遍历，找到第一个未过期的条目
        for key in reversed(cache._cache.keys()):
            entry = cache._cache[key]
            if not entry.is_expired(cache._ttl_seconds):
                log.info(f"[SIGNATURE_CACHE] get_last_signature: 找到有效的最近 signature, "
                        f"key={key[:16]}..., age={time.time() - entry.timestamp:.1f}s")
                return entry.signature
            else:
                log.debug(f"[SIGNATURE_CACHE] get_last_signature: 跳过过期条目 key={key[:16]}...")

        log.debug("[SIGNATURE_CACHE] get_last_signature: 所有条目都已过期")
        return None'''

OLD_GET_LAST_SIGNATURE_WITH_TEXT = '''def get_last_signature_with_text() -> Optional[tuple]:
    """
    获取最近缓存的 signature 及其对应的 thinking 文本（用于 fallback）

    [FIX 2026-01-09] 这是修复 "Invalid signature in thinking block" 错误的关键函数。

    问题根源：
    - Claude API 的 signature 是与特定的 thinking 内容加密绑定的
    - 之前的 fallback 机制使用 "..." 作为占位文本，但配合缓存的 signature
    - 这导致 signature 与 thinking 内容不匹配，触发 400 错误

    解决方案：
    - 返回 (signature, thinking_text) 元组
    - 调用方使用原始的 thinking_text 而不是占位符

    Returns:
        (signature, thinking_text) 元组，如果没有则返回 None
    """
    cache = get_signature_cache()
    with cache._lock:
        if not cache._cache:
            log.debug("[SIGNATURE_CACHE] get_last_signature_with_text: 缓存为空")
            return None

        # OrderedDict 保持插入顺序，最后一个是最近添加的
        # 从后往前遍历，找到第一个未过期的条目
        for key in reversed(cache._cache.keys()):
            entry = cache._cache[key]
            if not entry.is_expired(cache._ttl_seconds):
                log.info(f"[SIGNATURE_CACHE] get_last_signature_with_text: 找到有效的最近条目, "
                        f"key={key[:16]}..., age={time.time() - entry.timestamp:.1f}s, "
                        f"thinking_len={len(entry.thinking_text)}")
                return (entry.signature, entry.thinking_text)
            else:
                log.debug(f"[SIGNATURE_CACHE] get_last_signature_with_text: 跳过过期条目 key={key[:16]}...")

        log.debug("[SIGNATURE_CACHE] get_last_signature_with_text: 所有条目都已过期")
        return None'''


# ============================================================
# 修复后的便捷函数定义 - 添加迁移模式代理
# ============================================================

NEW_CACHE_SIGNATURE = '''# 便捷函数 - [FIX 2026-01-12] 修改为支持迁移模式代理
def cache_signature(thinking_text: str, signature: str, model: Optional[str] = None) -> bool:
    """
    缓存 signature（便捷函数）

    [FIX 2026-01-12] 添加迁移模式支持，当启用迁移模式时代理到 CacheFacade。

    Args:
        thinking_text: thinking 块的文本内容
        signature: 对应的 signature 值
        model: 可选的模型名称

    Returns:
        是否成功缓存
    """
    # [FIX 2026-01-12] 迁移模式支持
    if _is_migration_mode():
        facade = _get_migration_facade()
        if facade:
            log.debug("[SIGNATURE_CACHE] cache_signature: 代理到迁移门面")
            return facade.cache_signature(thinking_text, signature, model)

    return get_signature_cache().set(thinking_text, signature, model)'''

NEW_GET_CACHED_SIGNATURE = '''def get_cached_signature(thinking_text: str) -> Optional[str]:
    """
    获取缓存的 signature（便捷函数）

    [FIX 2026-01-12] 添加迁移模式支持，当启用迁移模式时代理到 CacheFacade。

    Args:
        thinking_text: thinking 块的文本内容

    Returns:
        缓存的 signature，如果未命中则返回 None
    """
    # [FIX 2026-01-12] 迁移模式支持
    if _is_migration_mode():
        facade = _get_migration_facade()
        if facade:
            log.debug("[SIGNATURE_CACHE] get_cached_signature: 代理到迁移门面")
            return facade.get_cached_signature(thinking_text)

    return get_signature_cache().get(thinking_text)'''

NEW_GET_CACHE_STATS = '''def get_cache_stats() -> Dict[str, Any]:
    """
    获取缓存统计信息（便捷函数）

    [FIX 2026-01-12] 添加迁移模式支持，当启用迁移模式时代理到 CacheFacade。

    Returns:
        缓存统计信息字典
    """
    # [FIX 2026-01-12] 迁移模式支持
    if _is_migration_mode():
        facade = _get_migration_facade()
        if facade:
            log.debug("[SIGNATURE_CACHE] get_cache_stats: 代理到迁移门面")
            return facade.get_cache_stats()

    return get_signature_cache().get_stats()'''

NEW_GET_LAST_SIGNATURE = '''def get_last_signature() -> Optional[str]:
    """
    获取最近缓存的 signature（用于 fallback）

    [FIX 2026-01-12] 添加迁移模式支持，当启用迁移模式时代理到 CacheFacade。

    当 Cursor 不保留历史消息中的 thinking 内容时，
    可以使用最近缓存的 signature 作为 fallback，
    从而保持 thinking 模式的连续性。

    Returns:
        最近缓存的有效 signature，如果没有则返回 None
    """
    # [FIX 2026-01-12] 迁移模式支持
    if _is_migration_mode():
        facade = _get_migration_facade()
        if facade:
            log.debug("[SIGNATURE_CACHE] get_last_signature: 代理到迁移门面")
            return facade.get_last_signature()

    cache = get_signature_cache()
    with cache._lock:
        if not cache._cache:
            log.debug("[SIGNATURE_CACHE] get_last_signature: 缓存为空")
            return None

        # OrderedDict 保持插入顺序，最后一个是最近添加的
        # 从后往前遍历，找到第一个未过期的条目
        for key in reversed(cache._cache.keys()):
            entry = cache._cache[key]
            if not entry.is_expired(cache._ttl_seconds):
                log.info(f"[SIGNATURE_CACHE] get_last_signature: 找到有效的最近 signature, "
                        f"key={key[:16]}..., age={time.time() - entry.timestamp:.1f}s")
                return entry.signature
            else:
                log.debug(f"[SIGNATURE_CACHE] get_last_signature: 跳过过期条目 key={key[:16]}...")

        log.debug("[SIGNATURE_CACHE] get_last_signature: 所有条目都已过期")
        return None'''

NEW_GET_LAST_SIGNATURE_WITH_TEXT = '''def get_last_signature_with_text() -> Optional[tuple]:
    """
    获取最近缓存的 signature 及其对应的 thinking 文本（用于 fallback）

    [FIX 2026-01-09] 这是修复 "Invalid signature in thinking block" 错误的关键函数。
    [FIX 2026-01-12] 添加迁移模式支持，当启用迁移模式时代理到 CacheFacade。

    问题根源：
    - Claude API 的 signature 是与特定的 thinking 内容加密绑定的
    - 之前的 fallback 机制使用 "..." 作为占位文本，但配合缓存的 signature
    - 这导致 signature 与 thinking 内容不匹配，触发 400 错误

    解决方案：
    - 返回 (signature, thinking_text) 元组
    - 调用方使用原始的 thinking_text 而不是占位符

    Returns:
        (signature, thinking_text) 元组，如果没有则返回 None
    """
    # [FIX 2026-01-12] 迁移模式支持
    if _is_migration_mode():
        facade = _get_migration_facade()
        if facade:
            log.debug("[SIGNATURE_CACHE] get_last_signature_with_text: 代理到迁移门面")
            return facade.get_last_signature_with_text()

    cache = get_signature_cache()
    with cache._lock:
        if not cache._cache:
            log.debug("[SIGNATURE_CACHE] get_last_signature_with_text: 缓存为空")
            return None

        # OrderedDict 保持插入顺序，最后一个是最近添加的
        # 从后往前遍历，找到第一个未过期的条目
        for key in reversed(cache._cache.keys()):
            entry = cache._cache[key]
            if not entry.is_expired(cache._ttl_seconds):
                log.info(f"[SIGNATURE_CACHE] get_last_signature_with_text: 找到有效的最近条目, "
                        f"key={key[:16]}..., age={time.time() - entry.timestamp:.1f}s, "
                        f"thinking_len={len(entry.thinking_text)}")
                return (entry.signature, entry.thinking_text)
            else:
                log.debug(f"[SIGNATURE_CACHE] get_last_signature_with_text: 跳过过期条目 key={key[:16]}...")

        log.debug("[SIGNATURE_CACHE] get_last_signature_with_text: 所有条目都已过期")
        return None'''


def apply_fix(content: str) -> str:
    """应用修复"""
    replacements = [
        (OLD_CACHE_SIGNATURE, NEW_CACHE_SIGNATURE, "cache_signature"),
        (OLD_GET_CACHED_SIGNATURE, NEW_GET_CACHED_SIGNATURE, "get_cached_signature"),
        (OLD_GET_CACHE_STATS, NEW_GET_CACHE_STATS, "get_cache_stats"),
        (OLD_GET_LAST_SIGNATURE, NEW_GET_LAST_SIGNATURE, "get_last_signature"),
        (OLD_GET_LAST_SIGNATURE_WITH_TEXT, NEW_GET_LAST_SIGNATURE_WITH_TEXT, "get_last_signature_with_text"),
    ]

    modified_content = content
    for old, new, name in replacements:
        if old in modified_content:
            modified_content = modified_content.replace(old, new, 1)
            print(f"[替换成功] {name}")
        else:
            print(f"[未找到] {name} - 可能已被修改或格式不同")

    return modified_content


def verify_fix(content: str) -> bool:
    """验证修复是否成功"""
    checks = [
        ("cache_signature: 代理到迁移门面", "cache_signature 迁移代理"),
        ("get_cached_signature: 代理到迁移门面", "get_cached_signature 迁移代理"),
        ("get_cache_stats: 代理到迁移门面", "get_cache_stats 迁移代理"),
        ("get_last_signature: 代理到迁移门面", "get_last_signature 迁移代理"),
        ("get_last_signature_with_text: 代理到迁移门面", "get_last_signature_with_text 迁移代理"),
    ]

    all_passed = True
    print("\n[验证结果]")
    for check_str, desc in checks:
        if check_str in content:
            print(f"  ✓ {desc}")
        else:
            print(f"  ✗ {desc}")
            all_passed = False

    return all_passed


def main():
    """主函数"""
    print("=" * 60)
    print("Signature Cache 便捷函数迁移代理修复脚本")
    print("=" * 60)
    print()

    # 检查文件存在
    if not os.path.exists(TARGET_FILE):
        print(f"[错误] 文件不存在: {TARGET_FILE}")
        return 1

    # 创建备份
    backup_path = create_backup(TARGET_FILE)

    # 读取原文件
    content = read_file(TARGET_FILE)
    print(f"[读取成功] 文件大小: {len(content)} 字符")
    print()

    # 应用修复
    print("[开始替换]")
    modified_content = apply_fix(content)
    print()

    # 验证修复
    if not verify_fix(modified_content):
        print("\n[警告] 部分修复未成功，请检查文件格式")
    else:
        print("\n[成功] 所有修复已应用")

    # 写入修改后的内容
    print()
    write_file(TARGET_FILE, modified_content)

    print()
    print("=" * 60)
    print("修复完成！")
    print(f"备份文件: {backup_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    exit(main())
