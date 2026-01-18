#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 signature_cache.py 便捷函数未代理到迁移门面的问题 - V2

使用更强力的方式：先删除原文件，再创建新文件

Author: Claude Opus 4.5
Date: 2026-01-12
"""

import os
import shutil
import time
from datetime import datetime

# 目标文件路径
TARGET_FILE = r"F:\antigravity2api\gcli2api\src\signature_cache.py"
BACKUP_DIR = r"F:\antigravity2api\gcli2api\src"

def main():
    print("=" * 60)
    print("Signature Cache Fix Script V2")
    print("=" * 60)

    # 读取原文件
    print("\n[1] Reading original file...")
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    print(f"    Original size: {len(content)} chars")

    # 创建备份
    backup_name = f"signature_cache.py.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    print(f"\n[2] Creating backup: {backup_name}")
    shutil.copy2(TARGET_FILE, backup_path)

    # 定义替换规则
    replacements = [
        # 1. cache_signature
        (
            '''# 便捷函数
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
    return get_signature_cache().set(thinking_text, signature, model)''',
            '''# 便捷函数 - [FIX 2026-01-12] 修改为支持迁移模式代理
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

    return get_signature_cache().set(thinking_text, signature, model)''',
            "cache_signature"
        ),
        # 2. get_cached_signature
        (
            '''def get_cached_signature(thinking_text: str) -> Optional[str]:
    """
    获取缓存的 signature（便捷函数）

    Args:
        thinking_text: thinking 块的文本内容

    Returns:
        缓存的 signature，如果未命中则返回 None
    """
    return get_signature_cache().get(thinking_text)''',
            '''def get_cached_signature(thinking_text: str) -> Optional[str]:
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

    return get_signature_cache().get(thinking_text)''',
            "get_cached_signature"
        ),
        # 3. get_cache_stats
        (
            '''def get_cache_stats() -> Dict[str, Any]:
    """
    获取缓存统计信息（便捷函数）

    Returns:
        缓存统计信息字典
    """
    return get_signature_cache().get_stats()''',
            '''def get_cache_stats() -> Dict[str, Any]:
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

    return get_signature_cache().get_stats()''',
            "get_cache_stats"
        ),
        # 4. get_last_signature
        (
            '''def get_last_signature() -> Optional[str]:
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
        return None''',
            '''def get_last_signature() -> Optional[str]:
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
        return None''',
            "get_last_signature"
        ),
        # 5. get_last_signature_with_text
        (
            '''def get_last_signature_with_text() -> Optional[tuple]:
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
        return None''',
            '''def get_last_signature_with_text() -> Optional[tuple]:
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
        return None''',
            "get_last_signature_with_text"
        ),
    ]

    # 应用替换
    print("\n[3] Applying replacements...")
    modified = content
    for old, new, name in replacements:
        if old in modified:
            modified = modified.replace(old, new, 1)
            print(f"    [OK] {name}")
        else:
            print(f"    [SKIP] {name} - pattern not found")

    print(f"\n    Modified size: {len(modified)} chars")

    # 删除原文件
    print("\n[4] Removing original file...")
    try:
        os.remove(TARGET_FILE)
        print("    Original file removed")
    except PermissionError as e:
        print(f"    [ERROR] Cannot remove file: {e}")
        print("    Trying alternative method...")
        # 尝试重命名
        temp_name = TARGET_FILE + ".old"
        try:
            os.rename(TARGET_FILE, temp_name)
            print(f"    Renamed to {temp_name}")
        except Exception as e2:
            print(f"    [FATAL] Cannot rename file: {e2}")
            return 1

    # 写入新文件
    print("\n[5] Writing new file...")
    time.sleep(0.5)  # 等待文件系统
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(modified)
    print("    New file written")

    # 验证
    print("\n[6] Verifying...")
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        verify_content = f.read()

    checks = [
        "cache_signature: 代理到迁移门面",
        "get_cached_signature: 代理到迁移门面",
        "get_cache_stats: 代理到迁移门面",
        "get_last_signature: 代理到迁移门面",
        "get_last_signature_with_text: 代理到迁移门面",
    ]

    all_ok = True
    for check in checks:
        if check in verify_content:
            print(f"    [PASS] {check.split(':')[0]}")
        else:
            print(f"    [FAIL] {check.split(':')[0]}")
            all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("SUCCESS! All fixes applied.")
    else:
        print("PARTIAL SUCCESS. Some fixes may not have been applied.")
    print("=" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    exit(main())
