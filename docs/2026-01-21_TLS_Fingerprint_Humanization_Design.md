# TLS 指纹与人类化行为完整设计方案

**日期**: 2026-01-21
**版本**: v1.0
**作者**: Claude Opus 4.5 (浮浮酱)

---

## 一、问题分析

### 1.1 当前状态

项目使用原生 `httpx` 库进行 HTTP 请求，存在以下机器人特征：

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| **Python httpx TLS 指纹** | 🔴 高 | httpx 使用 Python 的 ssl 模块，TLS 指纹（JA3）与 Go/浏览器完全不同 |
| **HTTP/2 指纹** | 🔴 高 | httpx 的 HTTP/2 实现有独特的 SETTINGS 帧顺序 |
| **请求头顺序** | 🟡 中 | Python dict 的请求头顺序与 Go 客户端不同 |
| **User-Agent 不一致** | 🟡 中 | 虽然设置了 Antigravity UA，但 TLS 层暴露了 Python 特征 |

### 1.2 目标

模拟 **Antigravity CLI (Go 客户端)** 的完整访问行为：
- TLS 指纹（JA3）匹配 Go net/http
- HTTP/2 指纹匹配 Go 客户端
- 请求头顺序和格式匹配
- User-Agent 保持一致

---

## 二、技术方案对比

### 2.1 可选方案

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **curl_cffi** | 支持多种客户端指纹、异步支持好、活跃维护 | 需要编译 C 库、Windows 兼容性需验证 | ⭐⭐⭐⭐⭐ |
| **tls_client** | 基于 Go utls、跨平台好 | 异步支持较弱、更新较慢 | ⭐⭐⭐ |
| **primp** | Rust 实现、性能好 | 较新、生态不成熟 | ⭐⭐ |
| **保持 httpx** | 无需改动 | 无法解决 TLS 指纹问题 | ⭐ |

### 2.2 选定方案：curl_cffi

**理由**：
1. 支持模拟 Chrome、Safari、Edge、Firefox 等浏览器指纹
2. 支持 `impersonate="chrome"` 等简单 API
3. 完整的异步支持 (`AsyncSession`)
4. 活跃维护，社区支持好
5. 可以精确控制 TLS 参数

**Go 客户端模拟策略**：
- curl_cffi 没有直接的 Go 指纹预设
- 使用 `impersonate="chrome"` 作为基础（比 Python 指纹更接近正常客户端）
- 或使用自定义 TLS 配置模拟 Go 特征

---

## 三、实施方案

### 3.1 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    httpx_client.py (改造)                    │
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

### 3.2 环境变量配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `TLS_IMPERSONATE_ENABLED` | `true` | 是否启用 TLS 指纹伪装 |
| `TLS_IMPERSONATE_TARGET` | `chrome` | 伪装目标（chrome/safari/edge/firefox） |

### 3.3 请求头人类化

#### Go net/http 典型请求头顺序：
```
:method: POST
:authority: generativelanguage.googleapis.com
:scheme: https
:path: /v1beta/models/gemini-2.5-pro:streamGenerateContent
accept-encoding: gzip
content-type: application/json
user-agent: antigravity/1.11.3 windows/amd64
authorization: Bearer xxx
```

#### Python httpx 典型请求头顺序：
```
host: generativelanguage.googleapis.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: antigravity/1.11.3 windows/amd64
content-type: application/json
authorization: Bearer xxx
```

**差异点**：
1. Go 使用 HTTP/2 伪头（`:method`, `:authority` 等）
2. Go 的 `accept-encoding` 只有 `gzip`
3. 请求头顺序不同

### 3.4 代码改造

#### 3.4.1 新增 TLS 伪装模块

创建 `src/tls_impersonate.py`：

```python
"""
TLS 指纹伪装模块

使用 curl_cffi 模拟真实客户端的 TLS 指纹，避免被识别为 Python 自动化工具。
支持优雅降级：如果 curl_cffi 不可用，回退到原生 httpx。
"""

import os
from typing import Optional, Dict, Any
from log import log

# 尝试导入 curl_cffi
try:
    from curl_cffi.requests import AsyncSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    AsyncSession = None

# 配置
TLS_IMPERSONATE_ENABLED = os.getenv("TLS_IMPERSONATE_ENABLED", "true").lower() in ("true", "1", "yes")
TLS_IMPERSONATE_TARGET = os.getenv("TLS_IMPERSONATE_TARGET", "chrome")

def is_tls_impersonate_available() -> bool:
    """检查 TLS 伪装是否可用"""
    return CURL_CFFI_AVAILABLE and TLS_IMPERSONATE_ENABLED

def get_impersonate_target() -> str:
    """获取伪装目标"""
    return TLS_IMPERSONATE_TARGET

# Go 客户端风格的请求头
GO_CLIENT_HEADERS = {
    "accept-encoding": "gzip",  # Go 默认只用 gzip
}
```

#### 3.4.2 改造 httpx_client.py

在 `HttpxClientManager` 中添加 TLS 伪装支持：

```python
from tls_impersonate import (
    is_tls_impersonate_available,
    get_impersonate_target,
    GO_CLIENT_HEADERS,
    AsyncSession as CurlAsyncSession,
)

class HttpxClientManager:
    """通用HTTP客户端管理器 - 支持 TLS 指纹伪装"""

    def __init__(self):
        self._use_curl_cffi = is_tls_impersonate_available()
        if self._use_curl_cffi:
            log.info(f"[HttpxClient] TLS 伪装已启用，目标: {get_impersonate_target()}")
        else:
            log.warning("[HttpxClient] TLS 伪装不可用，使用原生 httpx")

    @asynccontextmanager
    async def get_client(self, timeout: float = 30.0, **kwargs):
        """获取配置好的异步HTTP客户端"""
        if self._use_curl_cffi:
            # 使用 curl_cffi 的 AsyncSession
            async with CurlAsyncSession(
                impersonate=get_impersonate_target(),
                timeout=timeout,
                **kwargs
            ) as session:
                yield session
        else:
            # 降级到原生 httpx
            client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)
            async with httpx.AsyncClient(**client_kwargs) as client:
                yield client
```

---

## 四、实施步骤

### 阶段 1：添加依赖（可选）
```bash
pip install curl_cffi
```

### 阶段 2：创建 TLS 伪装模块
- 创建 `src/tls_impersonate.py`
- 实现可用性检测和配置

### 阶段 3：改造 httpx_client.py
- 添加 curl_cffi 支持
- 实现优雅降级
- 保持 API 兼容性

### 阶段 4：测试验证
- 验证 TLS 指纹变化
- 验证功能正常
- 验证降级机制

---

## 五、风险评估

| 风险项 | 评估 | 缓解措施 |
|--------|------|----------|
| curl_cffi 安装失败 | 中 | 优雅降级到原生 httpx |
| Windows 兼容性问题 | 低 | curl_cffi 官方支持 Windows |
| 性能影响 | 极低 | curl_cffi 性能优于 httpx |
| API 不兼容 | 低 | 封装统一接口，屏蔽差异 |

---

## 六、备选方案

如果 curl_cffi 方案遇到问题，可以考虑：

1. **仅优化请求头**：不改变 TLS 层，只优化请求头顺序和内容
2. **使用 tls_client**：另一个 TLS 伪装库
3. **代理方案**：通过 Go 编写的代理服务转发请求

---

*设计文档生成时间: 2026-01-21*
*维护者: 浮浮酱 (Claude Opus 4.5)*
