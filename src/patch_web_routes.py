#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Signature Cache 补丁脚本 - web_routes.py

用于在 web_routes.py 中添加缓存统计 API 端点。
执行前会自动备份原文件。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-07
"""

import shutil
import os
from datetime import datetime

def patch_web_routes():
    """在 web_routes.py 中添加缓存统计 API 端点"""

    file_path = os.path.join(os.path.dirname(__file__), "web_routes.py")

    # 备份原文件
    backup_path = file_path + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(file_path, backup_path)
    print(f"[PATCH] 已备份原文件到: {backup_path}")

    # 读取文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已经打过补丁
    if 'signature_cache_stats' in content:
        print("[PATCH] 文件已包含 signature_cache_stats 端点，跳过补丁")
        return False

    # 补丁1: 添加 import
    import_marker = "from log import log"
    if import_marker in content and "from signature_cache import" not in content:
        new_import = """from log import log
from signature_cache import get_cache_stats, get_signature_cache"""
        content = content.replace(import_marker, new_import)
        print("[PATCH] 已添加 signature_cache import")
    else:
        print("[PATCH] 警告: 未找到 import 标记或已存在 import")

    # 补丁2: 在文件末尾添加新的 API 端点
    # 找到文件末尾的合适位置（在最后一个路由之后）
    api_endpoint = '''

# ==================== Signature Cache 统计 API ====================

@router.get("/cache/signature/stats")
async def signature_cache_stats(token: str = Depends(verify_panel_token)):
    """
    获取 Signature 缓存统计信息

    返回:
    - hits: 缓存命中次数
    - misses: 缓存未命中次数
    - writes: 缓存写入次数
    - evictions: LRU 淘汰次数
    - expirations: TTL 过期次数
    - hit_rate: 缓存命中率
    - cache_size: 当前缓存大小
    - max_size: 最大缓存容量
    - ttl_seconds: TTL 过期时间（秒）
    """
    try:
        stats = get_cache_stats()
        return JSONResponse(content={
            "success": True,
            "data": stats
        })
    except Exception as e:
        log.error(f"获取 Signature 缓存统计失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@router.post("/cache/signature/clear")
async def signature_cache_clear(token: str = Depends(verify_panel_token)):
    """
    清空 Signature 缓存

    返回:
    - cleared_count: 清除的缓存条目数量
    """
    try:
        cache = get_signature_cache()
        cleared_count = cache.clear()
        log.info(f"[SIGNATURE_CACHE] 手动清空缓存: 删除 {cleared_count} 条")
        return JSONResponse(content={
            "success": True,
            "data": {"cleared_count": cleared_count}
        })
    except Exception as e:
        log.error(f"清空 Signature 缓存失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@router.post("/cache/signature/cleanup")
async def signature_cache_cleanup(token: str = Depends(verify_panel_token)):
    """
    清理过期的 Signature 缓存条目

    返回:
    - cleaned_count: 清理的过期条目数量
    """
    try:
        cache = get_signature_cache()
        cleaned_count = cache.cleanup_expired()
        log.info(f"[SIGNATURE_CACHE] 手动清理过期缓存: 删除 {cleaned_count} 条")
        return JSONResponse(content={
            "success": True,
            "data": {"cleaned_count": cleaned_count}
        })
    except Exception as e:
        log.error(f"清理过期 Signature 缓存失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )
'''

    # 在文件末尾添加
    content = content.rstrip() + api_endpoint + "\n"
    print("[PATCH] 已添加 Signature 缓存统计 API 端点")

    # 写入修改后的内容
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[PATCH] 补丁应用完成: {file_path}")
    return True


if __name__ == "__main__":
    patch_web_routes()
