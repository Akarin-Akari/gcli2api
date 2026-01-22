"""
SSE 到 Augment NDJSON 转换器

将 OpenAI SSE 格式转换为 Augment NDJSON 格式。

从 unified_gateway_router.py 抽取的 SSE 转换逻辑。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any, AsyncGenerator, Optional
import json

# 延迟导入 log，避免循环依赖
try:
    from log import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

__all__ = [
    "convert_sse_to_augment_ndjson",
    "parse_sse_line",
    "SSEParser",
]


def parse_sse_line(line: str) -> Optional[Dict[str, Any]]:
    """
    解析单行 SSE 数据

    Args:
        line: SSE 格式的行

    Returns:
        解析后的 JSON 对象，或 None（如果不是有效的数据行）
    """
    line = line.strip()
    if not line or not line.startswith("data: "):
        return None

    json_str = line[6:].strip()
    if json_str == "[DONE]":
        return {"done": True}

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


class SSEParser:
    """
    SSE 流解析器

    处理分块的 SSE 数据，支持跨块的行缓冲。
    """

    def __init__(self):
        self.buffer = ""

    def feed(self, chunk: str) -> list:
        """
        输入数据块，返回解析出的事件列表

        Args:
            chunk: 数据块

        Returns:
            解析出的事件列表
        """
        self.buffer += chunk
        events = []

        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()

            if not line:
                continue

            if line.startswith("data: "):
                json_str = line[6:].strip()
                if json_str == "[DONE]":
                    events.append({"done": True})
                else:
                    try:
                        events.append(json.loads(json_str))
                    except json.JSONDecodeError:
                        pass

        return events

    def flush(self) -> list:
        """
        刷新缓冲区，返回剩余的事件

        Returns:
            剩余的事件列表
        """
        events = []
        if self.buffer.strip():
            line = self.buffer.strip()
            if line.startswith("data: "):
                json_str = line[6:].strip()
                if json_str == "[DONE]":
                    events.append({"done": True})
                else:
                    try:
                        events.append(json.loads(json_str))
                    except json.JSONDecodeError:
                        pass
        self.buffer = ""
        return events


async def convert_sse_to_augment_ndjson(
    sse_stream: AsyncGenerator,
) -> AsyncGenerator[str, None]:
    """
    将 SSE 格式流转换为 Augment Code 期望的 NDJSON 格式流

    OpenAI SSE 格式: data: {"choices":[{"delta":{"content":"你好"}}]}\\n\\n
    Augment NDJSON 格式: {"text":"你好"}\\n

    Args:
        sse_stream: SSE 格式的异步生成器（可能返回 bytes 或 str）

    Yields:
        Augment NDJSON 格式的字符串（每行一个 {"text": "..."} 对象）
    """
    buffer = ""

    async for chunk in sse_stream:
        if not chunk:
            continue

        # 处理字节类型，转换为字符串
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="ignore")
        elif not isinstance(chunk, str):
            chunk = str(chunk)

        # 将 chunk 添加到缓冲区
        buffer += chunk

        # 按行处理缓冲区（SSE 格式以 \n\n 分隔事件）
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()

            # 跳过空行
            if not line:
                continue

            # 检查是否是 SSE 格式的 data: 行
            if line.startswith("data: "):
                # 提取 JSON 数据
                json_str = line[6:].strip()  # 移除 "data: " 前缀

                # 跳过 [DONE] 标记
                if json_str == "[DONE]":
                    continue

                # 验证是否是有效的 JSON
                try:
                    # 解析 OpenAI 格式的 JSON
                    json_obj = json.loads(json_str)

                    # 提取 content 字段转换为 Augment 格式
                    # OpenAI: {"choices":[{"delta":{"content":"xxx"}}]}
                    # Augment: {"text":"xxx"}
                    if "choices" in json_obj and len(json_obj["choices"]) > 0:
                        choice = json_obj["choices"][0]

                        # 处理流式响应的 delta
                        if "delta" in choice:
                            delta = choice["delta"]

                            # NOTE:
                            # When upstream chooses to call tools, OpenAI streaming returns `delta.tool_calls`
                            # (often with no `delta.content`). If we drop these deltas, the VSCode client will
                            # look like it "ended immediately" when a tool is attempted.
                            tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else None
                            if isinstance(tool_calls, list) and tool_calls:
                                try:
                                    log.warning(
                                        f"[TOOL CALL] Upstream returned tool_calls (count={len(tool_calls)}), "
                                        f"first={json.dumps(tool_calls[0], ensure_ascii=False)[:500]}",
                                        tag="GATEWAY",
                                    )
                                except Exception:
                                    log.warning("[TOOL CALL] Upstream returned tool_calls (unable to dump)", tag="GATEWAY")

                                # Emit a visible message so the user isn't left with an empty response.
                                augment_obj = {
                                    "text": (
                                        "\n[Gateway] 上游模型触发了工具调用(tool_calls)，但当前网关尚未实现将 tool_calls "
                                        "转换/执行为 Augment 工具链的逻辑，因此工具步骤无法继续。"
                                    )
                                }
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"

                            if "content" in delta and delta["content"] is not None:
                                augment_obj = {"text": delta["content"]}
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"

                        # 处理完整响应的 message
                        elif "message" in choice:
                            message = choice["message"]
                            if "content" in message and message["content"] is not None:
                                augment_obj = {"text": message["content"]}
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"

                        # 处理 finish_reason
                        if "finish_reason" in choice and choice["finish_reason"] is not None:
                            # Augment 不需要 finish_reason，跳过
                            if choice["finish_reason"] in ("tool_calls", "function_call"):
                                log.warning(f"[TOOL CALL] finish_reason={choice['finish_reason']}", tag="GATEWAY")
                            continue

                except json.JSONDecodeError:
                    # 如果不是有效的 JSON，记录警告但继续处理
                    log.warning(f"Invalid JSON in SSE stream: {json_str[:100]}")
                    continue
            elif line.startswith(":"):
                # SSE 注释行，跳过
                continue
            elif line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
                # 其他 SSE 字段，跳过
                continue
