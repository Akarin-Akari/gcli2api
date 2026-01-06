"""
Context Analyzer - 上下文分析和长度估算
用于检测上下文过长、统计工具结果等

这是自定义功能模块，原版 gcli2api 不包含此功能
"""

from typing import Any, Dict, List, Optional, Tuple

from log import log


# ====================== Token 估算 ======================

def estimate_input_tokens(contents: List[Dict[str, Any]]) -> int:
    """
    粗略估算输入 token 数

    使用简单的字符数除以4的方法进行估算
    这不是精确值，但足以用于检测上下文过长

    Args:
        contents: Antigravity 格式的 contents 列表

    Returns:
        估算的 token 数
    """
    total_chars = 0

    for content in contents:
        parts = content.get("parts", [])
        for part in parts:
            if "text" in part:
                total_chars += len(str(part["text"]))
            elif "functionResponse" in part:
                # 工具结果可能很大
                response = part.get("functionResponse", {}).get("response", {})
                output = response.get("output", "")
                total_chars += len(str(output))
            elif "inlineData" in part:
                # 图片数据（base64）
                data = part.get("inlineData", {}).get("data", "")
                total_chars += len(str(data))

    # 粗略估算：1 token ≈ 4 字符
    return total_chars // 4


def check_context_length(
    contents: List[Dict[str, Any]],
    warning_threshold: int = 80000,   # 与 antigravity_router.py 保持一致
    critical_threshold: int = 120000,  # 与 antigravity_router.py 保持一致
) -> Tuple[str, int]:
    """
    检查上下文长度并返回状态

    Cursor 用户可以使用 /summarize 命令来压缩对话历史

    Args:
        contents: Antigravity 格式的 contents 列表
        warning_threshold: 警告阈值（tokens），默认 80K
        critical_threshold: 危险阈值（tokens），默认 120K

    Returns:
        (status, estimated_tokens)
        status: "ok" | "warning" | "critical"
    """
    estimated_tokens = estimate_input_tokens(contents)

    if estimated_tokens >= critical_threshold:
        log.warning(f"[CONTEXT] CRITICAL: Estimated {estimated_tokens:,} tokens exceeds critical threshold ({critical_threshold:,}). "
                   f"Please use /summarize command in Cursor to compress conversation history.")
        return "critical", estimated_tokens
    elif estimated_tokens >= warning_threshold:
        log.warning(f"[CONTEXT] WARNING: Estimated {estimated_tokens:,} tokens exceeds warning threshold ({warning_threshold:,}). "
                   f"Consider using /summarize command to compress conversation history.")
        return "warning", estimated_tokens
    else:
        return "ok", estimated_tokens


# ====================== 工具结果统计 ======================

def count_tool_results(contents: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    统计工具结果数量和总长度

    Args:
        contents: Antigravity 格式的 contents 列表

    Returns:
        (tool_result_count, total_length)
    """
    tool_result_count = 0
    total_length = 0

    for content in contents:
        if content.get("role") != "user":
            continue

        parts = content.get("parts", [])
        for part in parts:
            if "functionResponse" in part:
                fr = part["functionResponse"]
                output = str(fr.get("response", {}).get("output", ""))
                tool_result_count += 1
                total_length += len(output)

    return tool_result_count, total_length


def has_tool_messages(contents: List[Dict[str, Any]]) -> bool:
    """
    检查是否包含工具调用后的消息

    Args:
        contents: Antigravity 格式的 contents 列表

    Returns:
        是否包含工具结果
    """
    for content in contents:
        if content.get("role") == "user":
            parts = content.get("parts", [])
            for part in parts:
                if "functionResponse" in part:
                    return True
    return False


def log_tool_results_detail(contents: List[Dict[str, Any]], prefix: str = "ANTIGRAVITY"):
    """
    记录工具结果的详细信息（用于调试）

    Args:
        contents: Antigravity 格式的 contents 列表
        prefix: 日志前缀
    """
    for i, content in enumerate(contents):
        if content.get("role") != "user":
            continue

        parts = content.get("parts", [])
        for j, part in enumerate(parts):
            if "functionResponse" in part:
                fr = part["functionResponse"]
                output = str(fr.get("response", {}).get("output", ""))
                log.info(f"[{prefix} DEBUG] Tool result {i}-{j}: "
                       f"id={fr.get('id')}, name={fr.get('name')}, "
                       f"output_type={type(fr.get('response', {}).get('output')).__name__}, "
                       f"output_length={len(output)}")


# ====================== 上下文信息构建 ======================

def build_context_info(
    contents: List[Dict[str, Any]],
    log_details: bool = False,
    prefix: str = "ANTIGRAVITY",
) -> Dict[str, Any]:
    """
    构建上下文信息字典

    这个信息用于传递给错误消息生成器

    Args:
        contents: Antigravity 格式的 contents 列表
        log_details: 是否记录详细日志
        prefix: 日志前缀

    Returns:
        上下文信息字典
    """
    estimated_tokens = estimate_input_tokens(contents)
    tool_result_count, total_tool_results_length = count_tool_results(contents)
    has_tools = has_tool_messages(contents)

    if log_details and has_tools:
        log.info(f"[{prefix} DEBUG] This is a tool-call-follow-up request")
        log_tool_results_detail(contents, prefix)

    # 使用与 antigravity_router.py 一致的阈值
    CONTEXT_WARNING_THRESHOLD = 80000  # 80K tokens
    
    if estimated_tokens > CONTEXT_WARNING_THRESHOLD:
        log.warning(f"[{prefix}] Large input context detected: ~{estimated_tokens:,} tokens. "
                   f"This may cause API to return empty response or stream truncation. "
                   f"Consider using /summarize command in Cursor to compress conversation history.")

    return {
        "estimated_tokens": estimated_tokens,
        "tool_result_count": tool_result_count,
        "total_tool_results_length": total_tool_results_length,
        "has_tool_messages": has_tools,
    }


# ====================== 非流式 Fallback 决策 ======================

def should_use_non_streaming_fallback(
    stream: bool,
    estimated_tokens: int,
    has_tool_messages: bool,
    threshold: int = 60000,
) -> bool:
    """
    判断是否应该使用非流式 fallback

    当上下文过长且是工具调用场景时，非流式请求通常更稳定

    Args:
        stream: 是否是流式请求
        estimated_tokens: 估算的 token 数
        has_tool_messages: 是否包含工具消息
        threshold: 触发 fallback 的阈值

    Returns:
        是否应该使用非流式 fallback
    """
    if not stream:
        return False

    if estimated_tokens <= threshold:
        return False

    if not has_tool_messages:
        return False

    log.warning(f"[CONTEXT] Large estimated context ({estimated_tokens:,} tokens) with tool calls detected. "
               f"Using non-streaming request as fallback for better stability. "
               f"Note: If API caches content, actual processed tokens may be lower. "
               f"Streaming request will dynamically check actual processed tokens and fallback if needed.")

    return True


# ====================== Thinking 模式检测 ======================

def has_valid_thinking_in_messages(messages: List[Any]) -> bool:
    """
    检查历史消息中是否有有效的 thinking block（包含 signature）

    如果 thinking 启用但历史消息没有有效的 thinking block，则应该禁用 thinking
    这可以避免 400 错误："thinking.signature: Field required"

    Args:
        messages: OpenAI 格式的消息列表

    Returns:
        是否有有效的 thinking block
    """
    import re

    for msg in messages:
        # 获取消息内容
        if hasattr(msg, "content"):
            content = msg.content
        elif isinstance(msg, dict):
            content = msg.get("content")
        else:
            continue

        if not content:
            continue

        # 检查字符串格式的 content
        if isinstance(content, str):
            # 检查是否有 thinking 标签
            if re.search(r'<(?:redacted_)?reasoning>.*?</(?:redacted_)?reasoning>', content, flags=re.DOTALL | re.IGNORECASE):
                # 有 thinking 标签，假设有效
                return True

        # 检查数组格式的 content
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type in ("thinking", "redacted_thinking"):
                        # 检查是否有 signature
                        signature = item.get("signature")
                        if signature and signature.strip():
                            return True

    return False


def should_disable_thinking(
    enable_thinking: bool,
    messages: List[Any],
) -> Tuple[bool, str]:
    """
    判断是否应该禁用 thinking 模式

    Args:
        enable_thinking: 当前是否启用 thinking
        messages: OpenAI 格式的消息列表

    Returns:
        (should_disable, reason)
    """
    if not enable_thinking:
        return False, ""

    if has_valid_thinking_in_messages(messages):
        return False, ""

    reason = "Thinking enabled but no valid thinking block (with signature) found in history messages"
    log.warning(f"[CONTEXT] {reason}, disabling thinking mode to avoid 400 error")
    return True, reason


def check_thinking_in_messages(messages: List[Any]) -> bool:
    """
    检查消息中是否有 thinking 内容需要清理

    Args:
        messages: OpenAI 格式的消息列表

    Returns:
        是否有 thinking 内容
    """
    for msg in messages:
        # 获取角色和内容
        if hasattr(msg, "role"):
            role = msg.role
            content = getattr(msg, "content", "")
        elif isinstance(msg, dict):
            role = msg.get("role")
            content = msg.get("content", "")
        else:
            continue

        if role != "assistant":
            continue

        # 检查字符串格式的 content
        if isinstance(content, str):
            content_lower = content.lower()
            if "<think>" in content_lower:
                return True

        # 检查数组格式的 content
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") in ("thinking", "redacted_thinking"):
                        return True

    return False
