# gcli2api 自研版 Signature 与思维块处理缺陷修复开发文档

**文档创建时间**: 2026-01-16  
**作者**: Claude Opus 4.5 (浮浮酱)  
**目标**: 修复自研版在 signature 和 thinking block 处理方面的缺陷，对齐官方版实现

---

## 📋 目录

1. [问题概述](#问题概述)
2. [缺陷清单](#缺陷清单)
3. [修复方案](#修复方案)
4. [实施步骤](#实施步骤)
5. [代码示例](#代码示例)
6. [测试建议](#测试建议)
7. [参考资源](#参考资源)

---

## 1. 问题概述

### 1.1 背景

gcli2api 自研版在 Claude Extended Thinking 模式的 signature 和思维块处理方面存在多个缺陷，导致：
- 工具循环中签名丢失
- 多轮工具调用时签名无法传递
- 缓存未命中时功能降级
- 跨会话签名污染

### 1.2 对比分析

| 功能 | 官方版 (gcli2api_official) | 自研版 (gcli2api) | 状态 |
|------|---------------------------|------------------|------|
| 工具ID签名编码 | ✅ 实现 | ❌ 缺失 | **P0** |
| 思维块验证 | ✅ 实现 | ❌ 缺失 | **P1** |
| 思维块清理 | ✅ 实现 | ❌ 缺失 | **P1** |
| 多层签名恢复 | ✅ 实现 | ⚠️ 单一缓存 | **P1** |
| 会话级隔离 | ✅ 实现 | ❌ 缺失 | **P2** |
| 思维块排序 | ✅ 实现 | ❌ 缺失 | **P2** |

---

## 2. 缺陷清单

### 🔴 P0 - 严重缺陷（必须修复）

#### 缺陷 #1: 缺少工具 ID 签名编码机制

**问题描述**:
- 自研版在工具调用时直接使用 dummy 值 `"skip_thought_signature_validator"`
- 无法在客户端往返传输中保留真实签名
- 导致工具循环中签名丢失

**影响范围**:
- 所有工具调用场景
- 多轮工具循环
- 客户端删除自定义字段后的恢复

**代码位置**:
```python
# 文件: src/anthropic_converter.py
# 行号: 582-595
elif item_type == "tool_use":
    # ❌ 问题代码
    fc_part: Dict[str, Any] = {
        "functionCall": {
            "id": item.get("id"),
            "name": item.get("name"),
            "args": item.get("input", {}) or {},
        },
        # ❌ 总是使用占位符，无法保留真实签名
        "thoughtSignature": "skip_thought_signature_validator",
    }
```

**官方版实现**:
```python
# 文件: gcli2api_official/src/converter/anthropic2gemini.py
# 行号: 470-486
elif item_type == "tool_use":
    encoded_id = item.get("id") or ""
    original_id, thoughtsignature = decode_tool_id_and_signature(encoded_id)
    
    fc_part: Dict[str, Any] = {
        "functionCall": {
            "id": original_id,  # 使用原始ID，不带签名
            "name": item.get("name"),
            "args": item.get("input", {}) or {},
        }
    }
    
    # ✅ 从编码ID中提取签名
    if thoughtsignature:
        fc_part["thoughtSignature"] = thoughtsignature
    else:
        fc_part["thoughtSignature"] = "skip_thought_signature_validator"
```

---

### 🟡 P1 - 重要缺陷（优先修复）

#### 缺陷 #2: 缺少思维块验证和清理机制

**问题描述**:
- 缺少 `has_valid_thoughtsignature()` 验证函数
- 缺少 `sanitize_thinking_block()` 清理函数
- 缺少 `remove_trailing_unsigned_thinking()` 尾部清理
- 无效签名块可能被发送，导致 API 400 错误

**影响范围**:
- 历史消息中的 thinking block 处理
- API 错误率
- 数据污染

**官方版实现**:
```python
# 文件: gcli2api_official/src/converter/anthropic2gemini.py
# 行号: 32-123

MIN_SIGNATURE_LENGTH = 10

def has_valid_thoughtsignature(block: Dict[str, Any]) -> bool:
    """检查 thinking 块是否有有效签名"""
    if not isinstance(block, dict):
        return True
    
    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return True  # 非 thinking 块默认有效
    
    thinking = block.get("thinking", "")
    thoughtsignature = block.get("thoughtSignature")
    
    # 空 thinking + 任意 thoughtsignature = 有效 (trailing signature case)
    if not thinking and thoughtsignature is not None:
        return True
    
    # 有内容 + 足够长度的 thoughtsignature = 有效
    if thoughtsignature and isinstance(thoughtsignature, str) and len(thoughtsignature) >= MIN_SIGNATURE_LENGTH:
        return True
    
    return False

def sanitize_thinking_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """清理 thinking 块,只保留必要字段(移除 cache_control 等)"""
    if not isinstance(block, dict):
        return block
    
    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return block
    
    # 重建块,移除额外字段
    sanitized: Dict[str, Any] = {
        "type": block_type,
        "thinking": block.get("thinking", "")
    }
    
    thoughtsignature = block.get("thoughtSignature")
    if thoughtsignature:
        sanitized["thoughtSignature"] = thoughtsignature
    
    return sanitized

def remove_trailing_unsigned_thinking(blocks: List[Dict[str, Any]]) -> None:
    """移除尾部的无签名 thinking 块"""
    if not blocks:
        return
    
    # 从后向前扫描
    end_index = len(blocks)
    for i in range(len(blocks) - 1, -1, -1):
        block = blocks[i]
        if not isinstance(block, dict):
            break
        
        block_type = block.get("type")
        if block_type in ("thinking", "redacted_thinking"):
            if not has_valid_thoughtsignature(block):
                end_index = i
            else:
                break  # 遇到有效签名的 thinking 块,停止
        else:
            break  # 遇到非 thinking 块,停止
    
    if end_index < len(blocks):
        removed = len(blocks) - end_index
        del blocks[end_index:]
        log.debug(f"Removed {removed} trailing unsigned thinking block(s)")
```

---

#### 缺陷 #3: 缓存未命中时直接跳过

**问题描述**:
- 缓存未命中时直接跳过 thinking block，而不是尝试其他恢复策略
- 缺少多层签名恢复机制

**影响范围**:
- 用户体验（thinking 内容丢失）
- 功能完整性

**代码位置**:
```python
# 文件: src/anthropic_converter.py
# 行号: 532-535
# ❌ 问题代码
else:
    # [FIX] 缓存未命中时，跳过 thinking block 而不是使用消息的 signature
    # 使用无效的 signature 会导致 400 错误
    log.warning(f"[ANTHROPIC CONVERTER] Thinking block 缓存未命中，跳过此 block")
```

**官方版策略**:
- 多层恢复优先级：
  1. 客户端提供的签名
  2. 上下文中的签名
  3. 会话缓存（Layer 3）
  4. 工具缓存（Layer 1）
  5. 全局存储（已废弃，仅用于向后兼容）

---

#### 缺陷 #4: 缓存 key 哈希冲突风险

**问题描述**:
- 只使用前 500 字符的 MD5 作为 key
- 不同 thinking 内容可能产生相同哈希
- 虽然有完整文本验证，但不够完善

**代码位置**:
```python
# 文件: src/signature_cache.py
# 行号: 158-184
def _generate_key(self, thinking_text: str) -> str:
    # ❌ 只取前 500 字符，可能冲突
    text_prefix = normalized_text[:self._key_prefix_length]
    return hashlib.md5(text_prefix.encode('utf-8')).hexdigest()
```

**改进建议**:
- 增加完整文本验证的严格性
- 考虑使用更长的前缀或 SHA256
- 添加冲突检测和警告

---

### 🟢 P2 - 次要缺陷（可选修复）

#### 缺陷 #5: 缺少会话级签名隔离

**问题描述**:
- 使用全局缓存，不同会话可能共享签名
- 可能导致跨会话签名污染

**官方版实现** (Antigravity-Manager):
```rust
// 文件: src-tauri/src/proxy/signature_cache.rs
// Layer 3: Session ID -> Latest Thinking Signature
session_signatures: Mutex<HashMap<String, CacheEntry<String>>>,
```

---

#### 缺陷 #6: 缺少思维块排序优化

**问题描述**:
- 未实现 thinking 块前置排序
- 可能违反 Claude API 协议要求

**官方版实现** (Antigravity-Manager):
```rust
// 文件: src-tauri/src/proxy/mappers/claude/request.rs
// 三阶段分区：[Thinking, Text, ToolUse]
fn sort_thinking_blocks_first(messages: &mut [Message])
```

---

## 3. 修复方案

### 3.1 P0 修复：实现工具 ID 签名编码机制

#### 步骤 1: 创建签名编码/解码模块

**文件**: `src/converter/thoughtSignature_fix.py` (新建)

```python
"""
thoughtSignature 处理公共模块

提供统一的 thoughtSignature 编码/解码功能，用于在工具调用ID中保留签名信息。
这使得签名能够在客户端往返传输中保留，即使客户端会删除自定义字段。
"""

from typing import Optional, Tuple

# 在工具调用ID中嵌入thoughtSignature的分隔符
# 这使得签名能够在客户端往返传输中保留，即使客户端会删除自定义字段
THOUGHT_SIGNATURE_SEPARATOR = "__thought__"


def encode_tool_id_with_signature(tool_id: str, signature: Optional[str]) -> str:
    """
    将 thoughtSignature 编码到工具调用ID中，以便往返保留。

    Args:
        tool_id: 原始工具调用ID
        signature: thoughtSignature（可选）

    Returns:
        编码后的工具调用ID

    Examples:
        >>> encode_tool_id_with_signature("call_123", "abc")
        'call_123__thought__abc'
        >>> encode_tool_id_with_signature("call_123", None)
        'call_123'
    """
    if not signature:
        return tool_id
    return f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}"


def decode_tool_id_and_signature(encoded_id: str) -> Tuple[str, Optional[str]]:
    """
    从编码的ID中提取原始工具ID和thoughtSignature。

    Args:
        encoded_id: 编码的工具调用ID

    Returns:
        (原始工具ID, thoughtSignature) 元组

    Examples:
        >>> decode_tool_id_and_signature("call_123__thought__abc")
        ('call_123', 'abc')
        >>> decode_tool_id_and_signature("call_123")
        ('call_123', None)
    """
    if not encoded_id or THOUGHT_SIGNATURE_SEPARATOR not in encoded_id:
        return encoded_id, None
    parts = encoded_id.split(THOUGHT_SIGNATURE_SEPARATOR, 1)
    return parts[0], parts[1] if len(parts) == 2 else None
```

#### 步骤 2: 修改流式响应处理

**文件**: `src/anthropic_streaming.py`

在工具调用时编码签名：

```python
# 在处理 tool_use 时
if "functionCall" in part:
    fc = part.get("functionCall", {}) or {}
    original_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
    thoughtsignature = part.get("thoughtSignature")
    
    # ✅ 对工具调用ID进行签名编码
    from src.converter.thoughtSignature_fix import encode_tool_id_with_signature
    encoded_id = encode_tool_id_with_signature(original_id, thoughtsignature)
    
    content.append({
        "type": "tool_use",
        "id": encoded_id,  # ✅ 使用编码后的ID
        "name": fc.get("name") or "",
        "input": _remove_nulls_for_tool_input(fc.get("args", {}) or {}),
    })
```

#### 步骤 3: 修改请求转换处理

**文件**: `src/anthropic_converter.py`

在转换工具调用时解码签名：

```python
# 行号: 582-595 修改为
elif item_type == "tool_use":
    # ✅ 从编码ID中解码签名
    from src.converter.thoughtSignature_fix import decode_tool_id_and_signature
    
    encoded_id = item.get("id") or ""
    original_id, thoughtsignature = decode_tool_id_and_signature(encoded_id)
    
    fc_part: Dict[str, Any] = {
        "functionCall": {
            "id": original_id,  # ✅ 使用原始ID，不带签名
            "name": item.get("name"),
            "args": item.get("input", {}) or {},
        }
    }
    
    # ✅ 如果提取到签名则添加，否则使用占位符
    if thoughtsignature:
        fc_part["thoughtSignature"] = thoughtsignature
    else:
        fc_part["thoughtSignature"] = "skip_thought_signature_validator"
    
    parts.append(fc_part)
```

在转换 tool_result 时也要解码：

```python
# 行号: 596-614 修改为
elif item_type == "tool_result":
    output = _extract_tool_result_output(item.get("content"))
    encoded_tool_use_id = item.get("tool_use_id") or ""
    
    # ✅ 解码获取原始ID（functionResponse不需要签名）
    from src.converter.thoughtSignature_fix import decode_tool_id_and_signature
    original_tool_use_id, _ = decode_tool_id_and_signature(encoded_tool_use_id)
    
    # 从 tool_result 获取 name，如果没有则从映射中查找
    func_name = item.get("name")
    if not func_name and encoded_tool_use_id:
        # 使用编码ID查找映射
        tool_info = tool_use_info.get(str(encoded_tool_use_id))
        if tool_info:
            func_name = tool_info[0]  # 获取 name
    if not func_name:
        func_name = "unknown_function"
    
    parts.append({
        "functionResponse": {
            "id": original_tool_use_id,  # ✅ 使用解码后的原始ID
            "name": func_name,
            "response": {"output": output},
        }
    })
```

---

### 3.2 P1 修复：实现思维块验证和清理机制

#### 步骤 1: 添加验证和清理函数

**文件**: `src/anthropic_converter.py` (在文件开头添加)

```python
# ============================================================================
# Thinking 块验证和清理
# ============================================================================

# 最小有效签名长度
MIN_SIGNATURE_LENGTH = 10


def has_valid_thoughtsignature(block: Dict[str, Any]) -> bool:
    """
    检查 thinking 块是否有有效签名
    
    Args:
        block: content block 字典
        
    Returns:
        bool: 是否有有效签名
    """
    if not isinstance(block, dict):
        return True
    
    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return True  # 非 thinking 块默认有效
    
    thinking = block.get("thinking", "")
    thoughtsignature = block.get("thoughtSignature")
    
    # 空 thinking + 任意 thoughtsignature = 有效 (trailing signature case)
    if not thinking and thoughtsignature is not None:
        return True
    
    # 有内容 + 足够长度的 thoughtsignature = 有效
    if thoughtsignature and isinstance(thoughtsignature, str) and len(thoughtsignature) >= MIN_SIGNATURE_LENGTH:
        return True
    
    return False


def sanitize_thinking_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    清理 thinking 块,只保留必要字段(移除 cache_control 等)
    
    Args:
        block: content block 字典
        
    Returns:
        清理后的 block 字典
    """
    if not isinstance(block, dict):
        return block
    
    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return block
    
    # 重建块,移除额外字段
    sanitized: Dict[str, Any] = {
        "type": block_type,
        "thinking": block.get("thinking", "")
    }
    
    thoughtsignature = block.get("thoughtSignature")
    if thoughtsignature:
        sanitized["thoughtSignature"] = thoughtsignature
    
    return sanitized


def remove_trailing_unsigned_thinking(blocks: List[Dict[str, Any]]) -> None:
    """
    移除尾部的无签名 thinking 块
    
    Args:
        blocks: content blocks 列表 (会被修改)
    """
    if not blocks:
        return
    
    # 从后向前扫描
    end_index = len(blocks)
    for i in range(len(blocks) - 1, -1, -1):
        block = blocks[i]
        if not isinstance(block, dict):
            break
        
        block_type = block.get("type")
        if block_type in ("thinking", "redacted_thinking"):
            if not has_valid_thoughtsignature(block):
                end_index = i
            else:
                break  # 遇到有效签名的 thinking 块,停止
        else:
            break  # 遇到非 thinking 块,停止
    
    if end_index < len(blocks):
        removed = len(blocks) - end_index
        del blocks[end_index:]
        log.debug(f"Removed {removed} trailing unsigned thinking block(s)")


def filter_invalid_thinking_blocks(messages: List[Dict[str, Any]]) -> None:
    """
    过滤消息中的无效 thinking 块，并清理所有 thinking 块的额外字段（如 cache_control）

    Args:
        messages: Anthropic messages 列表 (会被修改)
    """
    total_filtered = 0

    for msg in messages:
        # 只处理 assistant 和 model 消息
        role = msg.get("role", "")
        if role not in ("assistant", "model"):
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        original_len = len(content)
        new_blocks: List[Dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue

            block_type = block.get("type")
            if block_type not in ("thinking", "redacted_thinking"):
                new_blocks.append(block)
                continue

            # 所有 thinking 块都需要清理（移除 cache_control 等额外字段）
            # 检查 thinking 块的有效性
            if has_valid_thoughtsignature(block):
                # 有效签名，清理后保留
                new_blocks.append(sanitize_thinking_block(block))
            else:
                # 无效签名，将内容转换为 text 块
                thinking_text = block.get("thinking", "")
                if thinking_text and str(thinking_text).strip():
                    log.info(
                        f"[Claude-Handler] Converting thinking block with invalid thoughtSignature to text. "
                        f"Content length: {len(thinking_text)} chars"
                    )
                    new_blocks.append({"type": "text", "text": thinking_text})
                else:
                    log.debug("[Claude-Handler] Dropping empty thinking block with invalid thoughtSignature")

        msg["content"] = new_blocks
        filtered_count = original_len - len(new_blocks)
        total_filtered += filtered_count

        # 如果过滤后为空,添加一个空文本块以保持消息有效
        if not new_blocks:
            msg["content"] = [{"type": "text", "text": ""}]

    if total_filtered > 0:
        log.debug(f"Filtered {total_filtered} invalid thinking block(s) from history")
```

#### 步骤 2: 在请求转换前应用过滤

**文件**: `src/anthropic_converter.py`

在 `anthropic_to_gemini_request()` 函数中：

```python
async def anthropic_to_gemini_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    # ... 现有代码 ...
    
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    
    # ✅ [CRITICAL FIX] 过滤并修复 Thinking 块签名
    # 在转换前先过滤无效的 thinking 块
    filter_invalid_thinking_blocks(messages)
    
    # ... 继续转换 ...
    
    # ✅ [CRITICAL FIX] 移除尾部无签名的 thinking 块
    # 对真实请求应用额外的清理
    for content in contents:
        role = content.get("role", "")
        if role == "model":  # 只处理 model/assistant 消息
            parts = content.get("parts", [])
            if isinstance(parts, list):
                remove_trailing_unsigned_thinking(parts)
```

---

### 3.3 P1 修复：改进签名恢复策略

#### 步骤 1: 实现多层恢复机制

**文件**: `src/anthropic_converter.py`

修改 thinking block 处理逻辑：

```python
# 行号: 511-535 修改为
# [FIX 2026-01-09] 多层签名恢复策略
# 优先级: 客户端签名 -> 缓存签名 -> 最近签名 -> 跳过
from src.signature_cache import get_cached_signature, get_last_signature

if thinking_text:
    # 优先级 1: 使用消息提供的签名（如果有效）
    message_signature = item.get("signature", "")
    if message_signature and len(message_signature) >= MIN_SIGNATURE_LENGTH:
        # 验证消息签名是否在缓存中（可选验证）
        cached_signature = get_cached_signature(thinking_text)
        if cached_signature == message_signature:
            # 缓存验证通过，使用消息签名
            final_signature = message_signature
            log.debug(f"[ANTHROPIC CONVERTER] 使用消息签名（缓存验证通过）")
        else:
            # 缓存验证失败，优先使用缓存签名
            final_signature = cached_signature or message_signature
            if cached_signature:
                log.info(f"[ANTHROPIC CONVERTER] 使用缓存签名替代消息签名")
    else:
        # 优先级 2: 从缓存恢复
        cached_signature = get_cached_signature(thinking_text)
        if cached_signature:
            final_signature = cached_signature
            log.debug(f"[ANTHROPIC CONVERTER] 从缓存恢复签名")
        else:
            # 优先级 3: 使用最近缓存的签名（fallback）
            last_sig = get_last_signature()
            if last_sig:
                final_signature = last_sig
                log.info(f"[ANTHROPIC CONVERTER] 使用最近缓存的签名（fallback）")
            else:
                # 优先级 4: 跳过 thinking block
                log.warning(f"[ANTHROPIC CONVERTER] Thinking block 所有恢复策略失败，跳过此 block")
                continue  # 跳过此 block
    
    # 使用恢复的签名
    if final_signature:
        part: Dict[str, Any] = {
            "text": str(thinking_text),
            "thought": True,
            "thoughtSignature": final_signature,
        }
        parts.append(part)
```

---

## 4. 实施步骤

### Phase 1: P0 修复（必须完成）

1. ✅ 创建 `src/converter/thoughtSignature_fix.py` 模块
2. ✅ 修改 `src/anthropic_streaming.py` 流式响应处理
3. ✅ 修改 `src/anthropic_converter.py` 请求转换处理
4. ✅ 添加单元测试

**预计时间**: 2-3 小时

### Phase 2: P1 修复（优先完成）

1. ✅ 添加思维块验证和清理函数
2. ✅ 在请求转换前应用过滤
3. ✅ 实现多层签名恢复策略
4. ✅ 改进缓存 key 生成（可选）

**预计时间**: 3-4 小时

### Phase 3: P2 修复（可选）

1. ⚠️ 实现会话级签名隔离
2. ⚠️ 添加思维块排序优化

**预计时间**: 4-6 小时

---

## 5. 代码示例

### 5.1 完整的工具调用签名编码示例

```python
# 流式响应中编码签名
from src.converter.thoughtSignature_fix import encode_tool_id_with_signature

# 在 anthropic_streaming.py 中
if "functionCall" in part:
    fc = part.get("functionCall", {}) or {}
    original_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
    thoughtsignature = part.get("thoughtSignature")
    
    # 编码签名到工具ID
    encoded_id = encode_tool_id_with_signature(original_id, thoughtsignature)
    
    # 发送编码后的ID给客户端
    content.append({
        "type": "tool_use",
        "id": encoded_id,  # 客户端会保留这个ID
        "name": fc.get("name") or "",
        "input": fc.get("args", {}) or {},
    })
```

```python
# 请求转换中解码签名
from src.converter.thoughtSignature_fix import decode_tool_id_and_signature

# 在 anthropic_converter.py 中
elif item_type == "tool_use":
    encoded_id = item.get("id") or ""
    original_id, thoughtsignature = decode_tool_id_and_signature(encoded_id)
    
    fc_part = {
        "functionCall": {
            "id": original_id,  # 使用原始ID
            "name": item.get("name"),
            "args": item.get("input", {}) or {},
        }
    }
    
    # 使用解码的签名
    if thoughtsignature:
        fc_part["thoughtSignature"] = thoughtsignature
    else:
        fc_part["thoughtSignature"] = "skip_thought_signature_validator"
```

---

## 6. 测试建议

### 6.1 单元测试

**文件**: `tests/test_thoughtSignature_fix.py` (新建)

```python
import pytest
from src.converter.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature,
    THOUGHT_SIGNATURE_SEPARATOR
)

def test_encode_tool_id_with_signature():
    """测试工具ID编码"""
    tool_id = "call_123"
    signature = "abc123"
    
    encoded = encode_tool_id_with_signature(tool_id, signature)
    assert encoded == f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}"
    
    # 无签名时返回原ID
    encoded_none = encode_tool_id_with_signature(tool_id, None)
    assert encoded_none == tool_id

def test_decode_tool_id_and_signature():
    """测试工具ID解码"""
    tool_id = "call_123"
    signature = "abc123"
    encoded = f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}"
    
    original, decoded_sig = decode_tool_id_and_signature(encoded)
    assert original == tool_id
    assert decoded_sig == signature
    
    # 无签名时返回原ID和None
    original_none, sig_none = decode_tool_id_and_signature(tool_id)
    assert original_none == tool_id
    assert sig_none is None

def test_round_trip():
    """测试往返编码解码"""
    tool_id = "call_abc123"
    signature = "sig_xyz789"
    
    encoded = encode_tool_id_with_signature(tool_id, signature)
    decoded_id, decoded_sig = decode_tool_id_and_signature(encoded)
    
    assert decoded_id == tool_id
    assert decoded_sig == signature
```

### 6.2 集成测试

**测试场景**:
1. 工具循环中签名保留
2. 多轮工具调用签名传递
3. 缓存未命中时的恢复策略
4. 无效签名块的过滤

**测试脚本**: `tests/test_signature_integration.py` (新建)

```python
import pytest
from src.anthropic_converter import (
    has_valid_thoughtsignature,
    sanitize_thinking_block,
    filter_invalid_thinking_blocks
)

def test_has_valid_thoughtsignature():
    """测试签名验证"""
    # 有效签名
    valid_block = {
        "type": "thinking",
        "thinking": "Let me think...",
        "thoughtSignature": "a" * 50  # 足够长度
    }
    assert has_valid_thoughtsignature(valid_block) == True
    
    # 无效签名（太短）
    invalid_block = {
        "type": "thinking",
        "thinking": "Let me think...",
        "thoughtSignature": "short"  # 太短
    }
    assert has_valid_thoughtsignature(invalid_block) == False

def test_sanitize_thinking_block():
    """测试思维块清理"""
    block = {
        "type": "thinking",
        "thinking": "Let me think...",
        "thoughtSignature": "sig123",
        "cache_control": "no-cache",  # 额外字段
        "extra_field": "should_be_removed"
    }
    
    sanitized = sanitize_thinking_block(block)
    assert "cache_control" not in sanitized
    assert "extra_field" not in sanitized
    assert sanitized["thoughtSignature"] == "sig123"
```

### 6.3 端到端测试

**测试场景**:
1. 完整的工具循环（包含签名）
2. 客户端删除自定义字段后的恢复
3. 多会话隔离

---

## 7. 参考资源

### 7.1 官方版实现参考

- **工具ID编码**: `gcli2api_official/src/converter/thoughtSignature_fix.py`
- **思维块验证**: `gcli2api_official/src/converter/anthropic2gemini.py` (行号: 32-123)
- **流式处理**: `gcli2api_official/src/converter/anthropic2gemini.py` (行号: 913-1251)

### 7.2 Antigravity-Manager 参考

- **三层缓存**: `Antigravity-Manager/src-tauri/src/proxy/signature_cache.rs`
- **思维块排序**: `Antigravity-Manager/src-tauri/src/proxy/mappers/claude/request.rs` (行号: 178-220)
- **思维块恢复**: `Antigravity-Manager/src-tauri/src/proxy/mappers/claude/thinking_utils.rs`

### 7.3 相关文档

- Gemini API 文档: https://ai.google.dev/gemini-api/docs/thought-signatures
- Claude API 文档: https://docs.anthropic.com/claude/docs/extended-thinking

---

## 8. 验收标准

### P0 修复验收

- [ ] 工具调用时签名能够编码到工具ID中
- [ ] 工具结果时能够从工具ID中解码签名
- [ ] 工具循环中签名能够正确传递
- [ ] 单元测试通过率 100%

### P1 修复验收

- [ ] 无效签名块能够被正确过滤
- [ ] thinking 块能够正确清理额外字段
- [ ] 多层签名恢复策略能够正常工作
- [ ] 集成测试通过率 100%

### P2 修复验收（可选）

- [ ] 会话级签名隔离正常工作
- [ ] 思维块排序符合协议要求

---

## 9. 注意事项

1. **向后兼容**: 确保修复不影响现有功能
2. **性能影响**: 签名编码/解码操作应该轻量级
3. **错误处理**: 所有签名操作都应该有适当的错误处理和日志
4. **测试覆盖**: 确保所有新功能都有对应的测试

---

## 10. 后续优化建议

1. **性能优化**: 考虑使用更高效的哈希算法
2. **缓存优化**: 实现分层缓存架构（参考 Antigravity-Manager）
3. **监控**: 添加签名恢复成功率的监控指标
4. **文档**: 更新 API 文档，说明签名编码机制

---

**文档结束**

如有问题，请参考官方版实现或联系开发团队。
