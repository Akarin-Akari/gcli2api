"""
Gateway Augment Nodes Bridge

处理 OpenAI 响应到 Augment NDJSON 节点的转换。

从 unified_gateway_router.py 抽取的 Nodes Bridge 逻辑。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any, List, AsyncGenerator, Optional
import json
import time

from .state import bugment_tool_state_put, bugment_tool_state_get

# 延迟导入 log，避免循环依赖
try:
    from log import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# 延迟导入代理函数
try:
    from ..proxy import route_request_with_fallback
except ImportError:
    route_request_with_fallback = None

# 延迟导入工具转换函数
try:
    from src.converters.tool_converter import (
        parse_tool_definitions_from_request,
        convert_tools_to_openai,
    )
except ImportError:
    parse_tool_definitions_from_request = None
    convert_tools_to_openai = None

__all__ = [
    "stream_openai_with_nodes_bridge",
    "augment_chat_history_to_messages",
    "extract_tool_result_nodes",
    "build_openai_messages_from_bugment",
    "prepend_bugment_guidance_system_message",
]


# ==================== 辅助函数 ====================

def augment_chat_history_to_messages(chat_history: Any) -> List[Dict[str, Any]]:
    """
    将 Augment 聊天历史转换为 OpenAI 消息格式

    Args:
        chat_history: Augment 聊天历史

    Returns:
        OpenAI 格式的消息列表
    """
    messages: List[Dict[str, Any]] = []
    if not isinstance(chat_history, list):
        return messages

    for item in chat_history:
        if not isinstance(item, dict):
            continue

        # Bugment log format: { request_message, response_text, ... }
        request_message = item.get("request_message") or item.get("user") or item.get("requestMessage")
        response_text = item.get("response_text") or item.get("assistant") or item.get("responseText")

        if isinstance(request_message, str) and request_message.strip():
            messages.append({"role": "user", "content": request_message})
        if isinstance(response_text, str) and response_text.strip():
            messages.append({"role": "assistant", "content": response_text})

        # Alternate Augment format: { role, content }
        role = item.get("role")
        content = item.get("content")
        if isinstance(role, str) and isinstance(content, str) and role in ("user", "assistant", "system") and content.strip():
            messages.append({"role": role, "content": content})

    return messages


def extract_tool_result_nodes(nodes: Any) -> List[Dict[str, Any]]:
    """
    从节点列表中提取工具结果

    Args:
        nodes: Augment 节点列表

    Returns:
        工具结果列表
    """
    if not isinstance(nodes, list):
        return []
    results: List[Dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == 1 and isinstance(n.get("tool_result_node"), dict):
            results.append(n["tool_result_node"])
    return results


def build_openai_messages_from_bugment(raw_body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert Bugment's request (message/chat_history/nodes) into OpenAI-compatible messages.
    Supports TOOL_RESULT continuation by replaying the original assistant tool_calls (from state)
    and appending tool messages.

    Args:
        raw_body: Bugment 请求体

    Returns:
        OpenAI 格式的消息列表
    """
    messages: List[Dict[str, Any]] = []
    conversation_id = raw_body.get("conversation_id") if isinstance(raw_body, dict) else None

    messages.extend(augment_chat_history_to_messages(raw_body.get("chat_history")))

    tool_results = extract_tool_result_nodes(raw_body.get("nodes"))
    if tool_results:
        assistant_tool_calls: List[Dict[str, Any]] = []
        tool_messages: List[Dict[str, Any]] = []
        fallback_user_notes: List[str] = []

        for tr in tool_results:
            tool_use_id = tr.get("tool_use_id")
            content = tr.get("content")
            is_error = tr.get("is_error")

            if not isinstance(tool_use_id, str) or not tool_use_id.strip():
                continue
            text = content if isinstance(content, str) else (str(content) if content is not None else "")

            state = bugment_tool_state_get(conversation_id, tool_use_id)
            if isinstance(state, dict) and isinstance(state.get("tool_name"), str):
                assistant_tool_calls.append(
                    {
                        "id": tool_use_id,
                        "type": "function",
                        "function": {"name": state["tool_name"], "arguments": state.get("arguments_json") or "{}"},
                    }
                )
                tool_messages.append({"role": "tool", "tool_call_id": tool_use_id, "content": text})
            else:
                note = f"[Bugment] Tool result received but missing tool_call state: tool_use_id={tool_use_id}"
                if is_error:
                    note += " (is_error=true)"
                fallback_user_notes.append(note + "\n" + text)

        if assistant_tool_calls:
            messages.append({"role": "assistant", "content": "", "tool_calls": assistant_tool_calls})
            messages.extend(tool_messages)

        for note in fallback_user_notes:
            messages.append({"role": "user", "content": note})

    # Current user message (may be empty on tool_result continuations)
    current_message = raw_body.get("message")
    if isinstance(current_message, str) and current_message.strip():
        messages.append({"role": "user", "content": current_message})

    if not messages:
        messages = [{"role": "user", "content": raw_body.get("message") or "Hello"}]

    return messages


def prepend_bugment_guidance_system_message(raw_body: Dict[str, Any], messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Bugment/Augment sends guidance via out-of-band fields like:
    - user_guidelines / workspace_guidelines
    - rules (list)
    - agent_memories
    - persona_type

    Upstream OpenAI-compatible backends will ignore these unless we inject them into a system message.
    This is critical for preserving "agent" behavior and to avoid the model hallucinating local files
    like `.augment/` or `User Guidelines.md` inside the workspace.

    Args:
        raw_body: Bugment 请求体
        messages: 现有消息列表

    Returns:
        带有系统消息的消息列表
    """
    if not isinstance(raw_body, dict):
        return messages

    guidance_parts: List[str] = []

    # Stable prelude: keep it short to avoid fighting user-provided prompts.
    guidance_parts.append(
        "\n".join(
            [
                "# Runtime",
                "You are running inside a VSCode agent environment (Bugment/Augment-like).",
                "You can call provided tools to read/write workspace files and perform codebase retrieval.",
                "Do not assume there is a `.augment/` directory or `User Guidelines.md` file in the workspace unless you can list it.",
            ]
        )
    )

    ug = raw_body.get("user_guidelines")
    if isinstance(ug, str) and ug.strip():
        guidance_parts.append(f"# User Guidelines\n{ug.strip()}")

    wg = raw_body.get("workspace_guidelines")
    if isinstance(wg, str) and wg.strip():
        guidance_parts.append(f"# Workspace Guidelines\n{wg.strip()}")

    am = raw_body.get("agent_memories")
    if isinstance(am, str) and am.strip():
        guidance_parts.append(f"# Agent Memories\n{am.strip()}")

    rules = raw_body.get("rules")
    if isinstance(rules, list) and rules:
        guidance_parts.append(f"# Rules\n{json.dumps(rules, ensure_ascii=False)}")

    persona = raw_body.get("persona_type")
    if persona is not None and str(persona).strip():
        guidance_parts.append(f"# Persona Type\n{persona}")

    if not guidance_parts:
        return messages

    system_text = "\n\n".join(guidance_parts).strip()
    if not system_text:
        return messages

    return [{"role": "system", "content": system_text}] + list(messages)


# ==================== 核心流式函数 ====================

async def stream_openai_with_nodes_bridge(
    *,
    headers: Dict[str, str],
    raw_body: Dict[str, Any],
    model: str,
) -> AsyncGenerator[str, None]:
    """
    Stream upstream /chat/completions and emit Bugment-compatible NDJSON objects.

    Bugment expects each NDJSON line to be a "BackChatResult"-like object with:
    - text: string (required)
    - nodes: optional list of nodes (e.g. type=5 tool_use)
    - stop_reason: optional string

    Tool loop contract (from vanilla extension):
    - Tool use node: { id, type: 5, tool_use: { tool_use_id, tool_name, input_json } }
    - Tool result node (client->gateway): { id, type: 1, tool_result_node: { tool_use_id, content, is_error } }

    Args:
        headers: 请求头
        raw_body: Bugment 请求体
        model: 模型名称

    Yields:
        NDJSON 格式的响应字符串
    """
    if route_request_with_fallback is None:
        yield json.dumps({"text": "[Gateway Error] route_request_with_fallback not available"}, ensure_ascii=False) + "\n"
        return

    conversation_id = raw_body.get("conversation_id") if isinstance(raw_body, dict) else None
    messages = prepend_bugment_guidance_system_message(raw_body, build_openai_messages_from_bugment(raw_body))

    tools = None
    try:
        raw_tool_defs = raw_body.get("tool_definitions")
        if isinstance(raw_tool_defs, list) and raw_tool_defs:
            if parse_tool_definitions_from_request is not None and convert_tools_to_openai is not None:
                augment_tools = parse_tool_definitions_from_request(raw_tool_defs)
                tools = convert_tools_to_openai(augment_tools) if augment_tools else None
    except Exception as e:
        log.warning(f"Failed to convert tool_definitions to OpenAI tools: {e}", tag="GATEWAY")

    request_body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        request_body["tools"] = tools
        request_body["tool_choice"] = "auto"

    sse_stream = await route_request_with_fallback(
        endpoint="/chat/completions",
        method="POST",
        headers=headers,
        body=request_body,
        model=model,
        stream=True,
    )

    buffer = ""
    tool_calls_by_index: Dict[int, Dict[str, Any]] = {}
    saw_tool_calls = False
    saw_done = False

    async for chunk in sse_stream:
        if not chunk:
            continue
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="ignore")
        buffer += chunk

        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue

            json_str = line[6:].strip()
            if json_str == "[DONE]":
                buffer = ""
                saw_done = True
                break

            try:
                evt = json.loads(json_str)
            except Exception:
                continue

            choices = evt.get("choices") or []
            if not choices:
                continue
            choice0 = choices[0] if isinstance(choices[0], dict) else None
            if not choice0:
                continue

            # Text streaming
            delta = choice0.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield json.dumps({"text": content}, ensure_ascii=False, separators=(",", ":")) + "\n"

            # Tool calls streaming (OpenAI-like)
            tool_calls = delta.get("tool_calls") or []
            if isinstance(tool_calls, list) and tool_calls:
                saw_tool_calls = True
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    idx = tc.get("index", 0)
                    if not isinstance(idx, int):
                        idx = 0
                    cur = tool_calls_by_index.setdefault(idx, {"id": None, "type": "function", "function": {"name": None, "arguments": ""}})
                    if isinstance(tc.get("id"), str):
                        cur["id"] = tc["id"]
                    if isinstance(tc.get("type"), str):
                        cur["type"] = tc["type"]
                    func = tc.get("function")
                    if isinstance(func, dict):
                        if isinstance(func.get("name"), str):
                            cur["function"]["name"] = func["name"]
                        if isinstance(func.get("arguments"), str):
                            cur["function"]["arguments"] += func["arguments"]

            finish_reason = choice0.get("finish_reason")
            if finish_reason in ("tool_calls", "function_call"):
                saw_tool_calls = True

        if saw_done:
            break

    if saw_tool_calls and tool_calls_by_index:
        nodes: List[Dict[str, Any]] = []
        next_node_id = 0
        for idx in sorted(tool_calls_by_index.keys()):
            tc = tool_calls_by_index[idx]
            tool_use_id = tc.get("id") or f"call_{int(time.time())}_{idx}"
            func = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            tool_name = func.get("name") if isinstance(func, dict) else None
            arg_str = func.get("arguments") if isinstance(func, dict) else ""
            if not isinstance(tool_use_id, str) or not tool_use_id.strip():
                continue
            if not isinstance(tool_name, str) or not tool_name.strip():
                tool_name = "unknown"
            if not isinstance(arg_str, str):
                arg_str = ""
            arguments_json = arg_str.strip() or "{}"
            # Bugment will JSON.parse(input_json); ensure it's valid JSON.
            try:
                parsed_args = json.loads(arguments_json)
                # Compatibility: some upstreams call codebase-retrieval with `query`, while Bugment expects
                # `information_request` (per sidecar tool schema). Map when needed.
                if tool_name == "codebase-retrieval" and isinstance(parsed_args, dict):
                    if "information_request" not in parsed_args:
                        if isinstance(parsed_args.get("query"), str) and parsed_args.get("query"):
                            parsed_args["information_request"] = parsed_args["query"]
                        elif isinstance(parsed_args.get("informationRequest"), str) and parsed_args.get("informationRequest"):
                            parsed_args["information_request"] = parsed_args["informationRequest"]
                    arguments_json = json.dumps(parsed_args, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                arguments_json = json.dumps({"raw_arguments": arguments_json}, ensure_ascii=False, separators=(",", ":"))

            # Persist tool call mapping for TOOL_RESULT continuations.
            bugment_tool_state_put(conversation_id, tool_use_id, tool_name=tool_name, arguments_json=arguments_json)

            nodes.append(
                {
                    "id": next_node_id,
                    "type": 5,
                    "tool_use": {"tool_use_id": tool_use_id, "tool_name": tool_name, "input_json": arguments_json},
                }
            )
            next_node_id += 1

        if nodes:
            yield json.dumps({"text": "", "nodes": nodes, "stop_reason": "tool_use"}, ensure_ascii=False, separators=(",", ":")) + "\n"
        return

    # No tool calls: end of turn (send a deterministic terminal marker for clients that rely on it).
    yield json.dumps({"text": "", "stop_reason": "end_turn"}, ensure_ascii=False, separators=(",", ":")) + "\n"
