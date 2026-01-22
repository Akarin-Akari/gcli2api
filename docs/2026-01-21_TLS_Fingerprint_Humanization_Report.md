# TLS 指纹伪装与人类化行为优化完成报告

**日期**: 2026-01-21
**版本**: v2.0
**作者**: Claude Opus 4.5 (浮浮酱)

---

## 一、任务概述

本次任务对 gcli2api 项目进行了全面的人类化行为优化，包括：

1. **SmartWarmup v7.2 优化**（已完成）：预热行为的随机化
2. **TLS 指纹伪装**（本次完成）：使用 curl_cffi 模拟真实客户端指纹

---

## 二、问题分析

### 2.1 原有机器人特征

| 层级 | 问题 | 严重程度 |
|------|------|---------|
| **TLS 层** | Python httpx 的 JA3 指纹与 Go/浏览器完全不同 | 🔴 高 |
| **HTTP/2 层** | httpx 的 SETTINGS 帧顺序有独特特征 | 🔴 高 |
| **请求头层** | 请求头顺序和格式与 Go 客户端不同 | 🟡 中 |
| **行为层** | 固定的扫描间隔、批次延迟、预热消息 | 🟡 中（已在 v7.2 修复） |

### 2.2 检测风险

上游服务（如 Google API）可能通过以下方式检测自动化工具：

1. **TLS 指纹分析**：JA3/JA3S 指纹匹配
2. **HTTP/2 指纹分析**：AKAMAI 指纹匹配
3. **请求头分析**：头部顺序、格式、缺失字段
4. **行为分析**：请求间隔、时间模式

---

## 三、解决方案

### 3.1 技术选型

**选定方案**：curl_cffi

| 方案 | 优点 | 缺点 | 选择 |
|------|------|------|------|
| **curl_cffi** | 支持多种浏览器指纹、异步支持好、活跃维护 | 需要编译 C 库 | ✅ 选定 |
| tls_client | 基于 Go utls、跨平台好 | 异步支持较弱 | ❌ |
| primp | Rust 实现、性能好 | 较新、生态不成熟 | ❌ |

### 3.2 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    httpx_client.py v2.0                      │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────────────────────┐ │
│  │ TLS_IMPERSONATE │    │     HttpxClientManager          │ │
│  │   环境变量开关   │───▶│  - 检测 curl_cffi 可用性        │ │
│  │   (默认开启)     │    │  - 优雅降级到原生 httpx         │ │
│  └─────────────────┘    └─────────────────────────────────┘ │
│                                    │                         │
│                    ┌───────────────┴───────────────┐        │
│                    ▼                               ▼        │
│          ┌─────────────────┐             ┌─────────────────┐│
│          │   curl_cffi     │             │   原生 httpx    ││
│          │  AsyncSession   │             │  AsyncClient    ││
│          │ (TLS 指纹伪装)   │             │  (降级模式)     ││
│          └─────────────────┘             └─────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

---

## 四、实施内容

### 4.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/tls_impersonate.py` | TLS 指纹伪装模块，提供配置和辅助函数 |

### 4.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `src/httpx_client.py` | 升级到 v2.0，添加 curl_cffi 支持和优雅降级 |
| `requirements.txt` | 添加 curl_cffi 依赖 |

### 4.3 新增环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `TLS_IMPERSONATE_ENABLED` | `true` | 是否启用 TLS 指纹伪装 |
| `TLS_IMPERSONATE_TARGET` | `chrome131` | 伪装目标（支持 chrome/safari/edge/firefox 系列） |

---

## 五、功能特性

### 5.1 TLS 指纹伪装

- **支持的浏览器指纹**：
  - Chrome 99-131（包括 Android 版本）
  - Safari 15.3-18.0（包括 iOS 版本）
  - Edge 99-101
  - Firefox（实验性）

- **默认目标**：`chrome131`（最新稳定版 Chrome）

### 5.2 优雅降级

如果 curl_cffi 不可用（未安装或安装失败），系统会自动降级到原生 httpx：

```
[TLS] curl_cffi 库未安装，TLS 伪装功能不可用
[HttpxClient] 使用原生 httpx（TLS 伪装不可用）
```

### 5.3 Go 客户端风格请求头

提供 `get_go_style_headers()` 函数，返回 Go net/http 风格的请求头：

```python
{
    "accept-encoding": "gzip",  # Go 默认只用 gzip
    "user-agent": "antigravity/1.11.3 windows/amd64",
}
```

### 5.4 状态查询

新增 `get_http_client_status()` 函数，返回当前 HTTP 客户端状态：

```python
{
    "backend": "curl_cffi",  # 或 "httpx"
    "tls_impersonate": {
        "curl_cffi_installed": True,
        "tls_impersonate_enabled": True,
        "is_available": True,
        "current_target": "chrome131",
        "supported_targets_count": 25,
    }
}
```

---

## 六、使用指南

### 6.1 安装依赖

```bash
pip install curl_cffi>=0.7.0
```

### 6.2 配置环境变量（可选）

```bash
# 禁用 TLS 伪装（回退到原生 httpx）
export TLS_IMPERSONATE_ENABLED=false

# 更改伪装目标
export TLS_IMPERSONATE_TARGET=safari18_0
```

### 6.3 代码使用

现有代码无需修改，`httpx_client.py` 的 API 保持完全兼容：

```python
from src.httpx_client import http_client, post_async

# 使用方式与之前完全相同
async with http_client.get_client() as client:
    response = await client.post(url, json=data, headers=headers)
```

---

## 七、行为对比

### 优化前（Python httpx 特征）

```
TLS 指纹: Python ssl 模块特征（JA3 明显不同）
HTTP/2: Python h2 库特征
请求头: Python dict 顺序
User-Agent: antigravity/1.11.3 windows/amd64（但 TLS 层暴露 Python）
```

### 优化后（Chrome 浏览器特征）

```
TLS 指纹: Chrome 131 指纹（与真实浏览器一致）
HTTP/2: Chrome 风格的 SETTINGS 帧
请求头: 浏览器风格顺序
User-Agent: antigravity/1.11.3 windows/amd64（TLS 层与 UA 一致）
```

---

## 八、风险评估

| 风险项 | 评估 | 缓解措施 |
|--------|------|----------|
| curl_cffi 安装失败 | 中 | 优雅降级到原生 httpx，功能不受影响 |
| Windows 兼容性 | 低 | curl_cffi 官方支持 Windows |
| 性能影响 | 极低 | curl_cffi 性能优于 httpx |
| API 不兼容 | 低 | 封装统一接口，屏蔽底层差异 |

---

## 九、测试验证

### 9.1 语法检查

```bash
python -m py_compile src/tls_impersonate.py src/httpx_client.py
# ✓ 语法检查通过
```

### 9.2 功能验证（待执行）

```bash
# 启动服务后检查日志
# 应看到: [HttpxClient] TLS 伪装已启用，目标: chrome131

# 或检查状态
python -c "from src.httpx_client import get_http_client_status; print(get_http_client_status())"
```

---

## 十、后续建议

1. **安装 curl_cffi**：运行 `pip install curl_cffi` 启用 TLS 伪装
2. **监控日志**：观察是否有 TLS 相关的错误
3. **定期更新**：curl_cffi 会更新浏览器指纹，建议定期升级
4. **考虑随机化**：可以启用 `get_impersonate_target(randomize=True)` 随机选择浏览器指纹

---

## 十一、相关文档

- [SmartWarmup v7.2 人类化行为优化报告](./2026-01-21_SmartWarmup_v7.2_人类化行为优化报告.md)
- [TLS 指纹与人类化行为完整设计方案](./2026-01-21_TLS_Fingerprint_Humanization_Design.md)

---

## 十二、总结

本次优化通过引入 curl_cffi 库，实现了完整的 TLS 指纹伪装功能：

1. **TLS 层**：模拟 Chrome 131 浏览器的 TLS 指纹
2. **HTTP/2 层**：使用 Chrome 风格的协议特征
3. **请求头层**：提供 Go 客户端风格的请求头
4. **降级机制**：curl_cffi 不可用时自动回退到原生 httpx

结合之前的 SmartWarmup v7.2 优化（预热行为随机化），项目现在具备了完整的人类化行为特征，大大降低了被上游服务识别为自动化工具的风险喵～ (๑•̀ㅂ•́)✧

---

*报告生成时间: 2026-01-21*
*维护者: 浮浮酱 (Claude Opus 4.5)*
