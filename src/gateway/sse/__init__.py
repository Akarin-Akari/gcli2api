"""
Gateway SSE 转换模块

包含 SSE 流转换功能。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

__all__ = [
    "convert_sse_to_augment_ndjson",
    "parse_sse_line",
    "SSEParser",
]


# 延迟导入避免循环依赖
def __getattr__(name: str):
    if name == "convert_sse_to_augment_ndjson":
        from .converter import convert_sse_to_augment_ndjson
        return convert_sse_to_augment_ndjson
    if name == "parse_sse_line":
        from .converter import parse_sse_line
        return parse_sse_line
    if name == "SSEParser":
        from .converter import SSEParser
        return SSEParser
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
