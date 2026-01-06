"""
Stream Error Handler - 流式响应错误处理和 Fallback 逻辑
处理流式响应中的空响应、上下文过长等问题

这是自定义功能模块，原版 gcli2api 不包含此功能
"""

import json
import time
from typing import Any, Dict, List, Optional

from log import log


# ====================== 错误消息模板 ======================

def build_tool_reminder_message() -> List[str]:
    """
    构建工具调用格式提示消息
    帮助 AI agent 自我纠正参数格式问题
    """
    return [
        "",
        "⚠️ **Tool Call Format Reminder**:",
        "If you encounter 'invalid arguments' errors when calling tools:",
        "- Use EXACT parameter names from the current tool schema",
        "- Do NOT use parameters from previous conversations",
        "- For terminal/command tools: verify parameter name (may be `command`, `input`, or `cmd`)",
        "- When in doubt: re-read the tool definition",
    ]


def build_safety_blocked_message(finish_reason: str) -> str:
    """
    构建安全过滤被阻止的错误消息

    Args:
        finish_reason: SAFETY 或 RECITATION
    """
    error_parts = [
        f"[Response blocked by {finish_reason} filter. The content may have triggered safety policies.]",
    ]
    error_parts.extend(build_tool_reminder_message())
    return "\n".join(error_parts)


def build_no_response_message() -> str:
    """构建无响应错误消息"""
    error_parts = [
        "[Error: No response from backend. Please try again.]",
    ]
    error_parts.extend(build_tool_reminder_message())
    return "\n".join(error_parts)


def build_context_too_long_message(
    prompt_token_count: int = 0,
    cached_content_token_count: int = 0,
    actual_processed_tokens: int = 0,
    estimated_tokens: int = 0,
    tool_result_count: int = 0,
    total_tool_results_length: int = 0,
) -> str:
    """
    构建上下文过长错误消息

    这个消息会告诉 Cursor 的 AI agent 需要压缩上下文
    """
    error_parts = []
    error_parts.append("❌ **Context Length Limit Exceeded**: Backend returned empty response due to context being too long.")
    error_parts.append("")

    # 添加上下文统计信息（包含缓存信息）
    if prompt_token_count > 0:
        error_parts.append(f"📊 **Input tokens**: {prompt_token_count:,}")
        if cached_content_token_count > 0:
            error_parts.append(f"   - **Cached tokens**: {cached_content_token_count:,} (reused from previous requests)")
            error_parts.append(f"   - **Actual processed tokens**: {actual_processed_tokens:,}")
            if actual_processed_tokens < 50000:
                error_parts.append(f"   ⚠️ Note: Actual processed tokens ({actual_processed_tokens:,}) are within API limit, but API still returned empty response.")
                error_parts.append(f"   This may indicate: 1) API capacity issue, 2) Stream truncation, 3) Safety filter")
        else:
            error_parts.append(f"   ⚠️ **No cache available**: All {prompt_token_count:,} tokens need to be processed")
    elif estimated_tokens > 0:
        error_parts.append(f"📊 **Estimated input tokens**: {estimated_tokens:,} (exceeds API limit)")

    if tool_result_count > 0:
        error_parts.append(f"🔧 **Tool results**: {tool_result_count} results, {total_tool_results_length:,} characters")

    error_parts.append("")
    error_parts.append("💡 **Action Required**: You need to compress the context before retrying:")
    error_parts.append("")
    error_parts.append("🎯 **For Cursor Users**: Use the `/summarize` command in Cursor to automatically compress your conversation history.")
    error_parts.append("")
    error_parts.append("📝 **Manual Options**:")
    error_parts.append("1. **Summarize tool results**: Extract only essential information (errors, summaries, key findings)")
    error_parts.append("2. **Remove old tool results**: Keep only the most recent and relevant tool results")
    error_parts.append("3. **Truncate large results**: For large tool results, keep only the beginning and end, or extract key sections")
    error_parts.append("4. **Start a new chat**: If the conversation is too long, start a fresh chat session")
    error_parts.append("")

    # 工具调用格式提示
    error_parts.append("⚠️ **Tool Call Format Reminder** (IMPORTANT - Read carefully before making tool calls):")
    error_parts.append("")
    error_parts.append("If you encounter 'invalid arguments' errors when calling tools, please note:")
    error_parts.append("- **Always use the EXACT parameter names** as defined in the current tool schema")
    error_parts.append("- **Do NOT use parameters from previous conversations** - tool schemas may have changed")
    error_parts.append("- **Common mistakes to avoid**:")
    error_parts.append("  - `should_read_entire_file` → Use `target_file` with `offset`/`limit` instead")
    error_parts.append("  - `start_line_one_indexed` / `end_line_one_indexed` → Use `offset` / `limit` instead")
    error_parts.append("  - `command` for terminal → Check if it should be `input`, `cmd`, or other name")
    error_parts.append("  - Unknown parameters → Check the tool definition in current context")
    error_parts.append("- **Terminal/Command tools**: Parameter name varies - could be `command`, `input`, `cmd`, or `shell_command`")
    error_parts.append("- **When in doubt**: Re-read the tool definition and use only the parameters listed there")
    error_parts.append("")

    if cached_content_token_count > 0:
        error_parts.append(f"💡 **Cache Hint**: API cached {cached_content_token_count:,} tokens from previous requests.")
        error_parts.append(f"   If you keep similar context in subsequent requests, API may cache more tokens and reduce processing load.")
    error_parts.append("")
    error_parts.append("After compressing the context, retry the request with the reduced context.")

    return "\n".join(error_parts)


def build_streaming_error_message(error: Exception) -> str:
    """构建流式错误消息"""
    error_parts = [
        f"Streaming error: {str(error)}",
    ]
    error_parts.extend(build_tool_reminder_message())
    return "\n".join(error_parts)


# ====================== SSE Chunk 构建器 ======================

class SSEChunkBuilder:
    """SSE Chunk 构建器 - 简化流式响应的构建"""

    def __init__(self, request_id: str, model: str, created: Optional[int] = None):
        self.request_id = request_id
        self.model = model
        self.created = created or int(time.time())

    def build_content_chunk(self, content: str, finish_reason: Optional[str] = None) -> str:
        """构建内容 chunk"""
        chunk = {
            "id": self.request_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {"content": content},
                "finish_reason": finish_reason
            }]
        }
        return f"data: {json.dumps(chunk)}\n\n"

    def build_tool_calls_chunk(self, tool_calls: List[Dict[str, Any]], finish_reason: Optional[str] = None) -> str:
        """构建工具调用 chunk"""
        chunk = {
            "id": self.request_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": tool_calls},
                "finish_reason": finish_reason
            }]
        }
        return f"data: {json.dumps(chunk)}\n\n"

    def build_finish_chunk(self, finish_reason: str, usage: Optional[Dict[str, int]] = None) -> str:
        """构建结束 chunk"""
        chunk = {
            "id": self.request_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason
            }]
        }
        if usage:
            chunk["usage"] = usage
        return f"data: {json.dumps(chunk)}\n\n"

    def build_error_chunk(self, error_message: str) -> str:
        """构建错误 chunk"""
        error_response = {
            "error": {
                "message": error_message,
                "type": "api_error",
                "code": 500
            }
        }
        return f"data: {json.dumps(error_response)}\n\n"

    @staticmethod
    def build_done_marker() -> str:
        """构建结束标记"""
        return "data: [DONE]\n\n"


# ====================== 非流式 Fallback 处理 ======================

async def try_non_streaming_fallback(
    request_body: Dict[str, Any],
    cred_mgr: Any,
    model: str,
    request_id: str,
    created: int,
    actual_processed_tokens: int,
    threshold: int = 50000,
) -> Optional[List[str]]:
    """
    尝试非流式 fallback

    当流式请求返回空响应且实际处理的 tokens 超过阈值时，
    尝试使用非流式请求作为 fallback

    Args:
        request_body: 请求体
        cred_mgr: 凭证管理器
        model: 模型名
        request_id: 请求 ID
        created: 创建时间戳
        actual_processed_tokens: 实际处理的 tokens 数
        threshold: 触发 fallback 的阈值

    Returns:
        如果成功，返回 SSE chunks 列表；否则返回 None
    """
    if actual_processed_tokens <= threshold:
        return None

    log.warning(f"[STREAM FALLBACK] Actual processed tokens ({actual_processed_tokens:,}) exceed threshold ({threshold:,}). "
               f"Attempting non-streaming fallback...")

    try:
        from .antigravity_api import send_antigravity_request_no_stream
        from .antigravity_router import convert_antigravity_response_to_openai

        response_data, _, _ = await send_antigravity_request_no_stream(
            request_body, cred_mgr
        )

        # 转换非流式响应为 OpenAI 格式
        openai_response_dict = convert_antigravity_response_to_openai(response_data, model, request_id)

        # 提取内容和工具调用
        fallback_content = ""
        fallback_tool_calls = []
        if openai_response_dict and openai_response_dict.get("choices"):
            choice = openai_response_dict["choices"][0]
            message = choice.get("message", {})
            if message and message.get("content"):
                fallback_content = message["content"]
            if message and message.get("tool_calls"):
                fallback_tool_calls = message["tool_calls"]

        if not fallback_content and not fallback_tool_calls:
            log.warning("[STREAM FALLBACK] Non-streaming fallback returned empty response.")
            return None

        log.info(f"[STREAM FALLBACK] Non-streaming fallback successful! "
               f"Content length: {len(fallback_content)}, Tool calls: {len(fallback_tool_calls)}")

        # 构建 SSE chunks
        builder = SSEChunkBuilder(request_id, model, created)
        chunks = []

        # 发送工具调用（如果存在）
        if fallback_tool_calls:
            chunks.append(builder.build_tool_calls_chunk(fallback_tool_calls))

        # 发送内容（如果存在，分块发送以保持流式体验）
        if fallback_content:
            chunk_size = 100  # 每块约100字符
            for i in range(0, len(fallback_content), chunk_size):
                chunk_text = fallback_content[i:i + chunk_size]
                chunks.append(builder.build_content_chunk(chunk_text))

        # 发送 finish_reason
        finish_reason = "tool_calls" if fallback_tool_calls else "stop"
        chunks.append(builder.build_finish_chunk(finish_reason))

        return chunks

    except Exception as e:
        log.error(f"[STREAM FALLBACK] Non-streaming fallback failed: {e}")
        return None


# ====================== 流式状态跟踪 ======================

class StreamState:
    """流式响应状态跟踪器"""

    def __init__(self):
        self.thinking_started = False
        self.tool_calls = []
        self.content_buffer = ""
        self.thinking_buffer = ""
        self.success_recorded = False
        self.sse_lines_received = 0
        self.chunks_sent = 0
        self.tool_calls_sent = False
        self.finish_reason_sent = False
        self.has_valid_content = False
        self.empty_parts_count = 0
        self.fallback_attempted = False

        # Token 统计
        self.prompt_token_count = 0
        self.cached_content_token_count = 0
        self.actual_processed_tokens = 0

    def update_token_stats(self, usage_metadata: Dict[str, Any]):
        """从 usage_metadata 更新 token 统计"""
        if not usage_metadata:
            return

        cached = usage_metadata.get("cachedContentTokenCount", 0)
        prompt = usage_metadata.get("promptTokenCount", 0)

        if cached > 0 and "cached_content_token_count" not in self.__dict__:
            self.cached_content_token_count = cached
            if prompt > 0:
                self.prompt_token_count = prompt
                self.actual_processed_tokens = prompt - cached
                log.info(f"[STREAM STATE] Cache detected: {cached:,} tokens cached, "
                       f"{self.actual_processed_tokens:,} tokens actually processed (out of {prompt:,} total)")

    def get_context_info(self) -> Dict[str, Any]:
        """获取上下文信息（用于错误消息）"""
        return {
            "prompt_token_count": self.prompt_token_count,
            "cached_content_token_count": self.cached_content_token_count,
            "actual_processed_tokens": self.actual_processed_tokens,
        }

    def log_summary(self):
        """记录状态摘要"""
        log.info(f"[STREAM STATE] Summary: SSE lines={self.sse_lines_received}, "
               f"Chunks sent={self.chunks_sent}, Content buffer={len(self.content_buffer)}, "
               f"Tool calls={len(self.tool_calls)}, has_valid_content={self.has_valid_content}, "
               f"empty_parts_count={self.empty_parts_count}, finish_reason_sent={self.finish_reason_sent}")
