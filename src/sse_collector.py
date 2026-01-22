"""
SSE 收集器 - 将 SSE 流转换为完整的 JSON 响应

此模块是 Auto-Stream Conversion 功能的核心组件。

背景说明：
Google API 对流式请求 (streamGenerateContent) 的配额限制比非流式请求 (generateContent) 宽松得多。
通过在代理层将非流式请求自动转换为流式请求，可以显著减少 429 错误。

工作原理：
1. 代理层将非流式请求转换为流式请求发送给 Google API
2. 接收 SSE (Server-Sent Events) 格式的响应流
3. 本模块负责收集完整的 SSE 流并重组为 JSON 格式
4. 返回与原生 generateContent 相同格式的响应给客户端

参考实现：
- Antigravity_Tools/src-tauri/src/proxy/mappers/claude/collector.rs
- Antigravity_Tools/src-tauri/src/proxy/mappers/openai/collector.rs

作者：Auto-Stream Conversion 功能移植
日期：2026-01-11
更新：2026-01-17 - 增强容错能力和日志
"""

import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from log import log


async def collect_sse_to_json(
    lines: AsyncIterator[Union[str, bytes]],
    *,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    将 Antigravity SSE 流收集并转换为完整的 JSON 响应。

    SSE 事件格式示例：
        data: {"candidates":[{"content":{"parts":[{"text":"Hello"}],"role":"model"}}]}
        data: {"candidates":[{"content":{"parts":[{"text":" World"}],"role":"model"}}]}
        data: {"candidates":[{"finishReason":"STOP","content":{"parts":[{"text":"!"}]}}]}

    Args:
        lines: 异步迭代器，产生 SSE 事件行
        debug: 是否启用调试日志

    Returns:
        完整的 JSON 响应，格式与 generateContent 一致
    """
    # ✅ [FIX 2026-01-17] 添加统计信息用于调试
    start_time = time.time()
    event_count = 0
    error_count = 0
    last_event_time = start_time

    # 累积的响应数据
    accumulated_text: List[str] = []
    accumulated_parts: List[Dict[str, Any]] = []

    # 元数据
    finish_reason: Optional[str] = None
    usage_metadata: Dict[str, Any] = {}
    model_version: Optional[str] = None

    # 工具调用累积
    pending_function_calls: List[Dict[str, Any]] = []

    # 思维链累积
    thinking_parts: List[Dict[str, Any]] = []
    current_thinking_text: List[str] = []
    current_thinking_signature: Optional[str] = None
    in_thinking_block: bool = False

    try:
        async for line in lines:
            last_event_time = time.time()
            
            # ✅ [FIX 2026-01-22] 修复类型错误：处理 bytes 和 str 两种类型
            # response.aiter_lines() 可能返回 bytes 或 str，需要统一处理
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="ignore")
            
            line = line.strip()

            # 跳过空行和非 data 行
            if not line or not line.startswith("data:"):
                continue

            event_count += 1

            # 提取 JSON 数据
            data_str = line[5:].strip()  # 移除 "data:" 前缀

            # 跳过 [DONE] 标记
            if data_str == "[DONE]":
                if debug:
                    log.debug("[SSE_COLLECTOR] Received [DONE] marker")
                break

            # 解析 JSON
            try:
                event_data = json.loads(data_str)
            except json.JSONDecodeError as e:
                error_count += 1
                if debug:
                    log.warning(f"[SSE_COLLECTOR] Failed to parse SSE event: {e}, data: {data_str[:100]}")
                continue

            if debug:
                log.debug(f"[SSE_COLLECTOR] Processing event #{event_count}: {json.dumps(event_data)[:200]}")

            # ✅ [FIX 2026-01-17] 修复：Antigravity SSE 事件格式是 {"response": {"candidates": [...]}}
            # 需要先提取 response，再提取 candidates
            response_obj = event_data.get("response", {})
            candidates = response_obj.get("candidates", [])
            
            # 如果没有 response 包装，尝试直接从顶层获取（向后兼容）
            if not candidates:
                candidates = event_data.get("candidates", [])
            
            if not candidates:
                # 可能是 usageMetadata 更新
                usage_sources = [
                    event_data.get("usageMetadata"),
                    response_obj.get("usageMetadata"),
                    event_data.get("response", {}).get("usageMetadata") if isinstance(event_data.get("response"), dict) else None
                ]
                for usage_source in usage_sources:
                    if usage_source:
                        usage_metadata.update(usage_source)
                # ✅ [FIX 2026-01-17] 添加调试日志：记录没有 candidates 的事件
                if debug:
                    log.debug(f"[SSE_COLLECTOR] Event #{event_count} has no candidates, keys: {list(event_data.keys())}")
                continue

            candidate = candidates[0] if candidates else {}

            # 提取 finishReason
            if candidate.get("finishReason"):
                finish_reason = candidate["finishReason"]

            # 提取 modelVersion (可能在 response 或顶层)
            if response_obj.get("modelVersion"):
                model_version = response_obj["modelVersion"]
            elif event_data.get("modelVersion"):
                model_version = event_data["modelVersion"]

            # 提取 usageMetadata (可能在 candidate、response 或顶层)
            for usage_source in [
                candidate.get("usageMetadata"), 
                response_obj.get("usageMetadata"),
                event_data.get("usageMetadata")
            ]:
                if usage_source:
                    usage_metadata.update(usage_source)

            # 提取 content.parts
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            
            # ✅ [FIX 2026-01-17] 添加调试日志：记录空的 parts 情况
            if debug and not parts:
                log.debug(
                    f"[SSE_COLLECTOR] Event #{event_count} has empty parts. "
                    f"candidate keys: {list(candidate.keys())}, "
                    f"content keys: {list(content.keys()) if content else 'None'}, "
                    f"finishReason: {candidate.get('finishReason')}"
                )

            for part in parts:
                if not isinstance(part, dict):
                    continue

                # 处理思维链 (thought: true)
                if part.get("thought") is True:
                    text = part.get("text", "")
                    signature = part.get("thoughtSignature")

                    if not in_thinking_block:
                        in_thinking_block = True
                        current_thinking_text = []
                        current_thinking_signature = None

                    if text:
                        current_thinking_text.append(text)
                    if signature:
                        current_thinking_signature = signature
                    continue

                # 非思维链内容，先关闭当前思维块
                if in_thinking_block:
                    in_thinking_block = False
                    thinking_part = {
                        "thought": True,
                        "text": "".join(current_thinking_text),
                    }
                    if current_thinking_signature:
                        thinking_part["thoughtSignature"] = current_thinking_signature
                    thinking_parts.append(thinking_part)
                    current_thinking_text = []
                    current_thinking_signature = None

                # 处理普通文本
                if "text" in part:
                    accumulated_text.append(part["text"])
                    continue

                # 处理函数调用
                if "functionCall" in part:
                    pending_function_calls.append(part["functionCall"])
                    continue

                # 处理内联数据 (图片等)
                if "inlineData" in part:
                    accumulated_parts.append(part)
                    continue

                # 其他类型的 part 直接保留
                accumulated_parts.append(part)

    except Exception as e:
        # ✅ [FIX 2026-01-17] 增强错误日志
        elapsed = time.time() - start_time
        log.error(
            f"[SSE_COLLECTOR] Error during SSE collection: {e} "
            f"(events={event_count}, errors={error_count}, elapsed={elapsed:.2f}s)"
        )
        raise

    # 关闭最后的思维块
    if in_thinking_block and current_thinking_text:
        thinking_part = {
            "thought": True,
            "text": "".join(current_thinking_text),
        }
        if current_thinking_signature:
            thinking_part["thoughtSignature"] = current_thinking_signature
        thinking_parts.append(thinking_part)

    # 构建最终的 parts 列表
    final_parts: List[Dict[str, Any]] = []

    # 思维链放在最前面
    final_parts.extend(thinking_parts)

    # 累积的文本
    if accumulated_text:
        final_parts.append({"text": "".join(accumulated_text)})

    # 函数调用
    for fc in pending_function_calls:
        final_parts.append({"functionCall": fc})

    # 其他 parts
    final_parts.extend(accumulated_parts)

    # 构建完整响应 (与 generateContent 格式一致)
    response = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": final_parts,
                        "role": "model",
                    },
                    "finishReason": finish_reason or "STOP",
                }
            ],
        }
    }

    # 添加 usageMetadata
    if usage_metadata:
        response["response"]["usageMetadata"] = usage_metadata

    # 添加 modelVersion
    if model_version:
        response["response"]["modelVersion"] = model_version

    # ✅ [FIX 2026-01-17] 添加收集统计日志
    elapsed = time.time() - start_time
    text_len = len("".join(accumulated_text))
    thinking_len = sum(len(tp.get("text", "")) for tp in thinking_parts)

    # ✅ [FIX 2026-01-17] 检查是否有内容，如果没有内容则记录警告
    has_content = text_len > 0 or thinking_len > 0 or len(pending_function_calls) > 0 or len(accumulated_parts) > 0
    if not has_content and event_count > 0:
        log.warning(
            f"[SSE_COLLECTOR] ⚠️ WARNING: Collected {event_count} events but no content found! "
            f"(text={text_len}, thinking={thinking_len}, tools={len(pending_function_calls)}, "
            f"other_parts={len(accumulated_parts)}, finish_reason={finish_reason})"
        )
        # 在 debug 模式下，打印最终响应结构用于调试
        if debug:
            log.debug(f"[SSE_COLLECTOR] Final response structure: {json.dumps(response, indent=2)[:1000]}")

    if debug:
        log.debug(
            f"[SSE_COLLECTOR] Collection complete: events={event_count}, errors={error_count}, "
            f"elapsed={elapsed:.2f}s, text_len={text_len}, thinking_len={thinking_len}, "
            f"function_calls={len(pending_function_calls)}"
        )
        log.debug(f"[SSE_COLLECTOR] Final response: {json.dumps(response)[:500]}")
    else:
        # 即使不是 debug 模式，也记录基本统计信息（INFO 级别）
        log.info(
            f"[SSE_COLLECTOR] ✓ Collected {event_count} events in {elapsed:.2f}s "
            f"(text={text_len}, thinking={thinking_len}, tools={len(pending_function_calls)})"
        )

    return response


async def collect_sse_to_json_with_timeout(
    lines: AsyncIterator[Union[str, bytes]],
    *,
    timeout_seconds: float = 300.0,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    带超时的 SSE 收集。

    ✅ [FIX 2026-01-17] 增强超时处理：
    - 添加详细的超时日志
    - 正确传播 asyncio.TimeoutError 以便上层处理

    Args:
        lines: 异步迭代器
        timeout_seconds: 超时时间（秒），默认 300 秒（5 分钟）
        debug: 是否启用调试日志

    Returns:
        完整的 JSON 响应

    Raises:
        asyncio.TimeoutError: 当收集超时时抛出
        Exception: 当收集过程中发生其他错误时抛出
    """
    import asyncio

    start_time = time.time()

    if debug:
        log.debug(f"[SSE_COLLECTOR] Starting collection with timeout={timeout_seconds}s")

    try:
        result = await asyncio.wait_for(
            collect_sse_to_json(lines, debug=debug),
            timeout=timeout_seconds,
        )
        elapsed = time.time() - start_time
        if debug:
            log.debug(f"[SSE_COLLECTOR] Collection completed in {elapsed:.2f}s (timeout was {timeout_seconds}s)")
        return result
    except asyncio.TimeoutError:
        elapsed = time.time() - start_time
        log.error(
            f"[SSE_COLLECTOR] ⚠️ TIMEOUT after {elapsed:.2f}s (limit was {timeout_seconds}s) - "
            f"SSE stream did not complete in time. This may indicate the upstream is stuck or slow."
        )
        # 重新抛出 asyncio.TimeoutError 以便上层可以区分处理
        raise
    except Exception as e:
        elapsed = time.time() - start_time
        log.error(f"[SSE_COLLECTOR] Collection failed after {elapsed:.2f}s: {e}")
        raise
