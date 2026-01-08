#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复脚本：为流式请求添加超时机制
解决 tool call 卡住的问题

修复内容：
1. get_streaming_client: timeout 默认值从 None 改为 600.0
2. get_streaming_post_context: timeout 默认值从 None 改为 600.0
3. create_streaming_client_with_kwargs: 添加 600.0 秒默认超时

执行方式: python fix_streaming_timeout.py
"""

import os
import shutil
from datetime import datetime

# 配置
TARGET_FILE = "src/httpx_client.py"
BACKUP_DIR = "_archive/backups"

def create_backup(file_path: str) -> str:
    """创建备份文件"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{os.path.basename(file_path)}.bak.{timestamp}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    shutil.copy2(file_path, backup_path)
    print(f"[OK] Created backup: {backup_path}")
    return backup_path


def fix_httpx_client():
    """修复 httpx_client.py 文件"""

    # 读取原文件
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 创建备份
    create_backup(TARGET_FILE)

    # 修复1: get_streaming_client 方法的超时
    old_code_1 = '''    @asynccontextmanager
    async def get_streaming_client(
        self, timeout: float = None, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """获取用于流式请求的HTTP客户端（无超时限制）"""
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)'''

    new_code_1 = '''    @asynccontextmanager
    async def get_streaming_client(
        self, timeout: float = 600.0, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """
        获取用于流式请求的HTTP客户端

        默认超时 600 秒（10分钟），适合 thinking 模型的长时间思考
        如果需要无限等待，可以显式传入 timeout=None
        """
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)'''

    if old_code_1 in content:
        content = content.replace(old_code_1, new_code_1)
        print("[OK] Fix1: get_streaming_client timeout set to 600s")
    else:
        print("[WARN] Fix1: get_streaming_client code not found or already modified")

    # 修复2: get_streaming_post_context 函数的超时
    old_code_2 = '''@asynccontextmanager
async def get_streaming_post_context(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = None,
    **kwargs,
) -> AsyncGenerator[StreamingContext, None]:
    """获取流式POST请求的上下文管理器"""'''

    new_code_2 = '''@asynccontextmanager
async def get_streaming_post_context(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 600.0,
    **kwargs,
) -> AsyncGenerator[StreamingContext, None]:
    """
    获取流式POST请求的上下文管理器

    默认超时 600 秒（10分钟），适合 thinking 模型的长时间思考
    """'''

    if old_code_2 in content:
        content = content.replace(old_code_2, new_code_2)
        print("[OK] Fix2: get_streaming_post_context timeout set to 600s")
    else:
        print("[WARN] Fix2: get_streaming_post_context code not found or already modified")

    # 修复3: create_streaming_client_with_kwargs 函数
    old_code_3 = '''async def create_streaming_client_with_kwargs(**kwargs) -> httpx.AsyncClient:
    """
    创建用于流式处理的独立客户端实例（手动管理生命周期）

    警告：调用者必须确保调用 client.aclose() 来释放资源
    建议使用 get_streaming_client() 上下文管理器代替此方法
    """
    client_kwargs = await http_client.get_client_kwargs(timeout=None, **kwargs)
    return httpx.AsyncClient(**client_kwargs)'''

    new_code_3 = '''async def create_streaming_client_with_kwargs(**kwargs) -> httpx.AsyncClient:
    """
    创建用于流式处理的独立客户端实例（手动管理生命周期）

    警告：调用者必须确保调用 client.aclose() 来释放资源
    建议使用 get_streaming_client() 上下文管理器代替此方法

    默认超时 600 秒（10分钟），适合 thinking 模型的长时间思考
    如果调用方需要无限等待，可以显式传入 timeout=None
    """
    # 默认 600 秒超时，避免无限等待导致 tool call 卡住
    timeout = kwargs.pop('timeout', 600.0)
    client_kwargs = await http_client.get_client_kwargs(timeout=timeout, **kwargs)
    return httpx.AsyncClient(**client_kwargs)'''

    if old_code_3 in content:
        content = content.replace(old_code_3, new_code_3)
        print("[OK] Fix3: create_streaming_client_with_kwargs timeout set to 600s")
    else:
        print("[WARN] Fix3: create_streaming_client_with_kwargs code not found or already modified")

    # 写回文件
    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("")
    print("[DONE] httpx_client.py fix completed!")
    print("[INFO] Change: Streaming request default timeout changed from None (infinite) to 600s (10min)")
    print("       - This prevents tool calls from hanging indefinitely when context is too long")
    print("       - Thinking model long thinking (usually < 5min) still works normally")
    print("       - If longer time needed, caller can explicitly pass larger timeout value")


if __name__ == "__main__":
    print("=" * 60)
    print("[FIX] gcli2api Streaming Request Timeout Fix Script")
    print("=" * 60)
    print("")

    try:
        fix_httpx_client()
        print("")
        print("=" * 60)
        print("[SUCCESS] All fixes completed! Changes take effect on next request.")
        print("=" * 60)
    except Exception as e:
        print(f"[ERROR] Fix failed: {e}")
        import traceback
        traceback.print_exc()
