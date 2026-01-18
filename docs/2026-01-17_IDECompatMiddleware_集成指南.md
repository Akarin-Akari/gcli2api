# IDECompatMiddleware 集成指南

## 概述

`IDECompatMiddleware` 是一个 FastAPI 中间件,用于在请求处理前后执行 IDE 兼容逻辑。

## 核心功能

### 请求前处理
1. **客户端检测**: 根据 HTTP Headers 检测客户端类型
2. **消息净化**: 对 IDE 客户端的消息进行净化
   - 验证 thinking block 的签名有效性
   - 无效签名时降级为 text block
   - 确保 thinkingConfig 与消息内容一致

### 响应后处理 (TODO)
1. **更新权威历史**: 维护 SCID 状态机
2. **缓存签名**: 缓存新的签名到 Session Cache

## 使用方法

### 方法 1: 在 web.py 中全局注册

```python
from fastapi import FastAPI
from src.ide_compat import IDECompatMiddleware

app = FastAPI()

# 添加 IDE 兼容中间件 (应该在 CORS 中间件之后)
app.add_middleware(IDECompatMiddleware)
```

### 方法 2: 使用便捷函数

```python
from fastapi import FastAPI
from src.ide_compat import create_ide_compat_middleware

app = FastAPI()

# 创建并添加中间件
middleware = create_ide_compat_middleware(app)
app.add_middleware(IDECompatMiddleware)
```

### 方法 3: 自定义配置

```python
from fastapi import FastAPI
from src.ide_compat import IDECompatMiddleware, AnthropicSanitizer

app = FastAPI()

# 创建自定义 sanitizer
sanitizer = AnthropicSanitizer()

# 添加中间件
app.add_middleware(
    IDECompatMiddleware,
    sanitizer=sanitizer,
)
```

## 中间件顺序

**重要**: 中间件的顺序很重要! 建议的顺序:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.ide_compat import IDECompatMiddleware

app = FastAPI()

# 1. CORS 中间件 (最外层)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. IDE 兼容中间件 (在 CORS 之后,路由之前)
app.add_middleware(IDECompatMiddleware)

# 3. 其他中间件...

# 4. 路由注册
app.include_router(antigravity_router)
```

## 工作原理

### 1. 请求路径过滤

中间件只处理以下路径的 POST 请求:
- `/antigravity/v1/messages`
- `/v1/messages`

其他路径的请求会直接放行。

### 2. 客户端检测

通过 `ClientTypeDetector` 检测客户端类型:
- **Claude Code**: 不需要净化,直接放行
- **IDE 客户端** (Cursor, Augment, Windsurf 等): 需要净化
- **Unknown**: 默认需要净化 (保守策略)

### 3. 消息净化

对需要净化的请求:
1. 读取请求体
2. 提取 `messages` 和 `thinking` 配置
3. 使用 `AnthropicSanitizer` 净化消息
4. 更新请求体
5. 创建新的 Request 对象

### 4. 请求体重写

中间件会创建一个新的 Request 对象,包含净化后的消息:
- 保留原始 headers
- 更新 Content-Length
- 替换 body

### 5. 异常处理

中间件采用**兜底保护**策略:
- 所有异常都会被捕获
- 出错时返回原始请求的处理结果
- 不会影响主流程

## 统计信息

中间件会收集统计信息:

```python
from src.ide_compat import get_middleware_stats

stats = middleware.get_stats()
print(stats)
# {
#     "total_requests": 100,
#     "sanitized_requests": 80,
#     "skipped_requests": 15,
#     "errors": 5,
# }
```

## 性能考虑

### 1. 请求体读取

- 中间件会读取整个请求体到内存
- 对于大请求 (>10MB) 可能影响性能
- 建议配合 `max_request_body_size` 限制使用

### 2. JSON 解析

- 每个请求会解析 2 次 JSON (中间件 + 路由)
- 对于高频请求可能有性能影响
- 可以考虑缓存解析结果

### 3. 异步处理

- 中间件是异步的,不会阻塞事件循环
- 净化逻辑也是异步的
- 适合高并发场景

## 调试

### 启用详细日志

```python
import logging

# 设置日志级别
logging.getLogger("gcli2api.ide_compat.middleware").setLevel(logging.DEBUG)
logging.getLogger("gcli2api.ide_compat.sanitizer").setLevel(logging.DEBUG)
logging.getLogger("gcli2api.ide_compat.client_detector").setLevel(logging.DEBUG)
```

### 查看统计信息

```python
# 在路由中添加统计端点
@app.get("/ide_compat/stats")
async def get_ide_compat_stats():
    # 获取中间件实例 (需要保存到全局变量)
    return middleware.get_stats()
```

## 已知限制

1. **响应处理未实现**: 当前版本只实现了请求前处理,响应后处理 (更新权威历史、缓存签名) 还未实现
2. **流式响应**: 当前版本不处理流式响应,只处理非流式请求
3. **大请求**: 对于超大请求 (>100MB) 可能有性能问题

## TODO

- [ ] 实现响应后处理逻辑
- [ ] 支持流式响应的签名提取
- [ ] 集成 ConversationStateManager
- [ ] 添加请求体大小限制
- [ ] 优化 JSON 解析性能
- [ ] 添加更多统计指标

## 参考

- [FastAPI Middleware 文档](https://fastapi.tiangolo.com/tutorial/middleware/)
- [Starlette Middleware 文档](https://www.starlette.io/middleware/)
- [AnthropicSanitizer 文档](./sanitizer.py)
- [ClientTypeDetector 文档](./client_detector.py)

---

**Author**: Claude Sonnet 4.5 (浮浮酱)
**Date**: 2026-01-17
