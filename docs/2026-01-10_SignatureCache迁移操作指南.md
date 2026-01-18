# SignatureCache 迁移操作指南

> **Author**: Claude Opus 4.5 (浮浮酱)
> **Date**: 2026-01-10
> **Version**: 1.0.0

## 概述

本文档描述了从旧 `SignatureCache` 到新分层缓存系统的渐进式迁移流程。迁移采用四阶段策略，确保零停机、零风险的平滑过渡。

## 迁移架构

```
┌─────────────────────────────────────────────────────────────┐
│                    现有代码调用点                            │
│  (anthropic_converter, antigravity_router, web_routes...)  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   signature_cache.py                         │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              迁移代理层 (Phase 3)                        ││
│  │  - enable_migration_mode()                              ││
│  │  - disable_migration_mode()                             ││
│  │  - set_migration_phase()                                ││
│  └─────────────────────────┬───────────────────────────────┘│
└─────────────────────────────┼───────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              │                               │
              ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────────┐
│   旧 SignatureCache     │     │      CacheFacade            │
│   (原有实现)            │     │   ┌─────────────────────┐   │
│                         │     │   │ LegacyAdapter       │   │
│                         │     │   │ (迁移适配器)        │   │
│                         │     │   └──────────┬──────────┘   │
│                         │     │              │              │
│                         │     │   ┌──────────┴──────────┐   │
│                         │     │   │                     │   │
│                         │     │   ▼                     ▼   │
│                         │     │ ┌─────┐           ┌───────┐ │
│                         │     │ │ L1  │           │  L2   │ │
│                         │     │ │内存 │           │SQLite │ │
│                         │     │ └─────┘           └───────┘ │
└─────────────────────────┘     └─────────────────────────────┘
```

## 迁移阶段

### Phase 1: LEGACY_ONLY（默认，零风险）

- **行为**: 只使用旧的 SignatureCache
- **读取**: 旧缓存
- **写入**: 旧缓存
- **风险**: 无
- **用途**: 初始状态，验证代码部署

### Phase 2: DUAL_WRITE（双写模式）

- **行为**: 同时写入新旧缓存，读取优先旧缓存
- **读取**: 旧缓存优先，fallback 到新缓存
- **写入**: 新旧缓存同时写入
- **风险**: 低（新缓存写入失败不影响服务）
- **用途**: 预热新缓存，验证写入正确性

### Phase 3: NEW_PREFERRED（新缓存优先）

- **行为**: 同时写入新旧缓存，读取优先新缓存
- **读取**: 新缓存优先，fallback 到旧缓存
- **写入**: 新旧缓存同时写入
- **风险**: 中（新缓存问题可 fallback）
- **用途**: 验证新缓存读取正确性

### Phase 4: NEW_ONLY（迁移完成）

- **行为**: 只使用新缓存
- **读取**: 新缓存
- **写入**: 新缓存
- **风险**: 需确保新缓存稳定
- **用途**: 迁移完成，可移除旧代码

## 操作指南

### 方式一：通过环境变量启用

```bash
# 启用迁移模式
export CACHE_USE_MIGRATION_ADAPTER=true

# 启动服务
python main.py
```

### 方式二：通过 API 控制

```bash
# 1. 查看当前迁移状态
curl -X GET "http://localhost:8000/cache/migration/status" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 2. 启用迁移模式
curl -X POST "http://localhost:8000/cache/migration/enable" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 3. 设置迁移阶段为双写模式
curl -X POST "http://localhost:8000/cache/migration/phase?phase=DUAL_WRITE" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 4. 切换到新缓存优先
curl -X POST "http://localhost:8000/cache/migration/phase?phase=NEW_PREFERRED" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 5. 完成迁移，只使用新缓存
curl -X POST "http://localhost:8000/cache/migration/phase?phase=NEW_ONLY" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 6. 如需回滚，禁用迁移模式
curl -X POST "http://localhost:8000/cache/migration/disable" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 方式三：通过代码控制

```python
from signature_cache import (
    enable_migration_mode,
    disable_migration_mode,
    set_migration_phase,
    get_migration_status,
)

# 启用迁移模式
enable_migration_mode()

# 设置迁移阶段
set_migration_phase("DUAL_WRITE")

# 查看状态
status = get_migration_status()
print(status)

# 回滚
disable_migration_mode()
```

## 推荐迁移流程

### 第一步：部署代码（无风险）

1. 部署包含 Phase 3 代码的新版本
2. 默认使用 LEGACY_ONLY 模式
3. 验证服务正常运行

### 第二步：启用双写模式（低风险）

1. 通过 API 启用迁移模式
2. 设置阶段为 DUAL_WRITE
3. 观察日志，确认双写正常
4. 运行 24-48 小时

### 第三步：切换到新缓存优先（中风险）

1. 设置阶段为 NEW_PREFERRED
2. 监控命中率和性能指标
3. 确认 fallback 机制正常
4. 运行 24-48 小时

### 第四步：完成迁移（需确认稳定）

1. 设置阶段为 NEW_ONLY
2. 持续监控
3. 如无问题，可在后续版本移除旧代码

## 监控指标

### 通过 API 获取统计

```bash
# 获取缓存统计
curl -X GET "http://localhost:8000/cache/signature/stats" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 获取迁移状态
curl -X GET "http://localhost:8000/cache/migration/status" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 关键指标

- `hit_rate`: 缓存命中率
- `legacy_hits` / `new_hits`: 新旧缓存命中次数
- `fallback_hits`: Fallback 命中次数
- `dual_write_stats`: 双写统计

## 回滚方案

### 快速回滚

```bash
# 禁用迁移模式，立即回退到旧缓存
curl -X POST "http://localhost:8000/cache/migration/disable" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 阶段回滚

```bash
# 从 NEW_ONLY 回退到 NEW_PREFERRED
curl -X POST "http://localhost:8000/cache/migration/phase?phase=NEW_PREFERRED" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 从 NEW_PREFERRED 回退到 DUAL_WRITE
curl -X POST "http://localhost:8000/cache/migration/phase?phase=DUAL_WRITE" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 从 DUAL_WRITE 回退到 LEGACY_ONLY
curl -X POST "http://localhost:8000/cache/migration/phase?phase=LEGACY_ONLY" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 文件清单

| 文件 | 说明 |
|------|------|
| `cache/cache_facade.py` | 统一缓存门面 |
| `cache/migration/__init__.py` | 迁移模块入口 |
| `cache/migration/feature_flags.py` | 特性开关 |
| `cache/migration/migration_config.py` | 迁移配置 |
| `cache/migration/dual_write_strategy.py` | 双写策略 |
| `cache/migration/read_strategy.py` | 读取策略 |
| `cache/migration/legacy_adapter.py` | 旧接口适配器 |
| `cache/test_phase3_integration.py` | 集成测试 |
| `signature_cache.py` | 原有缓存（已添加迁移代理） |

## 注意事项

1. **渐进式迁移**: 不要跳过阶段，按顺序逐步推进
2. **监控先行**: 每个阶段都要充分监控后再进入下一阶段
3. **保留回滚能力**: 始终保持快速回滚的能力
4. **日志观察**: 关注 `[CACHE_MIGRATION]` 和 `[LEGACY_ADAPTER]` 日志
5. **性能对比**: 对比新旧缓存的性能指标

## 常见问题

### Q: 迁移模式启用后服务变慢？

A: 检查新缓存的 L2 SQLite 是否正常初始化，可能需要预热。

### Q: 双写模式下新缓存写入失败？

A: 检查 SQLite 数据库路径权限，查看 `[LEGACY_ADAPTER]` 日志。

### Q: 如何验证新缓存数据正确？

A: 在 DUAL_WRITE 阶段，对比新旧缓存的读取结果。

---

*本文档由 Claude Opus 4.5 (浮浮酱) 生成 喵～ φ(≧ω≦*)♪*
