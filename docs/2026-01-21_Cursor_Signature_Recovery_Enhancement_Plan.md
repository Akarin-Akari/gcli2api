# Cursor 签名恢复能力增强方案

**日期**: 2026-01-21
**作者**: Claude Opus 4.5 (浮浮酱)
**状态**: 📋 设计阶段

---

## 目录

1. [背景与问题分析](#背景与问题分析)
2. [现有架构分析](#现有架构分析)
3. [增强方案设计](#增强方案设计)
   - [Session Cache 增强](#session-cache-增强)
   - [Tool Cache 增强](#tool-cache-增强)
   - [长 tool_id 测试方案](#长-tool_id-测试方案)
4. [实现优先级](#实现优先级)
5. [风险评估](#风险评估)
6. [附录：代码示例](#附录代码示例)

---

## 背景与问题分析

### 问题描述

Cursor IDE 客户端在使用 Claude Extended Thinking 模式时，存在以下特殊行为：

| 行为 | 影响 |
|------|------|
| 截断 thinking 内容 | thinking 块可能被截断，导致签名验证失败 |
| 不保留 thoughtSignature 字段 | 历史消息中的 thinking 块丢失签名 |
| tool_result 必须精确匹配 tool_use_id | 如果 tool_id 被修改，工具链可能断裂 |

### 当前架构决策

为避免兼容性问题，当前架构对 Cursor 禁用了 **Layer 3 (Encoded Tool ID)** 签名恢复：

```python
# antigravity_router.py:341
CLI_CLIENTS_FOR_SIGNATURE_ENCODING = {"claude_code", "cline", "aider", "continue_dev", "openai_api"}
should_encode_signature = client_type in CLI_CLIENTS_FOR_SIGNATURE_ENCODING
# cursor 和 windsurf 不在列表中，签名编码被禁用
```

### 增强目标

在保持 Layer 3 禁用的前提下，增强其他恢复层的能力，提高 Cursor 场景下的签名恢复成功率。

---

## 现有架构分析

### 6层签名恢复策略

| 层级 | 名称 | Cursor 状态 | 说明 |
|------|------|-------------|------|
| Layer 1 | Client Signature | ⚠️ 不可靠 | Cursor 可能不保留 |
| Layer 2 | Context Signature | ✅ 可用 | 上下文中的 last_thought_signature |
| Layer 3 | Encoded Tool ID | ❌ 禁用 | 避免长 tool_id 兼容性问题 |
| Layer 4 | Session Cache | ✅ 可用 | 基于 session_id 精确匹配 |
| Layer 5 | Tool Cache | ✅ 可用 | 基于 tool_id 精确匹配 |
| Layer 6 | Last Signature | ✅ 可用 | 最近缓存的签名 |

### Session Cache 现状

**指纹生成逻辑** (`signature_cache.py:978`):
```python
def generate_session_fingerprint(messages: List[Dict]) -> str:
    # 基于第一条用户消息的内容
    # 使用 MD5 哈希的前 16 位
```

**问题**:
- 仅基于第一条用户消息，后续消息变化不影响指纹
- 精确匹配，无模糊匹配能力
- 统一 TTL (3600秒)，未针对客户端优化

### Tool Cache 现状

**缓存逻辑** (`signature_cache.py:225`):
```python
def cache_tool_signature(self, tool_id: str, signature: str) -> bool:
    # 基于 tool_id 精确匹配
    # TTL: 3600秒
```

**问题**:
- 精确匹配，Cursor 修改 tool_id 会导致缓存失效
- 无模糊匹配或前缀匹配能力
- 无时间窗口 fallback

---

## 增强方案设计

### Session Cache 增强

#### 方案 S1: 多级 Session 指纹 (P1)

**设计思路**: 生成多个维度的指纹，提高匹配概率

```python
def generate_multi_level_fingerprint(messages: List[Dict]) -> Dict[str, str]:
    """
    生成多级会话指纹

    Returns:
        {
            "first_user": "abc123...",   # 第一条用户消息
            "last_n": "def456...",       # 最后 N 条消息
            "full": "ghi789..."          # 全部消息摘要
        }
    """
    return {
        "first_user": generate_session_fingerprint(messages),
        "last_n": generate_last_n_fingerprint(messages, n=3),
        "full": generate_full_fingerprint(messages)
    }

def generate_last_n_fingerprint(messages: List[Dict], n: int = 3) -> str:
    """基于最后 N 条消息生成指纹"""
    last_n = messages[-n:] if len(messages) >= n else messages
    content = ""
    for msg in last_n:
        role = msg.get("role", "")
        msg_content = msg.get("content", "")
        if isinstance(msg_content, list):
            msg_content = " ".join(
                item.get("text", "") for item in msg_content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        content += f"{role}:{msg_content[:100]}"
    return hashlib.md5(content.encode()).hexdigest()[:16]
```

**查找逻辑**:
```python
def get_session_signature_multi_level(fingerprints: Dict[str, str]) -> Optional[str]:
    """多级指纹查找"""
    # 优先级: first_user > last_n > full
    for level in ["first_user", "last_n", "full"]:
        fp = fingerprints.get(level)
        if fp:
            sig = get_session_signature(fp)
            if sig:
                log.info(f"[SESSION_CACHE] 多级指纹命中: level={level}")
                return sig
    return None
```

#### 方案 S2: 客户端特定 TTL (P0)

**设计思路**: 为不同客户端配置不同的缓存有效期

```python
# 客户端 TTL 配置
CLIENT_TTL_CONFIG = {
    "cursor": 7200,       # 2小时 - IDE 客户端更长
    "windsurf": 7200,     # 2小时
    "claude_code": 3600,  # 1小时 - CLI 标准
    "cline": 3600,
    "aider": 3600,
    "default": 3600
}

def get_ttl_for_client(client_type: str) -> int:
    """获取客户端特定的 TTL"""
    return CLIENT_TTL_CONFIG.get(client_type, CLIENT_TTL_CONFIG["default"])
```

**实现位置**: `signature_cache.py` 的 `is_expired()` 方法

#### 方案 S3: 消息内容相似度匹配 (P2)

**设计思路**: 当精确匹配失败时，尝试基于内容相似度匹配

```python
def get_session_signature_fuzzy(
    session_id: str,
    thinking_text: str,
    similarity_threshold: float = 0.8
) -> Optional[str]:
    """模糊匹配 Session 签名"""
    # 1. 先尝试精确匹配
    sig = get_session_signature(session_id)
    if sig:
        return sig

    # 2. 遍历缓存，查找相似的 thinking_text
    with _session_lock:
        for cached_id, entry in _session_signatures.items():
            if entry.thinking_text:
                similarity = calculate_similarity(
                    thinking_text[:500],
                    entry.thinking_text[:500]
                )
                if similarity >= similarity_threshold:
                    log.info(f"[SESSION_CACHE] 模糊匹配成功: similarity={similarity:.2f}")
                    return entry.signature

    return None

def calculate_similarity(text1: str, text2: str) -> float:
    """计算文本相似度 (简化版)"""
    # 使用 Jaccard 相似度
    set1 = set(text1.split())
    set2 = set(text2.split())
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0
```

---

### Tool Cache 增强

#### 方案 T1: Tool ID 前缀匹配 (P1)

**设计思路**: 当精确匹配失败时，尝试前缀匹配

```python
def get_tool_signature_fuzzy(tool_id: str) -> Optional[str]:
    """模糊匹配 Tool 签名"""
    # 1. 精确匹配
    sig = get_tool_signature(tool_id)
    if sig:
        return sig

    # 2. 提取基础 ID（去除可能的后缀）
    base_id = extract_base_tool_id(tool_id)
    if base_id != tool_id:
        sig = get_tool_signature(base_id)
        if sig:
            log.info(f"[TOOL_CACHE] 基础ID匹配成功: {tool_id} -> {base_id}")
            return sig

    # 3. 前缀匹配（查找以相同前缀开头的条目）
    prefix = tool_id[:20]  # 取前 20 个字符作为前缀
    with _tool_lock:
        for cached_id, entry in _tool_signatures.items():
            if cached_id.startswith(prefix):
                log.info(f"[TOOL_CACHE] 前缀匹配成功: {tool_id} ~ {cached_id}")
                return entry.signature

    return None

def extract_base_tool_id(tool_id: str) -> str:
    """提取基础 Tool ID"""
    # 去除可能的后缀（如 _1, _2, _suffix 等）
    import re
    # 匹配 call_xxx 格式
    match = re.match(r'^(call_[a-zA-Z0-9]+)', tool_id)
    if match:
        return match.group(1)
    return tool_id
```

#### 方案 T2: 工具名称维度缓存 (P2)

**设计思路**: 除了 tool_id，还按工具名称缓存

```python
# 新增工具名称缓存
_tool_name_signatures: Dict[str, CacheEntry] = {}
_tool_name_lock = threading.Lock()

def cache_tool_signature_by_name(tool_name: str, signature: str) -> bool:
    """按工具名称缓存签名"""
    if not tool_name or not signature:
        return False

    with _tool_name_lock:
        _tool_name_signatures[tool_name] = CacheEntry(
            signature=signature,
            thinking_text="",
            thinking_text_preview="",
            timestamp=time.time()
        )
        log.debug(f"[TOOL_CACHE] 工具名称缓存成功: name={tool_name}")
    return True

def get_tool_signature_by_name(tool_name: str) -> Optional[str]:
    """通过工具名称获取签名"""
    with _tool_name_lock:
        entry = _tool_name_signatures.get(tool_name)
        if entry and not entry.is_expired(ttl_seconds):
            log.info(f"[TOOL_CACHE] 工具名称缓存命中: name={tool_name}")
            return entry.signature
    return None
```

#### 方案 T3: 时间窗口 Fallback (P0)

**设计思路**: 获取最近 N 分钟内缓存的任意签名作为最后 fallback

```python
def get_recent_signature(time_window_seconds: int = 300) -> Optional[str]:
    """
    获取最近 N 分钟内的任意签名

    作为最后的 fallback，当所有其他恢复层都失败时使用。

    Args:
        time_window_seconds: 时间窗口（默认 5 分钟）

    Returns:
        最近的签名，如果没有则返回 None
    """
    now = time.time()

    # 从 Tool Cache 查找
    with _tool_lock:
        for entry in sorted(
            _tool_signatures.values(),
            key=lambda e: e.timestamp,
            reverse=True
        ):
            if now - entry.timestamp < time_window_seconds:
                log.info(f"[FALLBACK] 时间窗口匹配成功: age={now - entry.timestamp:.1f}s")
                return entry.signature

    # 从 Session Cache 查找
    with _session_lock:
        for entry in sorted(
            _session_signatures.values(),
            key=lambda e: e.timestamp,
            reverse=True
        ):
            if now - entry.timestamp < time_window_seconds:
                log.info(f"[FALLBACK] Session 时间窗口匹配成功: age={now - entry.timestamp:.1f}s")
                return entry.signature

    return None
```

---

### 长 tool_id 测试方案

#### 测试目标

验证 Cursor 客户端对不同长度 tool_id 的处理行为，确定是否可以安全启用 Layer 3。

#### 测试方案 1: 单元测试

```python
# tests/test_cursor_tool_id_length.py

import pytest
from src.converters.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature,
    THOUGHT_SIGNATURE_SEPARATOR
)

class TestToolIdLength:
    """测试不同长度的 tool_id 编码"""

    @pytest.mark.parametrize("sig_length,expected_total", [
        (50, 75),    # call_abc123 (11) + __thought__ (11) + 50 = 72
        (100, 125),
        (200, 225),  # 标准签名长度
        (300, 325),
        (500, 525),
    ])
    def test_encoded_length(self, sig_length, expected_total):
        """测试编码后的总长度"""
        base_id = "call_abc123"
        signature = "x" * sig_length

        encoded = encode_tool_id_with_signature(base_id, signature)

        assert len(encoded) >= expected_total - 10  # 允许一定误差
        assert THOUGHT_SIGNATURE_SEPARATOR in encoded
        assert encoded.startswith(base_id)

    def test_roundtrip(self):
        """测试编码-解码往返"""
        base_id = "call_abc123"
        signature = "y" * 200

        encoded = encode_tool_id_with_signature(base_id, signature)
        decoded_id, decoded_sig = decode_tool_id_and_signature(encoded)

        assert decoded_id == base_id
        assert decoded_sig == signature

    def test_special_characters(self):
        """测试特殊字符处理"""
        base_id = "call_abc-123_def"
        signature = "EqQBCgIYAxoMCIqF" + "=" * 50  # Base64 风格

        encoded = encode_tool_id_with_signature(base_id, signature)
        decoded_id, decoded_sig = decode_tool_id_and_signature(encoded)

        assert decoded_id == base_id
        assert decoded_sig == signature
```

#### 测试方案 2: 集成测试

```python
# tests/integration/test_cursor_integration.py

import asyncio
import aiohttp
import json

class TestCursorIntegration:
    """Cursor 集成测试（需要真实环境）"""

    BASE_URL = "http://localhost:8000"  # gcli2api 服务地址

    async def test_tool_id_roundtrip(self):
        """测试 Cursor 对长 tool_id 的往返处理"""
        # 模拟 Cursor 请求
        headers = {
            "User-Agent": "cursor/1.0.0",
            "Content-Type": "application/json"
        }

        # 发送包含工具调用的请求
        request_body = {
            "model": "claude-opus-4-5",
            "messages": [
                {"role": "user", "content": "Read the file /test.txt"}
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "description": "Read a file",
                        "parameters": {"type": "object"}
                    }
                }
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.BASE_URL}/v1/chat/completions",
                headers=headers,
                json=request_body
            ) as resp:
                result = await resp.json()

                # 检查响应中的 tool_use id
                if "choices" in result:
                    message = result["choices"][0].get("message", {})
                    tool_calls = message.get("tool_calls", [])

                    for tool_call in tool_calls:
                        tool_id = tool_call.get("id", "")
                        print(f"Tool ID length: {len(tool_id)}")
                        print(f"Tool ID: {tool_id[:100]}...")

                        # 验证 tool_id 格式
                        assert len(tool_id) > 0
```

#### 测试方案 3: 日志分析

```python
# 在 antigravity_router.py 中添加日志

def log_tool_id_metrics(
    client_type: str,
    sent_id: str,
    received_id: Optional[str] = None
):
    """记录 tool_id 指标"""
    metrics = {
        "client_type": client_type,
        "sent_id_length": len(sent_id),
        "sent_id_preview": sent_id[:50],
        "has_signature_encoding": "__thought__" in sent_id,
    }

    if received_id:
        metrics["received_id_length"] = len(received_id)
        metrics["id_match"] = sent_id == received_id
        metrics["length_diff"] = len(sent_id) - len(received_id)

    log.info(f"[TOOL_ID_METRICS] {json.dumps(metrics)}")
```

---

## 实现优先级

### P0 - 高优先级（低风险、高收益）

| 方案 | 描述 | 预计工时 | 风险 |
|------|------|----------|------|
| S2 | 客户端特定 TTL | 2h | 极低 |
| T3 | 时间窗口 Fallback | 2h | 低 |

### P1 - 中优先级（中等复杂度）

| 方案 | 描述 | 预计工时 | 风险 |
|------|------|----------|------|
| S1 | 多级 Session 指纹 | 4h | 低 |
| T1 | Tool ID 前缀匹配 | 3h | 中 |

### P2 - 低优先级（需要更多验证）

| 方案 | 描述 | 预计工时 | 风险 |
|------|------|----------|------|
| S3 | 消息内容相似度匹配 | 6h | 中 |
| T2 | 工具名称维度缓存 | 4h | 中 |
| 长 tool_id 测试 | 验证 Cursor 行为 | 8h | 低 |

---

## 风险评估

### P0 方案风险

| 方案 | 风险 | 缓解措施 |
|------|------|----------|
| S2 客户端 TTL | 几乎无风险 | 配置化，可随时调整 |
| T3 时间窗口 | 可能返回不匹配的签名 | 仅作为最后 fallback，时间窗口设置较短 |

### P1 方案风险

| 方案 | 风险 | 缓解措施 |
|------|------|----------|
| S1 多级指纹 | 增加缓存复杂度 | 渐进式实现，先添加 last_n |
| T1 前缀匹配 | 可能误匹配 | 前缀长度设置较长（20字符） |

### P2 方案风险

| 方案 | 风险 | 缓解措施 |
|------|------|----------|
| S3 相似度匹配 | 性能开销、误匹配 | 设置高阈值（0.8），限制遍历数量 |
| T2 工具名称缓存 | 同名工具不同签名 | 结合时间戳，优先使用最新 |

---

## 附录：代码示例

### 完整的增强恢复流程

```python
def recover_signature_enhanced(
    thinking_text: str,
    tool_id: Optional[str] = None,
    session_id: Optional[str] = None,
    client_type: str = "unknown",
    context_signature: Optional[str] = None
) -> RecoveryResult:
    """
    增强版签名恢复（针对 Cursor 优化）

    恢复顺序:
    1. Context Signature
    2. Session Cache (多级指纹)
    3. Tool Cache (模糊匹配)
    4. 时间窗口 Fallback
    5. Last Signature
    """

    # Layer 2: Context Signature
    if context_signature and is_valid_signature(context_signature):
        return RecoveryResult(
            signature=context_signature,
            source=RecoverySource.CONTEXT
        )

    # Layer 4: Session Cache (多级指纹)
    if session_id:
        sig = get_session_signature(session_id)
        if sig:
            return RecoveryResult(
                signature=sig,
                source=RecoverySource.SESSION_CACHE
            )

    # Layer 5: Tool Cache (模糊匹配)
    if tool_id:
        sig = get_tool_signature_fuzzy(tool_id)
        if sig:
            return RecoveryResult(
                signature=sig,
                source=RecoverySource.TOOL_CACHE
            )

    # 时间窗口 Fallback
    ttl = get_ttl_for_client(client_type)
    sig = get_recent_signature(time_window_seconds=ttl // 2)
    if sig:
        return RecoveryResult(
            signature=sig,
            source=RecoverySource.LAST_SIGNATURE
        )

    # Layer 6: Last Signature
    sig = get_last_signature()
    if sig:
        return RecoveryResult(
            signature=sig,
            source=RecoverySource.LAST_SIGNATURE
        )

    return RecoveryResult(
        signature=None,
        source=RecoverySource.NONE
    )
```

---

## 相关文档

- [跨模型 Thinking 隔离修复报告](./2026-01-21_Cross_Model_Thinking_Isolation_Report.md)
- [Signature 恢复修复报告](./2026-01-20_Signature_Recovery_Fix_Report.md)
- [Thinking Signature 分析报告](./2026-01-20_Thinking_Signature_Analysis_Report.md)

---

**维护者**: 浮浮酱 (Claude Opus 4.5)
