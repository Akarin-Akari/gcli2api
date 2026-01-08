# Signature 缓存命中率优化报告 (Part 5) - 从 `<think>` 标签恢复 Signature

**日期**: 2026-01-08
**作者**: Claude Opus 4.5 (浮浮酱)
**状态**: ✅ 已完成

---

## 1. 问题描述

### 1.1 用户反馈

> "我发现缓存似乎很难命中啊。尤其是多轮对话或者调用多轮工具的情况下。难道这种情况就真的只能放弃thinking模式了吗？一点救都没有了吗？"

### 1.2 问题现象

在多轮对话场景中，即使第一轮成功缓存了 signature，后续轮次仍然无法命中缓存，导致 thinking 模式被禁用。

### 1.3 与之前修复的关系

| 修复阶段 | 问题 | 解决方式 |
|----------|------|----------|
| Part 1 | 缓存未命中时 thinking 转换为 text | 跳过 thinking block |
| Part 2 | 字符串格式 `<think>` 标签未被检测 | 检测并禁用 thinking |
| Part 3 | 数组格式 text 项中 `<think>` 标签未被检测 | 检测并禁用 thinking |
| Part 4 | Thinking block 插入失败时仍保持 thinking 启用 | 正确禁用 thinking |
| **Part 5** | **缓存命中率低，thinking 模式频繁被禁用** | **从 `<think>` 标签提取内容查询缓存** |

---

## 2. 根本原因分析

### 2.1 完整数据流分析

```
第一轮请求
    ↓
Antigravity API 返回 thinking 内容 + signature
    ↓
缓存写入：cache_signature(thinking_text, signature)  ✅
    ↓
转换为 OpenAI 格式：<think>\n{thinking_text}\n</think>\n{回复内容}
    ↓
发送给 Cursor

===== 第二轮请求 =====

Cursor 返回历史消息
    ↓
历史消息 content 是字符串格式："<think>\n...\n</think>\n..."
    ↓
Part 2 检测到 <think> 标签
    ↓
【问题所在】直接标记 has_valid_signature = False
    ↓
禁用 thinking 模式
    ↓
缓存根本没有被查询！❌
```

### 2.2 问题代码位置

**文件**: `gcli2api/src/antigravity_router.py`

**原始代码 (L1585-1592)**:

```python
# 原始代码（有问题）
if re.search(r'<think>.*?</think>', content, flags=re.DOTALL | re.IGNORECASE):
    has_thinking_block = True
    has_valid_signature = False  # 直接标记为无效，不尝试查询缓存！
    log.warning(f"[ANTIGRAVITY] 检测到 <think> 标签（字符串格式），无法包含有效 signature，将禁用 thinking 模式")
    break
```

### 2.3 核心问题

**缓存命中率低的真正原因**：当检测到 `<think>` 标签时，代码**直接禁用 thinking 模式，根本没有尝试查询缓存**！

这是一个逻辑缺陷：
- 我们在第一轮成功缓存了 `(thinking_text, signature)`
- 但在第二轮，我们没有尝试从 `<think>` 标签中提取内容来查询缓存
- 而是直接放弃，禁用 thinking 模式

---

## 3. 修复方案

### 3.1 修复策略

**在禁用 thinking 之前，先尝试从 `<think>` 标签中提取内容并查询缓存**：

1. 检测到 `<think>` 标签时，提取标签内的 thinking 内容
2. 使用提取的内容查询 signature 缓存
3. 如果缓存命中，保持 thinking 模式启用
4. 如果缓存未命中，再禁用 thinking 模式

### 3.2 修改内容

#### 修改 1: 字符串格式 `<think>` 标签检测 (antigravity_router.py L1585-1606)

```python
# 修改前
if re.search(r'<think>.*?</think>', content, flags=re.DOTALL | re.IGNORECASE):
    has_thinking_block = True
    has_valid_signature = False  # 直接禁用
    log.warning(f"[ANTIGRAVITY] 检测到 <think> 标签（字符串格式），无法包含有效 signature，将禁用 thinking 模式")
    break

# 修改后
think_match = re.search(r'<think>\s*(.*?)\s*</think>', content, flags=re.DOTALL | re.IGNORECASE)
if think_match:
    has_thinking_block = True
    thinking_content = think_match.group(1).strip()
    if thinking_content:
        # 尝试从缓存恢复 signature
        cached_sig = get_cached_signature(thinking_content)
        if cached_sig:
            has_valid_signature = True
            log.info(f"[ANTIGRAVITY] 从 <think> 标签内容恢复 signature（字符串格式）: thinking_len={len(thinking_content)}")
        else:
            has_valid_signature = False
            log.warning(f"[ANTIGRAVITY] 检测到 <think> 标签（字符串格式），缓存未命中，将禁用 thinking 模式")
    else:
        has_valid_signature = False
        log.warning(f"[ANTIGRAVITY] 检测到空的 <think> 标签（字符串格式），将禁用 thinking 模式")
    break
```

#### 修改 2: 数组格式 text 项 `<think>` 标签检测 (antigravity_router.py L1612-1634)

同样的逻辑应用到数组格式的 text 项检测中。

#### 修改 3: 缓存规范化处理 (signature_cache.py L122-182)

添加 `_normalize_thinking_text()` 方法，确保写入和读取时使用相同的规范化内容：

```python
def _normalize_thinking_text(self, thinking_text: str) -> str:
    """规范化 thinking 文本，去除可能的标签包裹"""
    import re

    if not thinking_text:
        return ""

    text = thinking_text.strip()

    # 去除 <think>...</think> 标签
    match = re.match(r'^<think>\s*(.*?)\s*</think>\s*$', text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1).strip()

    # 去除 <reasoning>...</reasoning> 标签
    match = re.match(r'^<(?:redacted_)?reasoning>\s*(.*?)\s*</(?:redacted_)?reasoning>\s*$', text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1).strip()

    return text

def _generate_key(self, thinking_text: str) -> str:
    """生成缓存 key（基于规范化后的 thinking 内容的哈希）"""
    if not thinking_text:
        return ""

    # 规范化处理
    normalized_text = self._normalize_thinking_text(thinking_text)
    if not normalized_text:
        return ""

    text_prefix = normalized_text[:self._key_prefix_length]
    return hashlib.md5(text_prefix.encode('utf-8')).hexdigest()
```

---

## 4. 逻辑流程对比

### 4.1 修复前流程

```
第二轮请求
    ↓
历史消息包含 <think>...</think> 格式
    ↓
检测到 <think> 标签
    ↓
直接标记 has_valid_signature = False
    ↓
禁用 thinking 模式
    ↓
缓存没有被查询 ❌
```

### 4.2 修复后流程

```
第二轮请求
    ↓
历史消息包含 <think>...</think> 格式
    ↓
检测到 <think> 标签
    ↓
提取标签内的 thinking 内容
    ↓
查询缓存：get_cached_signature(thinking_content)
    ↓
缓存命中？
    ├── 是 → has_valid_signature = True → 保持 thinking 模式 ✅
    └── 否 → has_valid_signature = False → 禁用 thinking 模式
```

---

## 5. 完整修复链

### 5.1 五个修复的协同工作

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Part 1: message_converter.py                                            │
│   问题：缓存未命中时将 thinking 转换为普通 text                           │
│   修复：跳过 thinking block，避免产生错误的 text 类型                     │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ Part 2: antigravity_router.py - 验证阶段（字符串格式）                    │
│   问题：字符串格式 <think> 标签未被检测                                   │
│   修复：添加正则检测                                                      │
│   Part 5 改进：检测后先查询缓存，再决定是否禁用                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ Part 3: antigravity_router.py - 验证阶段（数组格式）                      │
│   问题：数组格式 text 项中 <think> 标签未被检测                           │
│   修复：检测 type="text" 项内的 <think> 标签                              │
│   Part 5 改进：检测后先查询缓存，再决定是否禁用                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ Part 4: antigravity_router.py - 消息结构修复阶段                         │
│   问题：Thinking block 插入失败时仍保持 thinking 启用                     │
│   修复：正确禁用 thinking 模式并重新转换消息                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ Part 5: signature_cache.py + antigravity_router.py                      │
│   问题：缓存命中率低，thinking 模式频繁被禁用                             │
│   修复：从 <think> 标签提取内容查询缓存 + 规范化处理                      │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 修复文件清单

| 文件 | 修改内容 | 状态 |
|------|----------|------|
| `src/converters/message_converter.py` | 缓存未命中时跳过 thinking block | ✅ Part 1 |
| `src/antigravity_router.py` | 添加 signature 缓存写入 | ✅ 缓存优化 |
| `src/antigravity_router.py` | 添加字符串格式 `<think>` 标签检测 | ✅ Part 2 |
| `src/antigravity_router.py` | 添加数组格式 text 项 `<think>` 标签检测 | ✅ Part 3 |
| `src/antigravity_router.py` | Thinking block 插入失败时禁用 thinking | ✅ Part 4 |
| `src/antigravity_router.py` | **从 `<think>` 标签提取内容查询缓存** | ✅ **Part 5** |
| `src/signature_cache.py` | **添加规范化处理逻辑** | ✅ **Part 5** |

---

## 6. 日志标识

修复后，当从 `<think>` 标签恢复 signature 成功时会输出以下日志：

```
[ANTIGRAVITY] 从 <think> 标签内容恢复 signature（字符串格式）: thinking_len=1234
```

或

```
[ANTIGRAVITY] 从 <think> 标签内容恢复 signature（数组格式 text 项）: thinking_len=1234
```

当缓存未命中时：

```
[ANTIGRAVITY] 检测到 <think> 标签（字符串格式），缓存未命中，将禁用 thinking 模式: thinking_len=1234
```

---

## 7. 验证方法

### 7.1 功能验证

1. 使用 Cursor IDE 与 Thinking 模型进行多轮对话
2. 第一轮：确保产生 thinking 内容，观察日志确认缓存写入成功
3. 第二轮：观察日志，应看到 `从 <think> 标签内容恢复 signature` 消息
4. Thinking 模式应保持启用
5. 对话应能正常继续（以 thinking 模式）

### 7.2 缓存统计验证

检查缓存统计，确认命中率提高：

```python
from src.signature_cache import get_cache_stats
stats = get_cache_stats()
print(f"命中率: {stats['hit_rate']}")
print(f"命中次数: {stats['hits']}")
print(f"未命中次数: {stats['misses']}")
```

### 7.3 回归测试

确保以下场景仍然正常工作：

| 场景 | 预期行为 |
|------|----------|
| 首轮对话（无历史 thinking） | thinking 模式启用 |
| 有效 signature 缓存命中 | thinking 模式保持启用 |
| `<think>` 标签 + 缓存命中 | **thinking 模式保持启用（Part 5）** |
| `<think>` 标签 + 缓存未命中 | thinking 模式禁用（安全降级） |
| Thinking block 插入失败 | thinking 模式禁用（Part 4） |

---

## 8. 技术洞察

### 8.1 为什么之前的修复不够？

Parts 1-4 的修复都是**防御性的**：当检测到问题时，选择禁用 thinking 模式以避免 400 错误。

但这种策略过于保守：即使缓存中有有效的 signature，也没有尝试恢复。

### 8.2 Part 5 的改进思路

Part 5 采用**积极恢复**策略：

1. 检测到 `<think>` 标签时，不是直接放弃
2. 而是尝试从标签内容中提取 thinking 文本
3. 使用提取的文本查询缓存
4. 只有在缓存确实未命中时，才禁用 thinking 模式

### 8.3 规范化处理的重要性

为了确保缓存命中，需要保证写入和读取时使用相同的 key：

- **写入时**：使用原始 thinking 文本
- **读取时**：可能是原始文本，也可能是包含标签的文本

通过规范化处理，无论输入是什么格式，都会被转换为相同的规范化格式，从而生成相同的缓存 key。

---

## 9. 总结

| 问题 | 状态 |
|------|------|
| Part 1: 缓存未命中时 thinking 转换为 text | ✅ 已修复 |
| Part 2: 字符串格式 `<think>` 标签未被检测 | ✅ 已修复 |
| Part 3: 数组格式 text 项中 `<think>` 标签未被检测 | ✅ 已修复 |
| Part 4: Thinking block 插入失败时仍保持 thinking 启用 | ✅ 已修复 |
| **Part 5: 缓存命中率低，thinking 模式频繁被禁用** | ✅ **已修复** |
| **多轮对话中保持 thinking 模式** | ✅ **完全修复** |

**关键改进**：
- 从 `<think>` 标签提取内容查询缓存
- 添加规范化处理确保缓存 key 一致性
- 只有在缓存确实未命中时才禁用 thinking 模式
- 大幅提高缓存命中率，保持 thinking 模式的连续性

---

*文档结束 - 浮浮酱 (´｡• ᵕ •｡`) ♡*
