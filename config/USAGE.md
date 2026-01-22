# Gateway 配置加载器使用示例

## 基本用法

```python
from src.gateway.config_loader import load_gateway_config

# 加载所有后端配置
configs = load_gateway_config()

# 访问特定后端配置
antigravity_config = configs["antigravity"]
print(antigravity_config.base_url)  # http://127.0.0.1:7861/antigravity/v1
print(antigravity_config.priority)  # 1
print(antigravity_config.models)    # ["*"]
```

## 获取单个后端配置

```python
from src.gateway.config_loader import get_backend_config

config = get_backend_config("copilot")
print(config.timeout)  # 120.0
```

## 列出启用的后端

```python
from src.gateway.config_loader import list_enabled_backends

# 获取所有启用的后端（按优先级排序）
backends = list_enabled_backends()
print(backends)  # ['antigravity', 'copilot']
```

## 环境变量配置

在 `.env` 文件或系统环境变量中设置：

```bash
# 禁用 Copilot
COPILOT_ENABLED=false

# 启用 Kiro Gateway
KIRO_GATEWAY_ENABLED=true
KIRO_GATEWAY_ENDPOINT=http://custom.gateway.com/v1
KIRO_GATEWAY_MODELS=["gpt-4", "claude-3-opus"]
```

## 配置文件格式

`config/gateway.yaml`:

```yaml
backends:
  antigravity:
    enabled: true
    priority: 1
    base_url: "http://127.0.0.1:7861/antigravity/v1"
    models: ["*"]
    timeout: 60
    stream_timeout: 300
    max_retries: 2

  copilot:
    enabled: ${COPILOT_ENABLED:true}  # 从环境变量读取，默认 true
    priority: 2
    base_url: "http://127.0.0.1:8141/v1"
    models:
      - gpt-4
      - gpt-5.2
    timeout: 120
    stream_timeout: 600
    max_retries: 3
```

## 环境变量替换语法

- `${VAR_NAME}` - 从环境变量读取，如果不存在则为空字符串
- `${VAR_NAME:default}` - 从环境变量读取，如果不存在则使用默认值

支持的类型自动转换：
- 布尔值：`true`, `false`, `yes`, `no`, `1`, `0`
- 数字：整数或浮点数
- 列表：JSON 格式 `["item1", "item2"]`
- 字符串：其他所有值

## BackendConfig 属性

```python
@dataclass
class BackendConfig:
    name: str              # 后端名称
    base_url: str          # 后端基础 URL
    priority: int          # 优先级（数字越小优先级越高）
    models: List[str]      # 支持的模型列表（["*"] 表示所有模型）
    enabled: bool          # 是否启用
    timeout: float         # 请求超时时间（秒）
    max_retries: int       # 最大重试次数
    stream_timeout: float  # 流式请求超时时间（秒，通过 config_loader 添加）
```

## 检查模型支持

```python
config = configs["antigravity"]

# 检查是否支持特定模型
if config.supports_model("claude-sonnet-4.5"):
    print("支持 Claude Sonnet 4.5")

# "*" 表示支持所有模型
if "*" in config.models:
    print("支持所有模型")
```

## 完整示例

```python
from src.gateway.config_loader import load_gateway_config, list_enabled_backends

# 加载配置
configs = load_gateway_config()

# 按优先级处理后端
for backend_name in list_enabled_backends():
    config = configs[backend_name]

    print(f"后端: {config.name}")
    print(f"  URL: {config.base_url}")
    print(f"  优先级: {config.priority}")
    print(f"  超时: {config.timeout}s / {config.stream_timeout}s")

    # 检查模型支持
    if config.supports_model("gpt-5.2"):
        print(f"  ✓ 支持 GPT-5.2")
```

## 错误处理

```python
from src.gateway.config_loader import load_gateway_config

try:
    configs = load_gateway_config()
except FileNotFoundError as e:
    print(f"配置文件不存在: {e}")
except ValueError as e:
    print(f"配置格式错误: {e}")
```
