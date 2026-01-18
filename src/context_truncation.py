"""
Context Truncation - 智能对话历史截断模块
用于处理长对话导致的 token 超限问题

这是自定义功能模块，原版 gcli2api 不包含此功能

核心策略：
1. 保留 system 消息（必须）
2. 保留最近的工具调用上下文（保持工具调用连贯性）
3. 保留最近 N 条消息（基于 token 预算动态计算）
4. 对中间的对话历史进行摘要或删除
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from log import log

# [FIX 2026-01-10] 使用 tiktoken 精确计算 token
try:
    import tiktoken
    # 使用 cl100k_base 编码器（GPT-4/Claude 使用的编码器）
    _TIKTOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
    TIKTOKEN_AVAILABLE = True
    log.info("[CONTEXT TRUNCATION] tiktoken 已加载，使用精确 token 计算")
except ImportError:
    _TIKTOKEN_ENCODER = None
    TIKTOKEN_AVAILABLE = False
    log.warning("[CONTEXT TRUNCATION] tiktoken 未安装，使用字符估算模式")


# ====================== 配置常量 ======================

# Token 限制配置
# 注意：这些值需要根据实际 API 限制调整

# [FIX 2026-01-10] 动态阈值调整：根据模型类型设置不同的上下文限制
# [FIX 2026-01-11] 降低安全边际，为思考模式大量输出预留更多空间
# 模型系列 -> (上下文限制, 安全边际系数)
MODEL_CONTEXT_LIMITS = {
    # Claude 系列：200K 上下文
    # 思考模式输出可能高达 40K+，需要预留更多输出空间
    "claude": (200000, 0.55),      # 200K * 0.55 = 110K 安全限制，预留 90K 给输出
    "claude-opus": (200000, 0.50), # Opus thinking 需要更多输出空间
    "claude-sonnet": (200000, 0.55),
    "claude-haiku": (200000, 0.65),  # Haiku 输出较少，可以更激进
    
    # Gemini 3 系列：1M 上下文
    "gemini-3": (1000000, 0.70),   # 1M * 0.70 = 700K 安全限制
    "gemini-3-flash": (1000000, 0.75),
    "gemini-3-pro": (1000000, 0.70),
    
    # Gemini 2.5 系列：1M 上下文（与 Gemini 2.0/3.0 一致）
    "gemini-2.5": (1000000, 0.70),  # 1M * 0.70 = 700K 安全限制  # 128K * 0.80 = 102K 安全限制
    "gemini-2.5-flash": (1000000, 0.75),  # 1M * 0.75 = 750K 安全限制
    "gemini-2.5-pro": (1000000, 0.70),  # 1M * 0.70 = 700K 安全限制
    
    # GPT 系列：128K 上下文
    "gpt": (128000, 0.80),
    "gpt-4": (128000, 0.80),
    "gpt-oss": (128000, 0.80),
    
    # 默认值（保守估计）
    "default": (100000, 0.60),     # 100K * 0.60 = 60K 安全限制
}

def get_model_context_limit(model_name: str) -> Tuple[int, float]:
    """
    根据模型名称获取上下文限制和安全边际系数
    
    Args:
        model_name: 模型名称（如 "claude-sonnet-4-5", "gemini-3-flash"）
        
    Returns:
        (context_limit, safety_margin)
    """
    if not model_name:
        return MODEL_CONTEXT_LIMITS["default"]
    
    model_lower = model_name.lower()
    
    # 按优先级匹配（更具体的优先）
    for prefix in ["gemini-3-flash", "gemini-3-pro", "gemini-3",
                   "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5",
                   "claude-opus", "claude-sonnet", "claude-haiku", "claude",
                   "gpt-4", "gpt-oss", "gpt"]:
        if prefix in model_lower:
            return MODEL_CONTEXT_LIMITS[prefix]
    
    return MODEL_CONTEXT_LIMITS["default"]


def get_dynamic_target_limit(model_name: str, max_output_tokens: int = 16384) -> int:
    """
    动态计算目标 token 限制
    
    Args:
        model_name: 模型名称
        max_output_tokens: 预期的最大输出 token 数
        
    Returns:
        目标 token 限制
    """
    context_limit, safety_margin = get_model_context_limit(model_name)
    
    # 计算安全的输入限制
    # 公式：safe_input = (context_limit * safety_margin) - max_output_tokens
    safe_input = int(context_limit * safety_margin) - max_output_tokens
    
    # 确保至少有 10K tokens
    safe_input = max(safe_input, 10000)
    
    log.debug(f"[DYNAMIC LIMIT] model={model_name}, context={context_limit:,}, "
             f"margin={safety_margin}, output={max_output_tokens:,}, safe_input={safe_input:,}")
    
    return safe_input


# 目标 token 数量（截断后的目标值，留出足够的输出空间）
# 注意：这是默认值，实际使用时应该调用 get_dynamic_target_limit()
TARGET_TOKEN_LIMIT = 60000  # 60K tokens - 保守值，确保有足够输出空间

# 最小保留消息数（即使 token 超限也至少保留这些消息）
MIN_KEEP_MESSAGES = 4  # 至少保留最近 4 条消息

# 工具调用上下文保护：保留最近 N 轮工具调用相关的消息
TOOL_CONTEXT_PROTECT_ROUNDS = 3

# 每条消息的默认 token 估算系数
# 实际上不同类型的消息 token 密度不同，这里使用保守估算
CHARS_PER_TOKEN = 4  # 1 token ≈ 4 字符（英文），中文约 2-3 字符

# 工具结果的 token 估算系数（工具结果通常更密集）
TOOL_RESULT_CHARS_PER_TOKEN = 3

# ====================== [FIX 2026-01-15] AM兼容工具结果压缩配置 ======================
# 同步自 Antigravity-Manager/src-tauri/src/proxy/mappers/tool_result_compressor.rs

# 最大工具结果字符数 (约 20 万,防止 prompt 超长)
MAX_TOOL_RESULT_CHARS = 200_000

# 浏览器快照检测阈值
SNAPSHOT_DETECTION_THRESHOLD = 20_000

# 浏览器快照压缩后的最大字符数
SNAPSHOT_MAX_CHARS = 16_000

# 浏览器快照头部保留比例
SNAPSHOT_HEAD_RATIO = 0.7

# 浏览器快照尾部保留比例
SNAPSHOT_TAIL_RATIO = 0.4  # 用户要求 40%（AM 原为 0.3）

# 普通压缩头部保留比例
COMPRESS_HEAD_RATIO = 0.7  # 用户要求同步为 70%（原为 40%）

# 普通压缩尾部保留比例
COMPRESS_TAIL_RATIO = 0.4  # 用户要求保留 40%（AM 为 30%）


# ====================== Token 估算 ======================

def _count_tokens_tiktoken(text: str) -> int:
    """使用 tiktoken 精确计算 token 数量"""
    if not text or not TIKTOKEN_AVAILABLE or _TIKTOKEN_ENCODER is None:
        return 0
    try:
        return len(_TIKTOKEN_ENCODER.encode(text))
    except Exception:
        # 编码失败时回退到字符估算
        return len(text) // CHARS_PER_TOKEN


def _count_tokens_fallback(text: str, is_tool_result: bool = False) -> int:
    """字符估算模式（tiktoken 不可用时的回退方案）"""
    if not text:
        return 0
    chars_per_token = TOOL_RESULT_CHARS_PER_TOKEN if is_tool_result else CHARS_PER_TOKEN
    return max(1, len(text) // chars_per_token)


def estimate_message_tokens(message: Any) -> int:
    """
    估算单条消息的 token 数量
    
    [FIX 2026-01-10] 优先使用 tiktoken 精确计算，不可用时回退到字符估算
    
    Args:
        message: OpenAI 格式的消息对象或字典
        
    Returns:
        估算的 token 数量
    """
    total_tokens = 0
    use_tiktoken = TIKTOKEN_AVAILABLE and _TIKTOKEN_ENCODER is not None
    
    # 提取 role
    if hasattr(message, "role"):
        role = getattr(message, "role", "user")
        content = getattr(message, "content", "")
        tool_calls = getattr(message, "tool_calls", None)
        tool_call_id = getattr(message, "tool_call_id", None)
    elif isinstance(message, dict):
        role = message.get("role", "user")
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")
        tool_call_id = message.get("tool_call_id")
    else:
        return 10  # 未知格式，返回最小估算值
    
    is_tool_result = (role == "tool" or tool_call_id is not None)
    
    # 计算 content 的 token 数
    if isinstance(content, str):
        if use_tiktoken:
            total_tokens += _count_tokens_tiktoken(content)
        else:
            total_tokens += _count_tokens_fallback(content, is_tool_result)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if use_tiktoken:
                        total_tokens += _count_tokens_tiktoken(text)
                    else:
                        total_tokens += _count_tokens_fallback(text, is_tool_result)
                elif item.get("type") == "thinking":
                    thinking = item.get("thinking", "")
                    if use_tiktoken:
                        total_tokens += _count_tokens_tiktoken(thinking)
                    else:
                        total_tokens += _count_tokens_fallback(thinking, is_tool_result)
                elif item.get("type") == "image_url":
                    # 图片 token 估算：每张图片约 1K tokens
                    total_tokens += 1000
            elif isinstance(item, str):
                if use_tiktoken:
                    total_tokens += _count_tokens_tiktoken(item)
                else:
                    total_tokens += _count_tokens_fallback(item, is_tool_result)
    
    # 计算 tool_calls 的 token 数
    if tool_calls:
        for tc in tool_calls:
            if hasattr(tc, "function"):
                func = getattr(tc, "function", None)
                if func:
                    name = getattr(func, "name", "") or ""
                    args = getattr(func, "arguments", "") or ""
                    if use_tiktoken:
                        total_tokens += _count_tokens_tiktoken(name)
                        total_tokens += _count_tokens_tiktoken(args)
                    else:
                        total_tokens += _count_tokens_fallback(name)
                        total_tokens += _count_tokens_fallback(args)
            elif isinstance(tc, dict):
                func = tc.get("function", {})
                name = func.get("name", "")
                args = func.get("arguments", "")
                if use_tiktoken:
                    total_tokens += _count_tokens_tiktoken(name)
                    total_tokens += _count_tokens_tiktoken(args)
                else:
                    total_tokens += _count_tokens_fallback(name)
                    total_tokens += _count_tokens_fallback(args)
    
    # 返回计算的 token 数（至少为 1）
    return max(1, total_tokens)


def estimate_messages_tokens(messages: List[Any]) -> int:
    """
    估算消息列表的总 token 数量
    
    Args:
        messages: OpenAI 格式的消息列表
        
    Returns:
        估算的总 token 数量
    """
    total = 0
    for msg in messages:
        total += estimate_message_tokens(msg)
    return total


# ====================== 消息分类 ======================

def classify_messages(messages: List[Any]) -> Dict[str, List[Tuple[int, Any]]]:
    """
    将消息分类为不同类型
    
    Args:
        messages: OpenAI 格式的消息列表
        
    Returns:
        分类后的消息字典：
        {
            "system": [(index, message), ...],
            "tool_context": [(index, message), ...],  # 工具调用相关消息
            "regular": [(index, message), ...],  # 普通对话消息
        }
    """
    result = {
        "system": [],
        "tool_context": [],
        "regular": [],
    }
    
    # 第一遍：找出所有工具调用相关的消息索引
    tool_related_indices = set()
    
    for i, msg in enumerate(messages):
        if hasattr(msg, "role"):
            role = getattr(msg, "role", "")
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", None)
        elif isinstance(msg, dict):
            role = msg.get("role", "")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
        else:
            continue
        
        # 系统消息
        if role == "system":
            result["system"].append((i, msg))
            continue
        
        # 工具相关消息
        if role == "tool" or tool_call_id or tool_calls:
            tool_related_indices.add(i)
            # 如果是工具结果，向前查找对应的工具调用
            if role == "tool" or tool_call_id:
                # 向前查找最近的 assistant 消息（包含 tool_calls）
                for j in range(i - 1, -1, -1):
                    prev_msg = messages[j]
                    prev_role = getattr(prev_msg, "role", None) or (prev_msg.get("role") if isinstance(prev_msg, dict) else None)
                    prev_tool_calls = getattr(prev_msg, "tool_calls", None) or (prev_msg.get("tool_calls") if isinstance(prev_msg, dict) else None)
                    if prev_role == "assistant" and prev_tool_calls:
                        tool_related_indices.add(j)
                        break
    
    # 第二遍：分类消息
    for i, msg in enumerate(messages):
        if hasattr(msg, "role"):
            role = getattr(msg, "role", "")
        elif isinstance(msg, dict):
            role = msg.get("role", "")
        else:
            result["regular"].append((i, msg))
            continue
        
        if role == "system":
            continue  # 已处理
        elif i in tool_related_indices:
            result["tool_context"].append((i, msg))
        else:
            result["regular"].append((i, msg))
    
    return result


# ====================== 消息截断策略 ======================

def truncate_messages_smart(
    messages: List[Any],
    target_tokens: int = TARGET_TOKEN_LIMIT,
    min_keep: int = MIN_KEEP_MESSAGES,
    protect_tool_rounds: int = TOOL_CONTEXT_PROTECT_ROUNDS,
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    智能截断消息列表，保持对话连贯性
    
    策略：
    1. 始终保留 system 消息
    2. 保留最近 N 轮的工具调用上下文
    3. 从最旧的普通消息开始删除
    4. 确保至少保留 min_keep 条消息
    
    Args:
        messages: OpenAI 格式的消息列表
        target_tokens: 目标 token 数量上限
        min_keep: 最小保留消息数
        protect_tool_rounds: 保护最近 N 轮工具调用
        
    Returns:
        (truncated_messages, stats)
        - truncated_messages: 截断后的消息列表
        - stats: 截断统计信息
    """
    original_count = len(messages)
    original_tokens = estimate_messages_tokens(messages)
    
    # 如果不需要截断，直接返回
    if original_tokens <= target_tokens:
        return messages, {
            "truncated": False,
            "original_count": original_count,
            "final_count": original_count,
            "original_tokens": original_tokens,
            "final_tokens": original_tokens,
            "removed_count": 0,
        }
    
    log.warning(f"[CONTEXT TRUNCATION] Starting truncation: {original_tokens:,} tokens -> target {target_tokens:,} tokens")
    
    # 分类消息
    classified = classify_messages(messages)
    
    # 必须保留的消息索引
    must_keep_indices = set()
    
    # 1. 保留所有 system 消息
    for idx, _ in classified["system"]:
        must_keep_indices.add(idx)
    
    # 2. 保留最近的工具调用上下文
    tool_messages = classified["tool_context"]
    if tool_messages and protect_tool_rounds > 0:
        # 找出最近 N 轮工具调用
        # 一轮 = 一个 assistant(tool_calls) + 对应的 tool results + assistant(response)
        recent_tool_indices = set()
        rounds_counted = 0
        
        # 从后向前遍历工具消息
        for idx, msg in reversed(tool_messages):
            recent_tool_indices.add(idx)
            # 如果是 tool 消息，计为一轮的一部分
            role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
            if role == "tool":
                rounds_counted += 0.5  # tool 消息算半轮
            elif role == "assistant":
                rounds_counted += 0.5  # assistant 消息算半轮
            
            if rounds_counted >= protect_tool_rounds:
                break
        
        must_keep_indices.update(recent_tool_indices)
    
    # 3. 保留最近的普通消息
    regular_messages = classified["regular"]
    # 从后向前保留，直到满足 min_keep
    kept_regular_count = 0
    for idx, _ in reversed(regular_messages):
        if idx not in must_keep_indices:
            must_keep_indices.add(idx)
            kept_regular_count += 1
            if kept_regular_count >= min_keep:
                break
    
    # 计算当前保留消息的 token 数
    current_tokens = 0
    for idx in must_keep_indices:
        if idx < len(messages):
            current_tokens += estimate_message_tokens(messages[idx])
    
    # 4. 如果还有空间，继续从后向前添加更多消息
    remaining_budget = target_tokens - current_tokens
    
    if remaining_budget > 0:
        # 所有未添加的消息索引，按倒序排列（优先保留最近的）
        all_indices = set(range(len(messages)))
        remaining_indices = sorted(all_indices - must_keep_indices, reverse=True)
        
        for idx in remaining_indices:
            msg_tokens = estimate_message_tokens(messages[idx])
            if msg_tokens <= remaining_budget:
                must_keep_indices.add(idx)
                remaining_budget -= msg_tokens
    
    # 构建截断后的消息列表（保持原始顺序）
    truncated = []
    for i in range(len(messages)):
        if i in must_keep_indices:
            truncated.append(messages[i])
    
    # 统计信息
    final_tokens = estimate_messages_tokens(truncated)
    stats = {
        "truncated": True,
        "original_count": original_count,
        "final_count": len(truncated),
        "original_tokens": original_tokens,
        "final_tokens": final_tokens,
        "removed_count": original_count - len(truncated),
        "system_kept": len(classified["system"]),
        "tool_context_kept": len([i for i, _ in classified["tool_context"] if i in must_keep_indices]),
    }
    
    log.info(f"[CONTEXT TRUNCATION] Truncation complete: "
             f"{original_count} -> {len(truncated)} messages, "
             f"{original_tokens:,} -> {final_tokens:,} tokens, "
             f"removed {stats['removed_count']} messages")
    
    return truncated, stats


def truncate_messages_aggressive(
    messages: List[Any],
    target_tokens: int = TARGET_TOKEN_LIMIT // 2,  # 更激进的目标
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    激进截断策略 - 用于 MAX_TOKENS 错误后的重试
    
    只保留：
    1. System 消息
    2. 最近 2 条用户消息
    3. 最近的工具调用上下文（如果有）
    
    Args:
        messages: OpenAI 格式的消息列表
        target_tokens: 目标 token 数量（更低的值）
        
    Returns:
        (truncated_messages, stats)
    """
    original_count = len(messages)
    original_tokens = estimate_messages_tokens(messages)
    
    truncated = []
    
    # 1. 保留 system 消息
    for msg in messages:
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role == "system":
            truncated.append(msg)
    
    # 2. 找到最后一个用户消息及其之后的所有消息
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        role = getattr(messages[i], "role", None) or (messages[i].get("role") if isinstance(messages[i], dict) else None)
        if role == "user":
            # 检查是否是工具结果消息
            tool_call_id = getattr(messages[i], "tool_call_id", None) or (messages[i].get("tool_call_id") if isinstance(messages[i], dict) else None)
            if not tool_call_id:  # 不是工具结果的用户消息
                last_user_idx = i
                break
    
    if last_user_idx >= 0:
        # 保留最后一个用户消息及其之后的所有消息
        for i in range(last_user_idx, len(messages)):
            msg = messages[i]
            role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
            if role != "system":  # system 已添加
                truncated.append(msg)
    
    # 3. 如果还有工具调用上下文在最后一个用户消息之前，检查是否需要保留
    # 查找最后一个用户消息之前最近的工具调用链
    if last_user_idx > 0:
        tool_chain_start = -1
        for i in range(last_user_idx - 1, -1, -1):
            msg = messages[i]
            role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
            tool_calls = getattr(msg, "tool_calls", None) or (msg.get("tool_calls") if isinstance(msg, dict) else None)
            tool_call_id = getattr(msg, "tool_call_id", None) or (msg.get("tool_call_id") if isinstance(msg, dict) else None)
            
            if role == "tool" or tool_call_id or tool_calls:
                tool_chain_start = i
            elif tool_chain_start >= 0:
                # 找到工具链的起点
                break
        
        # 如果找到了工具链，检查是否需要保留
        if tool_chain_start >= 0:
            tool_chain = messages[tool_chain_start:last_user_idx]
            tool_chain_tokens = estimate_messages_tokens(tool_chain)
            current_tokens = estimate_messages_tokens(truncated)
            
            if current_tokens + tool_chain_tokens <= target_tokens:
                # 有足够空间，在正确位置插入工具链
                # 找到 truncated 中 system 消息之后的位置
                insert_pos = 0
                for i, msg in enumerate(truncated):
                    role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
                    if role == "system":
                        insert_pos = i + 1
                truncated[insert_pos:insert_pos] = tool_chain
    
    final_tokens = estimate_messages_tokens(truncated)
    stats = {
        "truncated": True,
        "aggressive": True,
        "original_count": original_count,
        "final_count": len(truncated),
        "original_tokens": original_tokens,
        "final_tokens": final_tokens,
        "removed_count": original_count - len(truncated),
    }
    
    log.warning(f"[CONTEXT TRUNCATION] Aggressive truncation: "
                f"{original_count} -> {len(truncated)} messages, "
                f"{original_tokens:,} -> {final_tokens:,} tokens")
    
    return truncated, stats


# ====================== 工具结果压缩 ======================

# ================== [FIX 2026-01-15] AM兼容智能压缩 ==================
# 同步自 Antigravity-Manager/src-tauri/src/proxy/mappers/tool_result_compressor.rs

import re as _re

def deep_clean_html(html: str) -> str:
    """
    [FIX 2026-01-15] 深度清理 HTML (移除 style, script, base64 等)
    
    同步自 AM tool_result_compressor.rs:deep_clean_html
    
    Args:
        html: 原始 HTML 内容
        
    Returns:
        清理后的 HTML
    """
    result = html
    
    # 1. 移除 <style>...</style> 及其内容
    result = _re.sub(r'(?is)<style\b[^>]*>.*?</style>', '[style omitted]', result)
    
    # 2. 移除 <script>...</script> 及其内容
    result = _re.sub(r'(?is)<script\b[^>]*>.*?</script>', '[script omitted]', result)
    
    # 3. 移除 inline Base64 数据 (如 src="data:image/png;base64,...")
    result = _re.sub(r'data:[^;/]+/[^;]+;base64,[A-Za-z0-9+/=]+', '[base64 omitted]', result)
    
    # 4. 移除 SVG 内容 (通常很长)
    result = _re.sub(r'(?is)<svg\b[^>]*>.*?</svg>', '[svg omitted]', result)
    
    # 5. 移除冗余的空白字符
    result = _re.sub(r'\n\s*\n', '\n', result)
    
    return result


def is_browser_snapshot(text: str) -> bool:
    """
    [FIX 2026-01-15] 检测是否是浏览器快照
    
    同步自 AM tool_result_compressor.rs:compact_browser_snapshot 检测逻辑
    
    Args:
        text: 待检测的文本
        
    Returns:
        是否是浏览器快照
    """
    lower = text.lower()
    return (
        'page snapshot' in lower
        or '页面快照' in text
        or text.count('ref=') > 30
        or text.count('[ref=') > 30
    )


def compact_saved_output_notice(text: str, max_chars: int) -> Optional[str]:
    """
    [FIX 2026-01-15] 压缩"输出已保存到文件"类型的提示
    
    同步自 AM tool_result_compressor.rs:compact_saved_output_notice
    
    检测模式: "result (N characters) exceeds maximum allowed tokens. Output saved to <path>"
    策略: 提取关键信息(文件路径、字符数、格式说明)
    
    Args:
        text: 原始文本
        max_chars: 最大字符数
        
    Returns:
        压缩后的文本，如果不是保存输出模式则返回 None
    """
    pattern = r'(?i)result\s*\(\s*(?P<count>[\d,]+)\s*characters\s*\)\s*exceeds\s+maximum\s+allowed\s+tokens\.\s*Output\s+(?:has\s+been\s+)?saved\s+to\s+(?P<path>[^\r\n]+)'
    
    match = _re.search(pattern, text)
    if not match:
        return None
    
    count = match.group('count')
    raw_path = match.group('path')
    
    # 清理文件路径
    file_path = raw_path.strip().rstrip(')]\"\'.').strip()
    
    # 提取关键行
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    # 查找通知行
    notice_line = None
    for line in lines:
        if 'exceeds maximum allowed tokens' in line.lower() and 'saved to' in line.lower():
            notice_line = line
            break
    
    if not notice_line:
        notice_line = f"result ({count} characters) exceeds maximum allowed tokens. Output has been saved to {file_path}"
    
    # 查找格式说明行
    format_line = None
    for line in lines:
        if line.startswith('Format:') or 'JSON array with schema' in line or line.lower().startswith('schema:'):
            format_line = line
            break
    
    # 构建压缩后的输出
    compact_lines = [notice_line]
    if format_line and format_line != notice_line:
        compact_lines.append(format_line)
    compact_lines.append(f"[tool_result omitted to reduce prompt size; read file locally if needed: {file_path}]")
    
    result = '\n'.join(compact_lines)
    return result[:max_chars] if len(result) > max_chars else result


def compact_browser_snapshot(text: str, max_chars: int) -> Optional[str]:
    """
    [FIX 2026-01-15] 压缩浏览器快照 (头+尾保留策略)
    
    同步自 AM tool_result_compressor.rs:compact_browser_snapshot
    
    检测: "page snapshot" 或 "页面快照" 或大量 "ref=" 引用
    策略: 保留头部 70% + 尾部 40%,中间省略
    
    Args:
        text: 原始文本
        max_chars: 最大字符数
        
    Returns:
        压缩后的文本，如果不是浏览器快照则返回 None
    """
    if not is_browser_snapshot(text):
        return None
    
    desired_max = min(max_chars, SNAPSHOT_MAX_CHARS)
    if desired_max < 2000 or len(text) <= desired_max:
        return None
    
    meta = f"[page snapshot summarized to reduce prompt size; original {len(text):,} chars]"
    overhead = len(meta) + 200
    budget = desired_max - overhead
    
    if budget < 1000:
        return None
    
    # 计算头部和尾部长度
    head_len = min(int(budget * SNAPSHOT_HEAD_RATIO), 10000)
    head_len = max(head_len, 500)
    tail_len = min(budget - head_len, 3000)
    
    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 and len(text) > head_len else ""
    
    omitted = len(text) - head_len - tail_len
    
    if tail:
        return f"{meta}\n---[HEAD]---\n{head}\n---[...omitted {omitted:,} chars]---\n---[TAIL]---\n{tail}"
    else:
        return f"{meta}\n---[HEAD]---\n{head}\n---[...omitted {omitted:,} chars]---"


def compress_tool_result(content: str, max_length: int = None) -> str:
    """
    [FIX 2026-01-15] 智能压缩工具结果内容
    
    同步自 AM tool_result_compressor.rs:compact_tool_result_text
    
    根据内容类型自动选择最佳压缩策略:
    1. HTML 预清理 → 移除 style, script, base64, svg
    2. 大文件提示 → 提取关键信息
    3. 浏览器快照 → 头 70% + 尾 40% 保留
    4. 普通截断 → 头 70% + 尾 40% 保留
    
    Args:
        content: 工具结果内容
        max_length: 最大保留长度 (默认 200,000)
        
    Returns:
        压缩后的内容
    """
    if max_length is None:
        max_length = MAX_TOOL_RESULT_CHARS
    
    if not content or len(content) <= max_length:
        return content
    
    original_len = len(content)
    
    # 1. [NEW] 针对可能的 HTML 内容进行深度预处理
    if '<html' in content or '<body' in content or '<!DOCTYPE' in content:
        content = deep_clean_html(content)
        if len(content) != original_len:
            log.debug(f"[TOOL COMPRESS] Deep cleaned HTML: {original_len:,} -> {len(content):,} chars")
        if len(content) <= max_length:
            return content
    
    # 2. 检测大文件提示模式
    compacted = compact_saved_output_notice(content, max_length)
    if compacted:
        log.debug(f"[TOOL COMPRESS] Detected saved output notice, compacted to {len(compacted):,} chars")
        return compacted
    
    # 3. 检测浏览器快照模式
    if len(content) > SNAPSHOT_DETECTION_THRESHOLD:
        compacted = compact_browser_snapshot(content, max_length)
        if compacted:
            log.debug(f"[TOOL COMPRESS] Detected browser snapshot, compacted to {len(compacted):,} chars")
            return compacted
    
    # 4. 普通截断 (头 70% + 尾 40%，总和超过 100% 时按比例调整)
    head_ratio = COMPRESS_HEAD_RATIO
    tail_ratio = COMPRESS_TAIL_RATIO
    total_ratio = head_ratio + tail_ratio
    
    # 如果总比例超过 100%，按比例缩放
    if total_ratio > 1.0:
        head_ratio = head_ratio / total_ratio
        tail_ratio = tail_ratio / total_ratio
    
    head_len = int(max_length * head_ratio)
    tail_len = int(max_length * tail_ratio)
    
    truncation_notice = (
        f"\n\n[... Content truncated: {len(content) - head_len - tail_len:,} characters removed. "
        f"Original length: {len(content):,} characters ...]\n\n"
    )
    
    result = content[:head_len] + truncation_notice + content[-tail_len:]
    log.debug(f"[TOOL COMPRESS] Normal truncation: {len(content):,} -> {len(result):,} chars")
    
    return result


def compress_tool_results_in_messages(
    messages: List[Any],
    max_result_length: int = None,  # [FIX 2026-01-15] 默认使用 MAX_TOOL_RESULT_CHARS (200K)
) -> Tuple[List[Any], int]:
    """
    压缩消息列表中的工具结果
    
    [FIX 2026-01-15] 默认限制从 5K 提升到 200K (AM 兼容)
    
    Args:
        messages: OpenAI 格式的消息列表
        max_result_length: 单个工具结果的最大长度 (默认 200,000)
        
    Returns:
        (compressed_messages, chars_saved)
    """
    if max_result_length is None:
        max_result_length = MAX_TOOL_RESULT_CHARS
    
    compressed = []
    total_saved = 0
    
    for msg in messages:
        # 获取消息属性
        if hasattr(msg, "role"):
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", "")
            tool_call_id = getattr(msg, "tool_call_id", None)
        elif isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_call_id = msg.get("tool_call_id")
        else:
            compressed.append(msg)
            continue
        
        # 只处理工具结果消息
        if role == "tool" or tool_call_id:
            if isinstance(content, str) and len(content) > max_result_length:
                original_len = len(content)
                compressed_content = compress_tool_result(content, max_result_length)
                
                # 创建新消息
                if hasattr(msg, "role"):
                    from src.models import OpenAIChatMessage
                    new_msg = OpenAIChatMessage(
                        role=role,
                        content=compressed_content,
                        tool_call_id=tool_call_id,
                        name=getattr(msg, "name", None),
                    )
                else:
                    new_msg = msg.copy()
                    new_msg["content"] = compressed_content
                
                compressed.append(new_msg)
                total_saved += original_len - len(compressed_content)
                log.debug(f"[TOOL COMPRESS] Compressed tool result: {original_len:,} -> {len(compressed_content):,} chars")
                continue
        
        compressed.append(msg)
    
    if total_saved > 0:
        log.info(f"[TOOL COMPRESS] Total saved: {total_saved:,} characters from tool results")
    
    return compressed, total_saved


# ====================== 综合截断函数 ======================

def truncate_context_for_api(
    messages: List[Any],
    target_tokens: int = TARGET_TOKEN_LIMIT,
    compress_tools: bool = True,
    tool_max_length: int = 5000,
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    为 API 请求截断上下文
    
    综合使用多种策略：
    1. 先压缩工具结果
    2. 再智能截断消息
    
    Args:
        messages: OpenAI 格式的消息列表
        target_tokens: 目标 token 数量
        compress_tools: 是否压缩工具结果
        tool_max_length: 工具结果最大长度
        
    Returns:
        (truncated_messages, stats)
    """
    stats = {
        "original_messages": len(messages),
        "original_tokens": estimate_messages_tokens(messages),
    }
    
    # Step 1: 压缩工具结果
    if compress_tools:
        messages, chars_saved = compress_tool_results_in_messages(messages, tool_max_length)
        stats["tool_chars_saved"] = chars_saved
        stats["after_tool_compress_tokens"] = estimate_messages_tokens(messages)
    
    # Step 2: 检查是否需要截断
    current_tokens = estimate_messages_tokens(messages)
    if current_tokens <= target_tokens:
        stats["truncated"] = False
        stats["final_messages"] = len(messages)
        stats["final_tokens"] = current_tokens
        return messages, stats
    
    # Step 3: 智能截断
    truncated, truncation_stats = truncate_messages_smart(
        messages,
        target_tokens=target_tokens,
    )
    
    stats.update(truncation_stats)
    stats["final_messages"] = len(truncated)
    stats["final_tokens"] = estimate_messages_tokens(truncated)
    
    return truncated, stats


# ====================== MAX_TOKENS 重试支持 ======================

def prepare_retry_after_max_tokens(
    messages: List[Any],
    previous_tokens: int = 0,
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    在 MAX_TOKENS 错误后准备重试
    
    使用激进截断策略，大幅减少上下文
    
    Args:
        messages: 原始消息列表
        previous_tokens: 上次请求的实际 token 数（来自 API 响应）
        
    Returns:
        (truncated_messages, stats)
    """
    # 根据上次的实际 token 数计算新的目标
    if previous_tokens > 0:
        # 减少到上次的 50%
        target = previous_tokens // 2
    else:
        # 使用默认激进目标
        target = TARGET_TOKEN_LIMIT // 2
    
    return truncate_messages_aggressive(messages, target_tokens=target)

# ====================== 智能预防性截断 ======================

def smart_preemptive_truncation(
    messages: List[Any],
    max_output_tokens: int = 16384,
    api_context_limit: int = 128000,
    safety_margin: float = 0.85,
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    智能预防性截断 - 根据 API 限制和预期输出动态调整
    
    [FIX 2026-01-10] 增强版截断策略：
    - 考虑预期输出 token 数
    - 动态计算安全的输入 token 上限
    - 提供更详细的截断统计
    
    Args:
        messages: OpenAI 格式的消息列表
        max_output_tokens: 预期的最大输出 token 数
        api_context_limit: API 的总上下文限制
        safety_margin: 安全边际系数（默认 85%）
        
    Returns:
        (truncated_messages, stats)
    """
    # 计算安全的输入 token 上限
    # 公式：safe_input = (api_limit * safety_margin) - max_output
    safe_input_limit = int(api_context_limit * safety_margin) - max_output_tokens
    safe_input_limit = max(safe_input_limit, 10000)  # 至少保留 10K tokens
    
    current_tokens = estimate_messages_tokens(messages)
    
    stats = {
        "api_context_limit": api_context_limit,
        "max_output_tokens": max_output_tokens,
        "safe_input_limit": safe_input_limit,
        "original_tokens": current_tokens,
        "truncated": False,
    }
    
    if current_tokens <= safe_input_limit:
        stats["final_tokens"] = current_tokens
        stats["action"] = "none"
        return messages, stats
    
    # 需要截断
    log.warning(f"[SMART TRUNCATION] 需要截断: {current_tokens:,} tokens > {safe_input_limit:,} 安全限制 "
               f"(API限制={api_context_limit:,}, 预期输出={max_output_tokens:,})")
    
    # 首先尝试普通截断
    truncated, truncation_stats = truncate_context_for_api(
        messages,
        target_tokens=safe_input_limit,
        compress_tools=True,
        tool_max_length=5000,
    )
    
    final_tokens = estimate_messages_tokens(truncated)
    
    # 如果普通截断不够，使用激进截断
    if final_tokens > safe_input_limit:
        log.warning(f"[SMART TRUNCATION] 普通截断不足 ({final_tokens:,} > {safe_input_limit:,})，使用激进截断")
        truncated, aggressive_stats = truncate_messages_aggressive(
            messages,
            target_tokens=safe_input_limit,
        )
        final_tokens = estimate_messages_tokens(truncated)
        stats["action"] = "aggressive"
        stats["aggressive_stats"] = aggressive_stats
    else:
        stats["action"] = "normal"
        stats["truncation_stats"] = truncation_stats
    
    stats["truncated"] = True
    stats["final_tokens"] = final_tokens
    stats["tokens_removed"] = current_tokens - final_tokens
    
    log.info(f"[SMART TRUNCATION] 截断完成: {current_tokens:,} -> {final_tokens:,} tokens "
            f"(移除 {stats['tokens_removed']:,}, 策略={stats['action']})")
    
    return truncated, stats


def should_retry_with_aggressive_truncation(
    finish_reason: str,
    output_tokens: int,
    retry_count: int = 0,
    max_retries: int = 1,
) -> bool:
    """
    判断是否应该使用激进截断策略重试
    
    Args:
        finish_reason: API 返回的 finish_reason
        output_tokens: 实际输出的 token 数
        retry_count: 当前重试次数
        max_retries: 最大重试次数
        
    Returns:
        是否应该重试
    """
    # 已达到最大重试次数
    if retry_count >= max_retries:
        log.warning(f"[RETRY CHECK] 已达到最大重试次数 ({max_retries})，不再重试")
        return False
    
    # 检查是否因为 MAX_TOKENS 被截断
    if finish_reason != "MAX_TOKENS" and finish_reason != "length":
        return False
    
    # 如果输出 token 很少（<1000），可能是输入太长导致的
    # 这种情况值得重试
    if output_tokens < 1000:
        log.info(f"[RETRY CHECK] 输出 token 很少 ({output_tokens})，建议使用激进截断重试")
        return True
    
    # 如果输出 token 接近上限（>4000），说明是正常的输出截断
    # 这种情况重试意义不大
    if output_tokens >= 4000:
        log.debug(f"[RETRY CHECK] 输出 token 接近上限 ({output_tokens})，不建议重试")
        return False
    
    return False


