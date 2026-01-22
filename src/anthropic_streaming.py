from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator, Dict, Optional, List, Union

from log import log
from .signature_cache import cache_signature, cache_tool_signature, get_last_signature
from .openai_transfer import generate_tool_call_id
from .ssop import SSOPScanner
from .converters.thoughtSignature_fix import encode_tool_id_with_signature


def _sse_event(event: str, data: Dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

_DEBUG_TRUE = {"1", "true", "yes", "on"}

def _remove_nulls_for_tool_input(value: Any) -> Any:
    """
    递归移除 dict/list 中值为 null/None 的字段/元素。
    """
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in value.items():
            if v is None:
                continue
            cleaned[k] = _remove_nulls_for_tool_input(v)
        return cleaned

    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            if item is None:
                continue
            cleaned_list.append(_remove_nulls_for_tool_input(item))
        return cleaned_list

    return value


def _anthropic_debug_enabled() -> bool:
    return str(os.getenv("ANTHROPIC_DEBUG", "")).strip().lower() in _DEBUG_TRUE


class _StreamingState:
    def __init__(self, message_id: str, model: str):
        self.message_id = message_id
        self.model = model

        self._current_block_type: Optional[str] = None
        self._current_block_index: int = -1
        self._current_thinking_signature: Optional[str] = None
        self._current_thinking_text: str = ""  # 用于累积 thinking 文本，支持 signature 缓存

        # [FIX 2026-01-17] [CURSOR兼容] 持久化的最后一个 thinking 签名
        # 这是针对 Cursor 的修复：Cursor 在工具调用时会丢失签名，导致 400 Invalid signature 错误
        # 问题：当 functionCall 到来时，thinking 块可能已经被关闭，_current_thinking_signature 被重置为 None
        # 解决：在关闭 thinking 块时，将签名保存到 _last_thinking_signature，供后续 functionCall 使用
        self._last_thinking_signature: Optional[str] = None

        self.has_tool_use: bool = False
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.has_input_tokens: bool = False
        self.has_output_tokens: bool = False
        self.finish_reason: Optional[str] = None
        
        # SSOP Scanner for detecting tool calls in text/thought stream
        self.ssop_scanner = SSOPScanner()

    def _next_index(self) -> int:
        self._current_block_index += 1
        return self._current_block_index

    def close_block_if_open(self) -> Optional[bytes]:
        if self._current_block_type is None:
            return None

        # 在关闭 thinking 块时，将 signature 写入缓存
        if self._current_block_type == "thinking" and self._current_thinking_text:
            # [FIX 2026-01-17] 如果没有 signature，尝试从全局缓存获取
            effective_signature = self._current_thinking_signature
            if not effective_signature:
                try:
                    cached_sig = get_last_signature()
                    if cached_sig:
                        effective_signature = cached_sig
                        log.info(f"[STREAMING] close_block: Using cached last signature as fallback, sig_len={len(cached_sig)}")
                except Exception as e:
                    log.warning(f"[STREAMING] close_block: Failed to get last signature from cache: {e}")

            if effective_signature:
                # [FIX 2026-01-17] [CURSOR兼容] 在关闭 thinking 块时，保存签名到持久化变量
                # 这是针对 Cursor 的修复：确保后续的 functionCall 可以使用这个签名
                self._last_thinking_signature = effective_signature
                log.debug(f"[STREAMING] Saved thinking signature for later use: len={len(effective_signature)}")

                try:
                    success = cache_signature(
                        thinking_text=self._current_thinking_text,
                        signature=effective_signature,
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
        return event

    def open_text_block(self) -> bytes:
        idx = self._next_index()
        self._current_block_type = "text"
        return _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "text", "text": ""},
            },
        )

    def open_thinking_block(self, signature: Optional[str]) -> bytes:
        idx = self._next_index()
        self._current_block_type = "thinking"
        self._current_thinking_signature = signature
        self._current_thinking_text = ""  # 重置 thinking 文本累积器
        block: Dict[str, Any] = {"type": "thinking", "thinking": ""}
        if signature:
            block["signature"] = signature
        return _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": idx,
                "content_block": block,
            },
        )
        
    def emit_ssop_tool(self, tool_call: Dict[str, Any]) -> List[bytes]:
        """
        Emit a synthetic tool call detected by SSOP.
        Converts the OpenAI-style tool_call dict to Anthropic SSE events.
        """
        events = []
        
        # Close current block (text or thinking)
        stop_evt = self.close_block_if_open()
        if stop_evt:
            events.append(stop_evt)
            
        tool_id = tool_call["id"]
        tool_name = tool_call["function"]["name"]
        # Arguments are already a JSON string in the tool_call dict
        tool_args_str = tool_call["function"]["arguments"] 
        
        # 1. content_block_start (tool_use)
        idx = self._next_index()
        evt_start = _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": idx,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": {}, # Input is streamed via delta
                },
            },
        )
        
        # 2. content_block_delta (input_json_delta)
        # We send the full JSON in one delta for now, as SSOP detects complete blocks
        evt_delta = _sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": idx,
                "delta": {"type": "input_json_delta", "partial_json": tool_args_str},
            },
        )
        
        # 3. content_block_stop
        evt_stop = _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": idx},
        )
        
        events.extend([evt_start, evt_delta, evt_stop])
        
        self.has_tool_use = True
        return events


async def antigravity_sse_to_anthropic_sse(
    lines: AsyncIterator[Union[str, bytes]],
    *,
    model: str,
    message_id: str,
    initial_input_tokens: int = 0,
    credential_manager: Any = None,
    credential_name: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """
    将 Antigravity SSE（data: {...}）转换为 Anthropic Messages Streaming SSE。
    """
    state = _StreamingState(message_id=message_id, model=model)
    success_recorded = False
    message_start_sent = False
    pending_output: list[bytes] = []

    try:
        initial_input_tokens_int = max(0, int(initial_input_tokens or 0))
    except Exception:
        initial_input_tokens_int = 0

    def pick_usage_metadata(response: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        response_usage = response.get("usageMetadata", {}) or {}
        if not isinstance(response_usage, dict):
            response_usage = {}

        candidate_usage = candidate.get("usageMetadata", {}) or {}
        if not isinstance(candidate_usage, dict):
            candidate_usage = {}

        fields = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")

        def score(d: Dict[str, Any]) -> int:
            s = 0
            for f in fields:
                if f in d and d.get(f) is not None:
                    s += 1
            return s

        if score(candidate_usage) > score(response_usage):
            return candidate_usage
        return response_usage

    def enqueue(evt: bytes) -> None:
        pending_output.append(evt)

    def flush_pending_ready(ready: list[bytes]) -> None:
        if not pending_output:
            return
        ready.extend(pending_output)
        pending_output.clear()

    def send_message_start(ready: list[bytes], *, input_tokens: int) -> None:
        nonlocal message_start_sent
        if message_start_sent:
            return
        message_start_sent = True
        ready.append(
            _sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": int(input_tokens or 0), "output_tokens": 0},
                    },
                },
            )
        )
        flush_pending_ready(ready)

    try:
        async for line in lines:
            ready_output: list[bytes] = []
            
            # ✅ [FIX 2026-01-22] 修复类型错误：处理 bytes 和 str 两种类型
            # response.aiter_lines() 可能返回 bytes 或 str，需要统一处理
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="ignore")
            
            if not line or not line.startswith("data: "):
                continue

            raw = line[6:].strip()
            if raw == "[DONE]":
                break

            if not success_recorded and credential_manager and credential_name:
                await credential_manager.record_api_call_result(
                    credential_name, True, is_antigravity=True
                )
                success_recorded = True

            try:
                data = json.loads(raw)
            except Exception:
                continue

            response = data.get("response", {}) or {}
            candidate = (response.get("candidates", []) or [{}])[0] or {}
            parts = (candidate.get("content", {}) or {}).get("parts", []) or []

            # 在任意 chunk 中尽早捕获 usageMetadata（优先选择字段更完整的一侧）
            if isinstance(response, dict) and isinstance(candidate, dict):
                usage = pick_usage_metadata(response, candidate)
                if isinstance(usage, dict):
                    if "promptTokenCount" in usage:
                        state.input_tokens = int(usage.get("promptTokenCount", 0) or 0)
                        state.has_input_tokens = True
                    if "candidatesTokenCount" in usage:
                        state.output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
                        state.has_output_tokens = True

            # 为保证 message_start 永远是首个事件：在拿到真实值之前，把所有事件暂存到 pending_output。
            if state.has_input_tokens and not message_start_sent:
                send_message_start(ready_output, input_tokens=state.input_tokens)

            # [FIX 2026-01-08] 提前发送 message_start 以避免流式输出被缓冲
            if parts and not message_start_sent:
                send_message_start(ready_output, input_tokens=initial_input_tokens_int)

            for part in parts:
                if not isinstance(part, dict):
                    continue

                if _anthropic_debug_enabled() and "thoughtSignature" in part:
                    try:
                        sig_val = part.get("thoughtSignature")
                        sig_len = len(str(sig_val)) if sig_val is not None else 0
                    except Exception:
                        sig_len = -1
                    log.info(
                        "[ANTHROPIC][thinking_signature] 收到 thoughtSignature 字段: "
                        f"current_block_type={state._current_block_type}, "
                        f"current_index={state._current_block_index}, len={sig_len}"
                    )

                # 兼容：下游可能会把 thoughtSignature 单独作为一个空 part 发送（此时未必带 thought=true）。
                # 只要当前处于 thinking 块且尚未记录 signature，就用 signature_delta 补发。
                signature = part.get("thoughtSignature")
                if (
                    signature
                    and state._current_block_type == "thinking"
                    and not state._current_thinking_signature
                ):
                    evt = _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": state._current_block_index,
                            "delta": {"type": "signature_delta", "signature": signature},
                        },
                    )
                    state._current_thinking_signature = str(signature)
                    if message_start_sent:
                        ready_output.append(evt)
                    else:
                        enqueue(evt)

                if part.get("thought") is True:
                    # SSOP Scan for thoughts
                    thinking_text = part.get("text", "")
                    if thinking_text:
                        syn_tool = state.ssop_scanner.scan(thinking_text)
                        if syn_tool:
                            ssop_evts = state.emit_ssop_tool(syn_tool)
                            if message_start_sent:
                                ready_output.extend(ssop_evts)
                            else:
                                for e in ssop_evts: enqueue(e)

                    if state._current_block_type != "thinking":
                        stop_evt = state.close_block_if_open()
                        if stop_evt:
                            if message_start_sent:
                                ready_output.append(stop_evt)
                            else:
                                enqueue(stop_evt)
                        signature = part.get("thoughtSignature")
                        evt = state.open_thinking_block(signature=signature)
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    
                    if thinking_text:
                        state._current_thinking_text += thinking_text  # 累积 thinking 文本
                        evt = _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": state._current_block_index,
                                "delta": {"type": "thinking_delta", "thinking": thinking_text},
                            },
                        )
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    continue

                if "text" in part:
                    text = part.get("text", "")
                    if isinstance(text, str) and not text.strip():
                        continue
                    
                    # SSOP Scan for text
                    if text:
                        syn_tool = state.ssop_scanner.scan(text)
                        if syn_tool:
                            ssop_evts = state.emit_ssop_tool(syn_tool)
                            if message_start_sent:
                                ready_output.extend(ssop_evts)
                            else:
                                for e in ssop_evts: enqueue(e)

                    if state._current_block_type != "text":
                        stop_evt = state.close_block_if_open()
                        if stop_evt:
                            if message_start_sent:
                                ready_output.append(stop_evt)
                            else:
                                enqueue(stop_evt)
                        evt = state.open_text_block()
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)

                    if text:
                        evt = _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": state._current_block_index,
                                "delta": {"type": "text_delta", "text": text},
                            },
                        )
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    continue

                if "inlineData" in part:
                    # ... inlineData logic remains the same ...
                    stop_evt = state.close_block_if_open()
                    if stop_evt:
                        if message_start_sent:
                            ready_output.append(stop_evt)
                        else:
                            enqueue(stop_evt)

                    inline = part.get("inlineData", {}) or {}
                    idx = state._next_index()
                    block = {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": inline.get("mimeType", "image/png"),
                            "data": inline.get("data", ""),
                        },
                    }
                    evt1 = _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": block,
                        },
                    )
                    evt2 = _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    if message_start_sent:
                        ready_output.extend([evt1, evt2])
                    else:
                        enqueue(evt1)
                        enqueue(evt2)
                    continue

                if "functionCall" in part:
                    # SSOP Logic: Check if we already emitted this tool call synthetically
                    # If so, SKIP it entirely.

                    state.has_tool_use = True

                    fc = part.get("functionCall", {}) or {}
                    tool_name = fc.get("name") or ""
                    tool_args = _remove_nulls_for_tool_input(fc.get("args", {}) or {})

                    # Deterministic ID generation to match SSOP
                    original_tool_id = generate_tool_call_id(tool_name, tool_args)

                    if original_tool_id in state.ssop_scanner.emitted_tool_call_ids:
                        log.info(f"[SSOP] Skipping native duplicate tool call: {tool_name} (ID={original_tool_id})")
                        continue

                    # [FIX 2026-01-16] 将 thoughtSignature 编码到工具ID中，以便往返保留
                    # 这使得签名能够在客户端往返传输中保留，即使客户端会删除自定义字段
                    # [FIX 2026-01-17] [CURSOR兼容] 优先使用 part 中的签名，fallback 到当前/最后的 thinking 块签名
                    # 这是针对 Cursor 的修复：Cursor 不保留编码后的工具ID，需要通过缓存恢复签名
                    # 问题：Gemini 响应的 functionCall part 可能不包含 thoughtSignature
                    # 问题2：当 functionCall 到来时，thinking 块可能已经被关闭，_current_thinking_signature 被重置
                    # 解决：使用 _last_thinking_signature 作为最终 fallback
                    # [FIX 2026-01-17] 添加 get_last_signature() 作为最终 fallback
                    # 问题3：如果上游从未发送 thoughtSignature，所有本地状态都是空的
                    # 解决：从全局缓存中获取最近的 signature 作为最终 fallback
                    thoughtsignature = (
                        part.get("thoughtSignature")
                        or state._current_thinking_signature
                        or state._last_thinking_signature
                    )

                    # [FIX 2026-01-17] 如果本地状态都为空，尝试从全局缓存获取
                    if not thoughtsignature:
                        try:
                            cached_sig = get_last_signature()
                            if cached_sig:
                                thoughtsignature = cached_sig
                                log.info(f"[STREAMING] Using cached last signature as fallback for tool call: {original_tool_id}, sig_len={len(cached_sig)}")
                        except Exception as e:
                            log.warning(f"[STREAMING] Failed to get last signature from cache: {e}")
                    encoded_tool_id = encode_tool_id_with_signature(original_tool_id, thoughtsignature)
                    if thoughtsignature:
                        log.info(f"[STREAMING] Encoded thoughtSignature into tool_id: {original_tool_id}, sig_len={len(thoughtsignature)}")

                        # [FIX 2026-01-16] 缓存工具ID签名 (Layer 1)
                        # 作为工具ID编码机制的补充，当编码失效时可以通过 tool_id 恢复签名
                        try:
                            cache_tool_signature(original_tool_id, thoughtsignature)
                            log.info(f"[STREAMING] Cached tool signature: tool_id={original_tool_id}")
                        except Exception as e:
                            log.warning(f"[SIGNATURE_CACHE] 工具ID签名缓存失败: {e}")
                    else:
                        log.warning(f"[STREAMING] No thoughtSignature available for tool call: {original_tool_id}")

                    stop_evt = state.close_block_if_open()
                    if stop_evt:
                        if message_start_sent:
                            ready_output.append(stop_evt)
                        else:
                            enqueue(stop_evt)

                    idx = state._next_index()
                    evt_start = _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": {
                                "type": "tool_use",
                                "id": encoded_tool_id,  # [FIX 2026-01-16] 使用编码后的ID
                                "name": tool_name,
                                "input": {},
                            },
                        },
                    )

                    input_json = json.dumps(tool_args, ensure_ascii=False, separators=(",", ":"))
                    evt_delta = _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": idx,
                            "delta": {"type": "input_json_delta", "partial_json": input_json},
                        },
                    )
                    evt_stop = _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    if message_start_sent:
                        ready_output.extend([evt_start, evt_delta, evt_stop])
                    else:
                        enqueue(evt_start)
                        enqueue(evt_delta)
                        enqueue(evt_stop)
                    continue

            finish_reason = candidate.get("finishReason")

            if ready_output:
                for evt in ready_output:
                    yield evt

            if finish_reason:
                state.finish_reason = str(finish_reason)
                break

        stop_evt = state.close_block_if_open()
        if stop_evt:
            if message_start_sent:
                yield stop_evt
            else:
                enqueue(stop_evt)

        # 流结束仍未拿到下游 usageMetadata 时，兜底使用估算值发送 message_start
        if not message_start_sent:
            ready_output = []
            send_message_start(ready_output, input_tokens=initial_input_tokens_int)
            for evt in ready_output:
                yield evt

        stop_reason = "tool_use" if state.has_tool_use else "end_turn"
        if state.finish_reason == "MAX_TOKENS" and not state.has_tool_use:
            stop_reason = "max_tokens"

        if _anthropic_debug_enabled():
            estimated_input = initial_input_tokens_int
            downstream_input = state.input_tokens if state.has_input_tokens else 0
            log.info(
                f"[ANTHROPIC][TOKEN] 流式 token: estimated={estimated_input}, "
                f"downstream={downstream_input}"
            )

        yield _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {
                    "input_tokens": state.input_tokens if state.has_input_tokens else initial_input_tokens_int,
                    "output_tokens": state.output_tokens if state.has_output_tokens else 0,
                },
            },
        )
        yield _sse_event("message_stop", {"type": "message_stop"})

    except Exception as e:
        log.error(f"[ANTHROPIC] 流式转换失败: {e}")
        # 错误场景也尽量保证客户端先收到 message_start（否则部分客户端会直接挂起）。
        if not message_start_sent:
            yield _sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": initial_input_tokens_int, "output_tokens": 0},
                    },
                },
            )
        yield _sse_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(e)}},
        )
