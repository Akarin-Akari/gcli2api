#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新修复报告文档到 v1.4
添加问题5：max_tokens=None 时缺少默认值
"""

import os

def main():
    doc_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "docs", "2026-01-11_上下文截断问题二次修复报告.md"
    )

    print(f"[INFO] 更新文档: {doc_file}")

    with open(doc_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. 更新问题数量
    content = content.replace(
        "用户报告优化后仍存在四个问题：",
        "用户报告优化后仍存在五个问题："
    )

    # 2. 添加问题5描述（在问题4后面）
    problem5_desc = '''
**问题 5**: max_tokens=None 时输出仍被截断（缺少默认值）
```
promptTokenCount: 81,690
candidatesTokenCount: 4,096
finishReason: MAX_TOKENS
日志: thinking=False, 无 "maxOutputTokens 低于下限" 日志（if 块被跳过）
```

---'''

    content = content.replace(
        '''日志: thinking=False, max_tokens=4096 (客户端原值直接使用，无下限保护)
```

---''',
        '''日志: thinking=False, max_tokens=4096 (客户端原值直接使用，无下限保护)
```
''' + problem5_desc
    )

    # 3. 添加问题5根因分析（在2.4后面）
    problem5_analysis = '''
### 2.5 问题 5 根因：max_tokens=None 时缺少默认值

**这是之前所有修复都没生效的真正根因！**

原始代码结构（`anthropic_converter.py:753-773`）：
```python
max_tokens = payload.get("max_tokens")  # 返回 None
if max_tokens is not None:              # 条件为 False，整个 if 块被跳过！
    # 上限保护...
    # 下限保护...
    config["maxOutputTokens"] = max_tokens
# 没有 else 分支！config 中没有 maxOutputTokens
```

问题分析：
- 客户端（如 Claude Code）可能**不传** `max_tokens` 参数
- `payload.get("max_tokens")` 返回 `None`
- 整个 `if` 块被跳过，`config["maxOutputTokens"]` 没有被设置
- 下游 API（Antigravity/Gemini）使用默认值 **4096**
- 之前添加的下限保护代码根本没有执行！

**结论**: 代码只处理了 `max_tokens is not None` 的情况，缺少 `else` 分支来设置默认值。

'''

    # 在问题4分析后插入
    content = content.replace(
        "**结论**: thinking=False 场景缺少下限保护，客户端的小 max_tokens 值被直接使用。\n\n---",
        "**结论**: thinking=False 场景缺少下限保护，客户端的小 max_tokens 值被直接使用。\n" + problem5_analysis + "---"
    )

    # 4. 添加修复方案3.5
    fix_solution = '''
### 3.5 修复问题 5：添加 max_tokens 默认值

**文件**: `gcli2api/src/anthropic_converter.py:753-781`

**修改前**:
```python
max_tokens = payload.get("max_tokens")
if max_tokens is not None:
    # 上限保护
    # 下限保护
    config["maxOutputTokens"] = max_tokens
# 没有 else 分支！

stop_sequences = payload.get("stop_sequences")
```

**修改后**:
```python
max_tokens = payload.get("max_tokens")
if max_tokens is not None:
    # 上限保护
    # 下限保护
    config["maxOutputTokens"] = max_tokens
else:
    # [FIX 2026-01-12] 客户端未传 max_tokens 时，使用默认值保证足够输出空间
    DEFAULT_MAX_OUTPUT_TOKENS = 16384
    log.info(f"[ANTHROPIC CONVERTER] max_tokens 未指定，使用默认值: {DEFAULT_MAX_OUTPUT_TOKENS}")
    config["maxOutputTokens"] = DEFAULT_MAX_OUTPUT_TOKENS

stop_sequences = payload.get("stop_sequences")
```

'''

    # 在修复方案3.4后插入
    content = content.replace(
        "    config[\"maxOutputTokens\"] = max_tokens\n```\n\n---\n\n## 4. 修复效果",
        "    config[\"maxOutputTokens\"] = max_tokens\n```\n" + fix_solution + "---\n\n## 4. 修复效果"
    )

    # 5. 添加修复效果4.4
    fix_effect = '''
### 4.4 问题 5 - max_tokens=None 时的默认值

| 场景 | 客户端 max_tokens | 修复前 | 修复后 |
|------|-------------------|--------|--------|
| 未传参数 | None | 4,096（API默认）| **16,384** |
| Claude Code | 未传 | 4,096 | **16,384** |
| 自定义客户端 | 未传 | 4,096 | **16,384** |

**关键改进**：
- 无论客户端是否传 `max_tokens`，都保证至少 16,384 tokens 输出空间
- 添加日志：`max_tokens 未指定，使用默认值: 16384`
- 彻底解决了之前所有修复都不生效的问题

'''

    # 在修复效果4.3后插入
    content = content.replace(
        "- 足够完成 MD 文档写入任务\n\n---\n\n## 5. 文件变更清单",
        "- 足够完成 MD 文档写入任务\n" + fix_effect + "---\n\n## 5. 文件变更清单"
    )

    # 6. 更新文件变更清单
    content = content.replace(
        "| `scripts/fix_max_tokens_floor.py` | 新增 | **关键** - max_tokens 下限保护修复脚本 |",
        "| `scripts/fix_max_tokens_floor.py` | 新增 | max_tokens 下限保护修复脚本 |\n| `scripts/fix_max_tokens_default.py` | 新增 | **关键** - max_tokens=None 默认值修复脚本 |"
    )

    # 7. 更新总结表格
    content = content.replace(
        "本次修复解决了 2026-01-10 优化后的四个问题：",
        "本次修复解决了 2026-01-10 优化后的五个问题："
    )

    content = content.replace(
        "| 输出空间不足 (thinking=False) | 无下限保护 | 添加 MIN_OUTPUT_TOKENS_FLOOR | ✅ 已修复 |",
        "| 输出空间不足 (thinking=False) | 无下限保护 | 添加 MIN_OUTPUT_TOKENS_FLOOR | ✅ 已修复 |\n| 输出空间不足 (max_tokens=None) | 缺少默认值 | 添加 else 分支设置默认值 | ✅ 已修复 |"
    )

    content = content.replace(
        "- 即使 `thinking=False`，输出空间也保证 >= 16,384 tokens",
        "- 即使 `thinking=False`，输出空间也保证 >= 16,384 tokens\n- 即使客户端不传 `max_tokens`，输出空间也保证 >= 16,384 tokens"
    )

    # 写回文件
    with open(doc_file, 'w', encoding='utf-8') as f:
        f.write(content)

    print("[SUCCESS] 文档更新完成！")
    return True


if __name__ == "__main__":
    main()
