#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Signature Cache 补丁脚本

用于修改 anthropic_streaming.py，添加 signature 缓存功能。
执行前会自动备份原文件。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-07
"""

import shutil
import os
from datetime import datetime

def patch_anthropic_streaming():
    """修改 anthropic_streaming.py 添加 signature 缓存支持"""

    file_path = os.path.join(os.path.dirname(__file__), "anthropic_streaming.py")

    # 备份原文件
    backup_path = file_path + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(file_path, backup_path)
    print(f"[PATCH] 已备份原文件到: {backup_path}")

    # 读取文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已经打过补丁
    if '_current_thinking_text' in content:
        print("[PATCH] 文件已包含 _current_thinking_text 字段，跳过补丁")
        return False

    # 补丁1: 添加 import
    if 'from signature_cache import' not in content:
        import_line = "from log import log"
        new_import = """from log import log
from signature_cache import cache_signature"""
        content = content.replace(import_line, new_import)
        print("[PATCH] 已添加 signature_cache import")

    # 补丁2: 在 _StreamingState.__init__ 中添加 _current_thinking_text 字段
    old_init = """        self._current_block_type: Optional[str] = None
        self._current_block_index: int = -1
        self._current_thinking_signature: Optional[str] = None

        self.has_tool_use: bool = False"""

    new_init = """        self._current_block_type: Optional[str] = None
        self._current_block_index: int = -1
        self._current_thinking_signature: Optional[str] = None
        self._current_thinking_text: str = ""  # 用于累积 thinking 文本，支持 signature 缓存

        self.has_tool_use: bool = False"""

    if old_init in content:
        content = content.replace(old_init, new_init)
        print("[PATCH] 已添加 _current_thinking_text 字段")
    else:
        print("[PATCH] 警告: 未找到 _StreamingState.__init__ 的目标代码块")

    # 补丁3: 修改 close_block_if_open 方法，添加缓存写入逻辑
    old_close = """    def close_block_if_open(self) -> Optional[bytes]:
        if self._current_block_type is None:
            return None
        event = _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": self._current_block_index},
        )
        self._current_block_type = None
        self._current_thinking_signature = None
        return event"""

    new_close = """    def close_block_if_open(self) -> Optional[bytes]:
        if self._current_block_type is None:
            return None

        # 在关闭 thinking 块时，将 signature 写入缓存
        if (
            self._current_block_type == "thinking"
            and self._current_thinking_text
            and self._current_thinking_signature
        ):
            try:
                success = cache_signature(
                    thinking_text=self._current_thinking_text,
                    signature=self._current_thinking_signature,
                    model=self.model
                )
                if success:
                    log.debug(
                        f"[SIGNATURE_CACHE] 缓存写入成功: "
                        f"thinking_len={len(self._current_thinking_text)}, "
                        f"model={self.model}"
                    )
            except Exception as e:
                log.warning(f"[SIGNATURE_CACHE] 缓存写入失败: {e}")

        event = _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": self._current_block_index},
        )
        self._current_block_type = None
        self._current_thinking_signature = None
        self._current_thinking_text = ""  # 重置 thinking 文本
        return event"""

    if old_close in content:
        content = content.replace(old_close, new_close)
        print("[PATCH] 已修改 close_block_if_open 方法")
    else:
        print("[PATCH] 警告: 未找到 close_block_if_open 的目标代码块")

    # 补丁4: 修改 open_thinking_block 方法，重置 thinking 文本
    old_open_thinking = """    def open_thinking_block(self, signature: Optional[str]) -> bytes:
        idx = self._next_index()
        self._current_block_type = "thinking"
        self._current_thinking_signature = signature"""

    new_open_thinking = """    def open_thinking_block(self, signature: Optional[str]) -> bytes:
        idx = self._next_index()
        self._current_block_type = "thinking"
        self._current_thinking_signature = signature
        self._current_thinking_text = ""  # 重置 thinking 文本累积器"""

    if old_open_thinking in content:
        content = content.replace(old_open_thinking, new_open_thinking)
        print("[PATCH] 已修改 open_thinking_block 方法")
    else:
        print("[PATCH] 警告: 未找到 open_thinking_block 的目标代码块")

    # 补丁5: 在处理 thinking delta 时累积文本
    # 找到 thinking_text = part.get("text", "") 后面的代码块
    old_thinking_delta = '''                    thinking_text = part.get("text", "")
                    if thinking_text:
                        evt = _sse_event('''

    new_thinking_delta = '''                    thinking_text = part.get("text", "")
                    if thinking_text:
                        state._current_thinking_text += thinking_text  # 累积 thinking 文本
                        evt = _sse_event('''

    if old_thinking_delta in content:
        content = content.replace(old_thinking_delta, new_thinking_delta)
        print("[PATCH] 已添加 thinking 文本累积逻辑")
    else:
        print("[PATCH] 警告: 未找到 thinking_delta 的目标代码块")

    # 写入修改后的内容
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[PATCH] 补丁应用完成: {file_path}")
    return True


if __name__ == "__main__":
    patch_anthropic_streaming()
