"""
Converters Package - Antigravity API format converters
格式转换器包 - 处理 OpenAI/Gemini 与 Antigravity API 之间的格式转换

注意：响应转换函数（convert_antigravity_stream_to_openai 等）由于与路由逻辑紧密耦合，
暂时保留在 antigravity_router.py 中，后续可以进一步拆分。
"""

from .model_config import (
    model_mapping,
    get_fallback_models,
    should_fallback_on_error,
    is_thinking_model,
)

from .message_converter import (
    extract_images_from_content,
    strip_thinking_from_openai_messages,
    openai_messages_to_antigravity_contents,
    gemini_contents_to_antigravity_contents,
)

from .tool_converter import (
    extract_tool_params_summary,
    convert_openai_tools_to_antigravity,
    generate_generation_config,
    # 新增：工具格式验证函数
    validate_tool_name,
    validate_tool_parameters,
    validate_antigravity_tool,
    validate_tools_batch,
)

# [FIX 2026-01-11] 新增 gemini_fix 模块 - 上游同步
from .gemini_fix import (
    ALLOWED_PART_KEYS,
    clean_part_fields,
    clean_contents,
    normalize_gemini_request,
    check_last_assistant_has_thinking,
    is_thinking_model,
    is_search_model,
    get_base_model_name,
    get_thinking_settings,
)

__all__ = [
    # model_config
    "model_mapping",
    "get_fallback_models",
    "should_fallback_on_error",
    "is_thinking_model",
    # message_converter
    "extract_images_from_content",
    "strip_thinking_from_openai_messages",
    "openai_messages_to_antigravity_contents",
    "gemini_contents_to_antigravity_contents",
    # tool_converter
    "extract_tool_params_summary",
    "convert_openai_tools_to_antigravity",
    "generate_generation_config",
    # tool_converter - 验证函数
    "validate_tool_name",
    "validate_tool_parameters",
    "validate_antigravity_tool",
    "validate_tools_batch",
    # gemini_fix - [FIX 2026-01-11] 上游同步
    "ALLOWED_PART_KEYS",
    "clean_part_fields",
    "clean_contents",
    "normalize_gemini_request",
    "check_last_assistant_has_thinking",
    "is_search_model",
    "get_base_model_name",
    "get_thinking_settings",
]
