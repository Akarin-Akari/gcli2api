# Antigravity-Manager 缺陷启示与 gcli2api 自研版对比分析

**文档创建时间**: 2026-01-16  
**作者**: Claude Opus 4.5 (浮浮酱)  
**目标**: 分析 Antigravity-Manager 的缺陷对自研版的启示，找出需要借鉴和避免的错误

---

## 📋 目录

1. [对比总结](#对比总结)
2. [自研版优势](#自研版优势)
3. [自研版相同缺陷](#自研版相同缺陷)
4. [可借鉴点](#可借鉴点)
5. [需要避免的错误](#需要避免的错误)
6. [改进建议](#改进建议)

---

## 1. 对比总结

### 1.1 功能对比表

| 功能 | Antigravity-Manager | gcli2api 自研版 | 状态 |
|------|-------------------|----------------|------|
| **工具ID生成** | ❌ 随机生成（不一致） | ✅ 确定性哈希（一致） | **自研版更好** |
| **工具ID编码机制** | ❌ 缺失 | ✅ 已实现 | **自研版更好** |
| **思维块验证** | ✅ 实现 | ✅ 已实现 | **相同** |
| **思维块清理** | ✅ 实现 | ✅ 已实现 | **相同** |
| **工具ID签名缓存** | ✅ Layer 1 缓存 | ❌ 缺失 | **Antigravity更好** |
| **会话级签名缓存** | ✅ Layer 3 缓存 | ❌ 缺失 | **Antigravity更好** |
| **多层恢复策略** | ✅ 5层优先级 | ⚠️ 2-3层 | **Antigravity更好** |
| **Signature恢复失败处理** | ❌ 直接跳过 | ⚠️ 直接跳过 | **都有问题** |

---

## 2. 自研版优势

### ✅ 优势 #1: 确定性工具ID生成

**自研版实现** (`src/openai_transfer.py:985-990`):
```python
def generate_tool_call_id(name: str, args: dict) -> str:
    """生成确定性的工具调用 ID (基于哈希)"""
    import hashlib
    unique_string = f"{name}{json.dumps(args, sort_keys=True)}"
    hash_object = hashlib.md5(unique_string.encode())
    return f"call_{hash_object.hexdigest()[:24]}"
```

**Antigravity-Manager 问题**:
```rust
// ❌ 随机生成，导致不一致
let tool_id = fc.id.clone().unwrap_or_else(|| {
    format!("{}-{}", fc.name, generate_random_id())  // 随机ID
});
```

**启示**: 
- ✅ **自研版已经避免了 Antigravity-Manager 的核心缺陷**
- ✅ 确定性ID生成确保了流式响应和请求转换时的一致性
- ✅ 这是自研版的一个重大优势

---

### ✅ 优势 #2: 工具ID签名编码机制已实现

**自研版实现** (`src/converters/thoughtSignature_fix.py`):
```python
def encode_tool_id_with_signature(tool_id: str, signature: Optional[str]) -> str:
    """将签名编码到工具ID中"""
    if not signature:
        return tool_id
    return f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}"

def decode_tool_id_and_signature(encoded_id: str) -> Tuple[str, Optional[str]]:
    """从编码ID中提取签名"""
    # ...
```

**使用位置**:
- ✅ 流式响应: `anthropic_streaming.py:494` - 编码签名到工具ID
- ✅ 请求转换: `anthropic_converter.py:646, 668` - 解码签名

**Antigravity-Manager**: ❌ 完全缺失此机制

**启示**:
- ✅ **自研版已经实现了 Antigravity-Manager 缺失的关键功能**
- ✅ 这是自研版的核心优势之一

---

## 3. 自研版相同缺陷

### 🔴 缺陷 #1: 缺少工具ID签名缓存（与 Antigravity-Manager 不同但相关）

**问题描述**:
- 自研版只有 `thinking_text -> signature` 的缓存
- 缺少 `tool_id -> signature` 的缓存（Antigravity-Manager 的 Layer 1）
- 当工具ID编码机制失效时（客户端修改ID），无法通过 tool_id 恢复签名

**代码位置** (`src/signature_cache.py`):
```python
class SignatureCache:
    # ❌ 只有 thinking_text -> signature 的缓存
    self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
    
    def set(self, thinking_text: str, signature: str, model: Optional[str] = None):
        # ❌ 只基于 thinking_text 缓存
        key = self._generate_key(thinking_text)
        # ...
```

**Antigravity-Manager 的优势**:
```rust
// ✅ 有 tool_id -> signature 的缓存（Layer 1）
tool_signatures: Mutex<HashMap<String, CacheEntry<String>>>,

pub fn cache_tool_signature(&self, tool_use_id: &str, signature: String) {
    // 可以直接通过 tool_id 查找签名
}
```

**影响**:
- 当工具ID编码机制失效时，无法恢复签名
- 恢复策略受限

---

### 🔴 缺陷 #2: Signature 恢复失败时直接跳过（与 Antigravity-Manager 相同）

**问题描述**:
- 当所有恢复策略都失败时，直接跳过添加 `thoughtSignature`
- 可能导致 API 拒绝请求

**代码位置** (`src/anthropic_converter.py:660-661`):
```python
if thoughtsignature:
    fc_part["thoughtSignature"] = thoughtsignature
else:
    fc_part["thoughtSignature"] = SKIP_SIGNATURE_VALIDATOR  # ⚠️ 使用占位符
```

**Antigravity-Manager 的问题** (`request.rs:1019-1022`):
```rust
if let Some(sig) = final_sig {
    part["thoughtSignature"] = json!(sig);
}
// ❌ 如果 final_sig 为 None，直接跳过，不添加 thoughtSignature
parts.push(part);  // ❌ 发送没有 signature 的工具调用
```

**对比**:
- 自研版：使用占位符 `SKIP_SIGNATURE_VALIDATOR`（可能被某些API拒绝）
- Antigravity-Manager：直接跳过（肯定被API拒绝）

**启示**:
- ⚠️ **自研版稍好，但仍然有问题**
- 需要增强恢复策略

---

### 🟡 缺陷 #3: 缺少会话级签名隔离（与 Antigravity-Manager 相同）

**问题描述**:
- 自研版使用全局缓存，不同会话可能共享签名
- 可能导致跨会话签名污染

**代码位置** (`src/signature_cache.py`):
```python
class SignatureCache:
    # ❌ 全局缓存，没有会话隔离
    self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
```

**Antigravity-Manager 的优势**:
```rust
// ✅ 有会话级缓存（Layer 3）
session_signatures: Mutex<HashMap<String, CacheEntry<String>>>,
```

**影响**:
- 跨会话签名污染
- 工具循环恢复不准确

---

### 🟡 缺陷 #4: 多层恢复策略不够完善

**自研版当前实现** (`src/anthropic_converter.py:582-610`):
```python
# 优先级 1: 从缓存恢复
cached_signature = get_cached_signature(thinking_text)
if cached_signature:
    final_signature = cached_signature
    recovery_source = "cache"

# 优先级 2: 检查消息提供的签名是否有效
if not final_signature and message_signature and len(message_signature) >= MIN_SIGNATURE_LENGTH:
    final_signature = message_signature
    recovery_source = "message"

# 优先级 3: 使用最近缓存的签名（fallback）
if not final_signature:
    last_sig = get_last_signature()
    if last_sig:
        final_signature = last_sig
        recovery_source = "last_signature"
```

**Antigravity-Manager 的5层策略**:
```rust
// 优先级 1: 客户端提供的签名
// 优先级 2: 上下文中的签名
// 优先级 3: 会话缓存（Layer 3）
// 优先级 4: 工具缓存（Layer 1）
// 优先级 5: 全局存储（已废弃）
```

**对比**:
- 自研版：3层策略（缓存 -> 消息 -> 最近签名）
- Antigravity-Manager：5层策略（更完善）

**启示**:
- ⚠️ **自研版策略不够完善**
- 缺少工具ID缓存查找
- 缺少会话级缓存

---

## 4. 可借鉴点

### 💡 借鉴点 #1: 工具ID签名缓存（Layer 1）

**Antigravity-Manager 实现**:
```rust
// Layer 1: Tool Use ID -> Thinking Signature
tool_signatures: Mutex<HashMap<String, CacheEntry<String>>>,

pub fn cache_tool_signature(&self, tool_use_id: &str, signature: String) {
    // 缓存工具ID到签名的映射
}

pub fn get_tool_signature(&self, tool_use_id: &str) -> Option<String> {
    // 通过工具ID查找签名
}
```

**自研版改进建议**:
```python
class SignatureCache:
    def __init__(self):
        # ✅ 新增：工具ID签名缓存
        self._tool_signatures: Dict[str, CacheEntry] = {}
        self._tool_lock = threading.Lock()
    
    def cache_tool_signature(self, tool_id: str, signature: str) -> bool:
        """缓存工具ID到签名的映射"""
        if not self._is_valid_signature(signature):
            return False
        
        with self._tool_lock:
            self._tool_signatures[tool_id] = CacheEntry(
                signature=signature,
                thinking_text="",  # 工具ID缓存不需要thinking_text
                thinking_text_preview="",
                timestamp=time.time()
            )
        return True
    
    def get_tool_signature(self, tool_id: str) -> Optional[str]:
        """通过工具ID获取签名"""
        with self._tool_lock:
            entry = self._tool_signatures.get(tool_id)
            if entry and not entry.is_expired(self._ttl_seconds):
                return entry.signature
        return None
```

**使用场景**:
- 当工具ID编码机制失效时（客户端修改ID），可以通过 tool_id 直接查找签名
- 作为签名恢复的额外策略

---

### 💡 借鉴点 #2: 会话级签名缓存（Layer 3）

**Antigravity-Manager 实现**:
```rust
// Layer 3: Session ID -> Latest Thinking Signature
session_signatures: Mutex<HashMap<String, CacheEntry<String>>>,

pub fn cache_session_signature(&self, session_id: &str, signature: String) {
    // 缓存会话级签名
}

pub fn get_session_signature(&self, session_id: &str) -> Option<String> {
    // 获取会话级签名
}
```

**自研版改进建议**:
```python
class SignatureCache:
    def __init__(self):
        # ✅ 新增：会话级签名缓存
        self._session_signatures: Dict[str, CacheEntry] = {}
        self._session_lock = threading.Lock()
    
    def cache_session_signature(self, session_id: str, signature: str) -> bool:
        """缓存会话级签名"""
        if not self._is_valid_signature(signature):
            return False
        
        with self._session_lock:
            # 只更新更长的签名（更完整）
            existing = self._session_signatures.get(session_id)
            if not existing or signature.len() > existing.signature.len():
                self._session_signatures[session_id] = CacheEntry(
                    signature=signature,
                    thinking_text="",
                    thinking_text_preview="",
                    timestamp=time.time()
                )
        return True
    
    def get_session_signature(self, session_id: str) -> Optional[str]:
        """获取会话级签名"""
        with self._session_lock:
            entry = self._session_signatures.get(session_id)
            if entry and not entry.is_expired(self._ttl_seconds):
                return entry.signature
        return None
```

**使用场景**:
- 提供会话级别的签名隔离
- 防止跨会话签名污染
- 作为签名恢复的额外策略

---

### 💡 借鉴点 #3: 增强的签名恢复策略

**Antigravity-Manager 的5层策略**:
```rust
let final_sig = signature.as_ref()                    // 1. 客户端
    .or(last_thought_signature.as_ref())               // 2. 上下文
    .cloned()
    .or_else(|| {
        get_session_signature(&session_id)             // 3. 会话缓存
    })
    .or_else(|| {
        get_tool_signature(id)                         // 4. 工具缓存
    })
    .or_else(|| {
        get_thought_signature()                        // 5. 全局存储
    });
```

**自研版改进建议**:
```python
# 在 anthropic_converter.py 中
def recover_signature_for_tool_use(
    tool_id: str,
    signature: Optional[str],
    last_thought_signature: Optional[str],
    session_id: Optional[str]
) -> Optional[str]:
    """多层签名恢复策略"""
    from src.signature_cache import (
        get_cached_signature,
        get_tool_signature,  # ✅ 新增
        get_session_signature,  # ✅ 新增
        get_last_signature
    )
    
    # 优先级 1: 客户端提供的签名
    if signature and len(signature) >= MIN_SIGNATURE_LENGTH:
        return signature
    
    # 优先级 2: 上下文中的签名
    if last_thought_signature and len(last_thought_signature) >= MIN_SIGNATURE_LENGTH:
        return last_thought_signature
    
    # 优先级 3: 从编码的工具ID中解码（自研版特有）
    encoded_id = tool_id
    _, decoded_sig = decode_tool_id_and_signature(encoded_id)
    if decoded_sig:
        return decoded_sig
    
    # 优先级 4: 会话级缓存（✅ 新增）
    if session_id:
        session_sig = get_session_signature(session_id)
        if session_sig:
            return session_sig
    
    # 优先级 5: 工具ID缓存（✅ 新增）
    tool_sig = get_tool_signature(tool_id)
    if tool_sig:
        return tool_sig
    
    # 优先级 6: thinking_text 缓存（自研版特有）
    # 注意：这里需要 thinking_text，但工具调用时可能没有
    # 所以这个策略在工具调用场景下可能不适用
    
    # 优先级 7: 最近签名（fallback）
    return get_last_signature()
```

---

## 5. 需要避免的错误

### ❌ 错误 #1: 工具ID生成不一致（Antigravity-Manager 的错误）

**Antigravity-Manager 的错误**:
```rust
// ❌ 随机生成，导致不一致
let tool_id = fc.id.clone().unwrap_or_else(|| {
    format!("{}-{}", fc.name, generate_random_id())
});
```

**自研版现状**: ✅ **已经避免**
- 使用确定性哈希生成：`generate_tool_call_id(name, args)`
- 确保流式响应和请求转换时生成相同的ID

**保持优势**:
- ✅ 继续使用确定性ID生成
- ✅ 不要改为随机生成

---

### ❌ 错误 #2: Signature 恢复失败时直接跳过（两个版本都有）

**Antigravity-Manager 的错误**:
```rust
if let Some(sig) = final_sig {
    part["thoughtSignature"] = json!(sig);
}
// ❌ 如果 final_sig 为 None，直接跳过，不添加 thoughtSignature
parts.push(part);  // ❌ 发送没有 signature 的工具调用
```

**自研版现状**:
```python
if thoughtsignature:
    fc_part["thoughtSignature"] = thoughtsignature
else:
    fc_part["thoughtSignature"] = SKIP_SIGNATURE_VALIDATOR  # ⚠️ 使用占位符
```

**改进建议**:
```python
# ✅ 增强恢复策略
final_sig = recover_signature_for_tool_use(
    tool_id=original_id,
    signature=thoughtsignature,
    last_thought_signature=last_thought_signature,
    session_id=session_id
)

if final_sig:
    fc_part["thoughtSignature"] = final_sig
else:
    # ⚠️ 如果所有策略都失败，记录严重警告
    log.error(
        f"[CRITICAL] No signature found for tool call (tool_id: {original_id}, name: {name}). "
        f"Request may be rejected by API."
    )
    # ✅ 使用占位符作为最后手段（某些API可能接受）
    fc_part["thoughtSignature"] = SKIP_SIGNATURE_VALIDATOR
```

---

### ❌ 错误 #3: 工具ID和签名缓存不匹配（Antigravity-Manager 的错误）

**Antigravity-Manager 的错误**:
- 流式响应时用生成的 tool_id 缓存签名
- 请求转换时用客户端发送的 tool_id 查找缓存
- 如果 tool_id 不一致，缓存查找失败

**自研版现状**: ✅ **已经避免**
- 使用确定性ID生成，确保 tool_id 一致
- 使用工具ID编码机制，签名直接编码在ID中

**保持优势**:
- ✅ 继续使用确定性ID生成
- ✅ 继续使用工具ID编码机制
- ✅ 添加工具ID缓存作为额外保障

---

## 6. 改进建议

### 6.1 立即改进（P0）

#### 改进 #1: 添加工具ID签名缓存

**文件**: `src/signature_cache.py`

```python
class SignatureCache:
    def __init__(self):
        # 现有缓存
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        
        # ✅ 新增：工具ID签名缓存
        self._tool_signatures: Dict[str, CacheEntry] = {}
        self._tool_lock = threading.Lock()
    
    def cache_tool_signature(self, tool_id: str, signature: str) -> bool:
        """缓存工具ID到签名的映射"""
        if not self._is_valid_signature(signature):
            return False
        
        with self._tool_lock:
            self._tool_signatures[tool_id] = CacheEntry(
                signature=signature,
                thinking_text="",
                thinking_text_preview="",
                timestamp=time.time()
            )
        return True
    
    def get_tool_signature(self, tool_id: str) -> Optional[str]:
        """通过工具ID获取签名"""
        with self._tool_lock:
            entry = self._tool_signatures.get(tool_id)
            if entry and not entry.is_expired(self._ttl_seconds):
                return entry.signature
        return None
```

**使用位置**:
- 流式响应时缓存：`anthropic_streaming.py`
- 请求转换时查找：`anthropic_converter.py`

---

#### 改进 #2: 添加会话级签名缓存

**文件**: `src/signature_cache.py`

```python
class SignatureCache:
    def __init__(self):
        # ✅ 新增：会话级签名缓存
        self._session_signatures: Dict[str, CacheEntry] = {}
        self._session_lock = threading.Lock()
    
    def cache_session_signature(self, session_id: str, signature: str) -> bool:
        """缓存会话级签名"""
        if not self._is_valid_signature(signature):
            return False
        
        with self._session_lock:
            # 只更新更长的签名（更完整）
            existing = self._session_signatures.get(session_id)
            if not existing or len(signature) > len(existing.signature):
                self._session_signatures[session_id] = CacheEntry(
                    signature=signature,
                    thinking_text="",
                    thinking_text_preview="",
                    timestamp=time.time()
                )
        return True
    
    def get_session_signature(self, session_id: str) -> Optional[str]:
        """获取会话级签名"""
        with self._session_lock:
            entry = self._session_signatures.get(session_id)
            if entry and not entry.is_expired(self._ttl_seconds):
                return entry.signature
        return None
```

---

#### 改进 #3: 增强签名恢复策略

**文件**: `src/anthropic_converter.py`

```python
def recover_signature_for_tool_use(
    tool_id: str,
    encoded_tool_id: str,
    signature: Optional[str],
    last_thought_signature: Optional[str],
    session_id: Optional[str] = None
) -> Optional[str]:
    """
    多层签名恢复策略（用于工具调用）
    
    优先级：
    1. 客户端提供的签名
    2. 上下文中的签名
    3. 从编码的工具ID中解码（自研版特有）
    4. 会话级缓存
    5. 工具ID缓存
    6. 最近签名（fallback）
    """
    from src.signature_cache import (
        get_tool_signature,
        get_session_signature,
        get_last_signature
    )
    from src.converters.thoughtSignature_fix import decode_tool_id_and_signature
    
    # 优先级 1: 客户端提供的签名
    if signature and len(signature) >= MIN_SIGNATURE_LENGTH:
        return signature
    
    # 优先级 2: 上下文中的签名
    if last_thought_signature and len(last_thought_signature) >= MIN_SIGNATURE_LENGTH:
        return last_thought_signature
    
    # 优先级 3: 从编码的工具ID中解码（自研版特有优势）
    _, decoded_sig = decode_tool_id_and_signature(encoded_tool_id)
    if decoded_sig and len(decoded_sig) >= MIN_SIGNATURE_LENGTH:
        log.debug(f"[SIGNATURE_RECOVERY] Recovered from encoded tool_id")
        return decoded_sig
    
    # 优先级 4: 会话级缓存
    if session_id:
        session_sig = get_session_signature(session_id)
        if session_sig:
            log.debug(f"[SIGNATURE_RECOVERY] Recovered from session cache")
            return session_sig
    
    # 优先级 5: 工具ID缓存
    tool_sig = get_tool_signature(tool_id)
    if tool_sig:
        log.debug(f"[SIGNATURE_RECOVERY] Recovered from tool_id cache")
        return tool_sig
    
    # 优先级 6: 最近签名（fallback）
    last_sig = get_last_signature()
    if last_sig:
        log.warning(f"[SIGNATURE_RECOVERY] Using last signature as fallback")
        return last_sig
    
    return None
```

**使用位置** (`src/anthropic_converter.py:642-663`):
```python
elif item_type == "tool_use":
    encoded_id = item.get("id") or ""
    original_id, thoughtsignature = decode_tool_id_and_signature(encoded_id)
    
    # ✅ 增强恢复策略
    final_sig = recover_signature_for_tool_use(
        tool_id=original_id,
        encoded_tool_id=encoded_id,
        signature=thoughtsignature,
        last_thought_signature=last_thought_signature,
        session_id=session_id  # 需要从请求中提取
    )
    
    fc_part: Dict[str, Any] = {
        "functionCall": {
            "id": original_id,
            "name": item.get("name"),
            "args": item.get("input", {}) or {},
        },
    }
    
    if final_sig:
        fc_part["thoughtSignature"] = final_sig
    else:
        # ⚠️ 所有策略都失败，使用占位符
        log.error(f"[CRITICAL] No signature found for tool call: {original_id}")
        fc_part["thoughtSignature"] = SKIP_SIGNATURE_VALIDATOR
    
    parts.append(fc_part)
```

---

### 6.2 中期改进（P1）

#### 改进 #4: 在流式响应时缓存工具签名

**文件**: `src/anthropic_streaming.py`

```python
# 在处理工具调用时
if "functionCall" in part:
    fc = part.get("functionCall", {}) or {}
    original_id = generate_tool_call_id(tool_name, tool_args)
    thoughtsignature = part.get("thoughtSignature")
    
    # 编码签名到工具ID
    encoded_id = encode_tool_id_with_signature(original_id, thoughtsignature)
    
    # ✅ 新增：缓存工具ID签名
    if thoughtsignature:
        from src.signature_cache import cache_tool_signature
        cache_tool_signature(original_id, thoughtsignature)
        
        # ✅ 新增：缓存会话级签名
        if session_id:
            from src.signature_cache import cache_session_signature
            cache_session_signature(session_id, thoughtsignature)
    
    # 发送编码后的ID
    content.append({
        "type": "tool_use",
        "id": encoded_id,
        "name": tool_name,
        "input": tool_args,
    })
```

---

### 6.3 长期改进（P2）

#### 改进 #5: 添加签名有效性验证

参考 Antigravity-Manager 的验证逻辑，但避免其过于宽松的问题。

---

## 7. 关键启示总结

### ✅ 自研版已经避免的错误

1. **工具ID生成不一致** ✅
   - 自研版使用确定性哈希，确保一致性
   - Antigravity-Manager 使用随机生成，导致不一致

2. **缺少工具ID编码机制** ✅
   - 自研版已实现编码/解码机制
   - Antigravity-Manager 完全缺失

### ⚠️ 自研版需要改进的地方

1. **缺少工具ID签名缓存** ⚠️
   - 需要添加 Layer 1 缓存（tool_id -> signature）

2. **缺少会话级签名隔离** ⚠️
   - 需要添加 Layer 3 缓存（session_id -> signature）

3. **签名恢复策略不够完善** ⚠️
   - 需要增强为5-6层策略

4. **Signature恢复失败处理** ⚠️
   - 需要增强恢复策略，减少失败率

### 💡 可以借鉴的优势

1. **三层缓存架构** 💡
   - Layer 1: tool_id -> signature
   - Layer 2: signature -> model_family（可选）
   - Layer 3: session_id -> signature

2. **多层恢复策略** 💡
   - 5层优先级恢复机制
   - 提高签名恢复成功率

3. **会话级隔离** 💡
   - 防止跨会话签名污染
   - 提高工具循环恢复准确性

---

## 8. 实施优先级

| 改进项 | 优先级 | 预计时间 | 依赖 |
|--------|--------|---------|------|
| 添加工具ID签名缓存 | P0 | 2-3小时 | 无 |
| 增强签名恢复策略 | P0 | 2-3小时 | 工具ID缓存 |
| 添加会话级签名缓存 | P1 | 2-3小时 | 无 |
| 在流式响应时缓存工具签名 | P1 | 1-2小时 | 工具ID缓存 |
| 添加签名有效性验证 | P2 | 2-3小时 | 无 |

---

## 9. 注意事项

1. **保持现有优势**:
   - ✅ 不要改变确定性ID生成机制
   - ✅ 不要移除工具ID编码机制

2. **向后兼容**:
   - 新增缓存不影响现有功能
   - 保持现有API接口不变

3. **性能考虑**:
   - 多层缓存查找可能略微影响性能
   - 需要监控缓存命中率

4. **测试覆盖**:
   - 确保所有新功能都有测试
   - 特别是工具循环场景

---

**文档结束**

总结：自研版在工具ID生成和编码机制方面已经优于 Antigravity-Manager，但在缓存架构和恢复策略方面需要借鉴其优势。
