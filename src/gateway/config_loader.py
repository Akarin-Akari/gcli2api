"""
Gateway 配置加载器

从 YAML 配置文件加载后端配置，支持环境变量替换。

作者: 浮浮酱 (Claude Sonnet 4.5)
创建日期: 2026-01-18
"""

import os
import re
import yaml
from pathlib import Path
from typing import Dict, Any, List, Union

from src.gateway.backends.interface import BackendConfig
from dataclasses import dataclass, field
from typing import Dict, Any, List, Union, Set, Tuple, Optional

__all__ = [
    "load_gateway_config",
    "load_model_routing_config",
    "expand_env_vars",
    "ModelRoutingRule",
    "BackendEntry",
]


@dataclass
class BackendEntry:
    """
    后端链条目

    表示降级链中的一个后端配置，包含后端名称和目标模型

    Attributes:
        backend: 后端名称（如 kiro-gateway, antigravity, copilot）
        model: 目标模型名称（如 claude-sonnet-4.5, gemini-3-pro）
    """
    backend: str
    model: str

    def __repr__(self) -> str:
        return f"BackendEntry({self.backend}, {self.model})"


@dataclass
class ModelRoutingRule:
    """
    模型特定路由规则

    用于配置特定模型的后端优先级链和降级条件

    Attributes:
        model: 模型名称（如 claude-sonnet-4.5）
        backend_chain: 按优先级排序的后端链（包含后端和目标模型）
        fallback_on: 触发降级的条件（HTTP 状态码或特殊条件）
        enabled: 是否启用此规则
    """
    model: str
    backend_chain: List[BackendEntry] = field(default_factory=list)
    fallback_on: Set[Union[int, str]] = field(default_factory=set)
    enabled: bool = True

    @property
    def backends(self) -> List[str]:
        """
        兼容属性：返回后端名称列表（不包含目标模型）

        用于向后兼容旧代码
        """
        return [entry.backend for entry in self.backend_chain]

    def should_fallback(self, status_code: int = None, error_type: str = None) -> bool:
        """
        判断是否应该降级到下一个后端

        Args:
            status_code: HTTP 状态码
            error_type: 错误类型（timeout, connection_error, unavailable）

        Returns:
            是否应该降级
        """
        if status_code and status_code in self.fallback_on:
            return True
        if error_type and error_type in self.fallback_on:
            return True
        return False

    def get_first_backend(self) -> Optional[BackendEntry]:
        """
        获取第一个后端

        Returns:
            第一个后端条目，如果没有则返回 None
        """
        return self.backend_chain[0] if self.backend_chain else None

    def get_next_backend(self, current_backend: str) -> Optional[str]:
        """
        获取下一个后端名称（向后兼容）

        Args:
            current_backend: 当前后端名称

        Returns:
            下一个后端名称，如果没有则返回 None
        """
        entry = self.get_next_backend_entry(current_backend)
        return entry.backend if entry else None

    def get_next_backend_entry(self, current_backend: str) -> Optional[BackendEntry]:
        """
        获取下一个后端条目（包含后端和目标模型）

        Args:
            current_backend: 当前后端名称

        Returns:
            下一个后端条目，如果没有则返回 None
        """
        # 查找当前后端在链中的位置
        for i, entry in enumerate(self.backend_chain):
            if entry.backend == current_backend:
                if i + 1 < len(self.backend_chain):
                    return self.backend_chain[i + 1]
                return None

        # ✅ [FIX 2026-01-22] 当前后端不在链中，不应该返回第一个（可能导致降级链断裂）
        # 返回 None，让调用者处理
        from src.utils import log
        log.warning(
            f"[FALLBACK] 后端 {current_backend} 不在模型 {self.model} 的降级链中",
            tag="GATEWAY"
        )
        return None

    def get_backend_entry_by_name(self, backend_name: str) -> Optional[BackendEntry]:
        """
        根据后端名称获取条目

        Args:
            backend_name: 后端名称

        Returns:
            后端条目，如果不存在则返回 None
        """
        for entry in self.backend_chain:
            if entry.backend == backend_name:
                return entry
        return None


def expand_env_vars(value: Any) -> Any:
    """
    递归展开环境变量

    支持语法：${VAR_NAME:default_value}

    Args:
        value: 配置值（可以是字符串、列表、字典等）

    Returns:
        展开后的值

    Examples:
        >>> os.environ["TEST_VAR"] = "hello"
        >>> expand_env_vars("${TEST_VAR:world}")
        'hello'
        >>> expand_env_vars("${MISSING_VAR:world}")
        'world'
        >>> expand_env_vars("${BOOL_VAR:true}")
        True
        >>> expand_env_vars("${INT_VAR:42}")
        42
    """
    if isinstance(value, str):
        # 匹配 ${VAR:default} 或 ${VAR}
        pattern = r'\$\{([A-Za-z_][A-Za-z0-9_]*?)(?::([^}]*))?\}'

        def replacer(match):
            var_name = match.group(1)
            default_value = match.group(2) if match.group(2) is not None else ""
            env_value = os.getenv(var_name, default_value)
            return env_value

        result = re.sub(pattern, replacer, value)

        # 类型转换
        # 布尔值
        if result.lower() in ("true", "yes", "1"):
            return True
        elif result.lower() in ("false", "no", "0"):
            return False

        # 数字
        try:
            if "." in result:
                return float(result)
            else:
                return int(result)
        except ValueError:
            pass

        # 列表（JSON 格式）
        if result.startswith("[") and result.endswith("]"):
            try:
                import json
                return json.loads(result)
            except (json.JSONDecodeError, ValueError):
                pass

        return result

    elif isinstance(value, list):
        return [expand_env_vars(item) for item in value]

    elif isinstance(value, dict):
        return {key: expand_env_vars(val) for key, val in value.items()}

    else:
        return value


def load_gateway_config(config_path: str = None) -> Dict[str, BackendConfig]:
    """
    从 YAML 文件加载 Gateway 配置

    Args:
        config_path: 配置文件路径（默认为 config/gateway.yaml）

    Returns:
        后端配置字典 {backend_name: BackendConfig}

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置格式错误

    Examples:
        >>> configs = load_gateway_config()
        >>> antigravity_config = configs["antigravity"]
        >>> print(antigravity_config.base_url)
        http://127.0.0.1:7861/antigravity/v1
    """
    # 默认配置文件路径
    if config_path is None:
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / "config" / "gateway.yaml"
    else:
        config_path = Path(config_path)

    # 检查文件是否存在
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    # 读取 YAML 文件
    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    if not isinstance(raw_config, dict) or "backends" not in raw_config:
        raise ValueError("配置文件格式错误：缺少 'backends' 字段")

    backends_raw = raw_config["backends"]
    if not isinstance(backends_raw, dict):
        raise ValueError("配置文件格式错误：'backends' 必须是字典")

    # 转换为 BackendConfig 对象
    configs: Dict[str, BackendConfig] = {}

    for backend_name, backend_data in backends_raw.items():
        if not isinstance(backend_data, dict):
            raise ValueError(f"后端 '{backend_name}' 配置格式错误：必须是字典")

        # 展开环境变量
        expanded_data = expand_env_vars(backend_data)

        # 提取字段
        name = backend_name  # 使用 key 作为名称
        base_url = expanded_data.get("base_url")
        priority = expanded_data.get("priority")
        models = expanded_data.get("models", [])
        enabled = expanded_data.get("enabled", True)
        timeout = expanded_data.get("timeout", 30.0)
        stream_timeout = expanded_data.get("stream_timeout", timeout * 2)  # 默认为普通超时的 2 倍
        max_retries = expanded_data.get("max_retries", 3)

        # 验证必填字段
        if not base_url:
            raise ValueError(f"后端 '{backend_name}' 缺少 'base_url' 字段")
        if priority is None:
            raise ValueError(f"后端 '{backend_name}' 缺少 'priority' 字段")

        # 类型转换
        if not isinstance(models, list):
            raise ValueError(f"后端 '{backend_name}' 的 'models' 必须是列表")

        try:
            priority = int(priority)
            timeout = float(timeout)
            stream_timeout = float(stream_timeout)
            max_retries = int(max_retries)
        except (ValueError, TypeError) as e:
            raise ValueError(f"后端 '{backend_name}' 配置类型错误: {e}")

        # 创建 BackendConfig 对象
        # 注意：BackendConfig 目前没有 stream_timeout 字段，我们将其存储在额外属性中
        config = BackendConfig(
            name=name,
            base_url=base_url,
            priority=priority,
            models=models,
            enabled=enabled,
            timeout=timeout,
            max_retries=max_retries,
        )

        # 临时存储 stream_timeout（直到 BackendConfig 添加此字段）
        # 使用 object.__setattr__ 绕过 dataclass 的限制
        object.__setattr__(config, "stream_timeout", stream_timeout)

        configs[backend_name] = config

    return configs


def load_model_routing_config(config_path: str = None) -> Dict[str, ModelRoutingRule]:
    """
    从 YAML 文件加载模型特定路由配置

    Args:
        config_path: 配置文件路径（默认为 config/gateway.yaml）

    Returns:
        模型路由规则字典 {model_name: ModelRoutingRule}

    Examples:
        >>> rules = load_model_routing_config()
        >>> sonnet_rule = rules.get("claude-sonnet-4.5")
        >>> if sonnet_rule and sonnet_rule.enabled:
        ...     print(f"Sonnet 4.5 backends: {sonnet_rule.backends}")
        Sonnet 4.5 backends: ['kiro-gateway', 'antigravity']
    """
    # 默认配置文件路径
    if config_path is None:
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / "config" / "gateway.yaml"
    else:
        config_path = Path(config_path)

    # 检查文件是否存在
    if not config_path.exists():
        return {}  # 配置文件不存在时返回空字典

    # 读取 YAML 文件
    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    if not isinstance(raw_config, dict):
        return {}

    # 获取 model_routing 配置节
    model_routing_raw = raw_config.get("model_routing", {})
    if not isinstance(model_routing_raw, dict):
        return {}

    # 转换为 ModelRoutingRule 对象
    rules: Dict[str, ModelRoutingRule] = {}

    for model_name, rule_data in model_routing_raw.items():
        if not isinstance(rule_data, dict):
            continue

        # 展开环境变量
        expanded_data = expand_env_vars(rule_data)

        # 提取字段
        backends_raw = expanded_data.get("backends", [])
        fallback_on_raw = expanded_data.get("fallback_on", [])
        enabled = expanded_data.get("enabled", True)

        # 处理 backends：支持两种格式
        # 1. 字符串格式（旧）: ["kiro-gateway", "antigravity"]
        # 2. 对象格式（新）: [{"backend": "kiro-gateway", "model": "claude-sonnet-4.5"}]
        backend_chain: List[BackendEntry] = []
        for item in backends_raw:
            if isinstance(item, str):
                # 旧格式：字符串，使用原始模型名作为目标模型
                backend_chain.append(BackendEntry(
                    backend=item,
                    model=model_name.lower()  # 使用配置的模型名作为默认目标
                ))
            elif isinstance(item, dict):
                # 新格式：{backend: "name", model: "target_model"}
                backend_name = item.get("backend", "")
                target_model = item.get("model", model_name.lower())
                if backend_name:
                    backend_chain.append(BackendEntry(
                        backend=backend_name,
                        model=target_model
                    ))

        # 处理 fallback_on：转换为 set，支持整数和字符串
        fallback_on = set()
        for item in fallback_on_raw:
            if isinstance(item, int):
                fallback_on.add(item)
            elif isinstance(item, str):
                # 尝试转换为整数（HTTP 状态码）
                try:
                    fallback_on.add(int(item))
                except ValueError:
                    # 保留字符串（特殊条件如 timeout, connection_error）
                    fallback_on.add(item.lower())

        # 创建规则对象
        rule = ModelRoutingRule(
            model=model_name.lower(),
            backend_chain=backend_chain,
            fallback_on=fallback_on,
            enabled=enabled,
        )

        rules[model_name.lower()] = rule

    return rules


# 全局缓存：避免重复加载配置
_model_routing_cache: Dict[str, ModelRoutingRule] = None


def get_model_routing_rule(model: str, config_path: str = None) -> ModelRoutingRule:
    """
    获取指定模型的路由规则

    Args:
        model: 模型名称
        config_path: 配置文件路径（可选）

    Returns:
        模型路由规则，如果不存在则返回 None
    """
    global _model_routing_cache

    if _model_routing_cache is None:
        _model_routing_cache = load_model_routing_config(config_path)

    # 规范化模型名称
    model_lower = model.lower()

    # 1. 精确匹配（原样）
    rule = _model_routing_cache.get(model_lower)
    if rule:
        return rule

    # 2. 基础模糊匹配：移除 -thinking/-extended/-preview/-latest 后缀和日期后缀
    normalized = re.sub(r'-(thinking|extended|preview|latest)$', '', model_lower)
    normalized = re.sub(r'-\d{8}$', '', normalized)

    rule = _model_routing_cache.get(normalized)
    if rule:
        return rule

    # 3. 针对 Claude 4.5 版本号写法的额外兼容：
    #    - 配置中通常使用 4.5（点号）
    #    - 实际模型名可能使用 4-5 或带日期后缀，如 claude-sonnet-4-5-20250929
    #    这里对 4.5 / 4-5 做双向归一化，以便命中配置键。
    claude_variants = set()
    claude_variants.add(normalized)
    claude_variants.add(normalized.replace("4-5", "4.5"))
    claude_variants.add(normalized.replace("4.5", "4-5"))

    for key in claude_variants:
        if key in _model_routing_cache:
            return _model_routing_cache[key]

    return None


def reload_model_routing_config(config_path: str = None) -> None:
    """
    重新加载模型路由配置（清除缓存）

    用于配置文件更新后刷新配置
    """
    global _model_routing_cache
    _model_routing_cache = load_model_routing_config(config_path)


def get_backend_config(backend_name: str, config_path: str = None) -> BackendConfig:
    """
    获取指定后端的配置

    Args:
        backend_name: 后端名称
        config_path: 配置文件路径（可选）

    Returns:
        后端配置对象

    Raises:
        KeyError: 后端不存在

    Examples:
        >>> config = get_backend_config("antigravity")
        >>> print(config.priority)
        1
    """
    configs = load_gateway_config(config_path)
    if backend_name not in configs:
        raise KeyError(f"后端 '{backend_name}' 不存在于配置文件中")
    return configs[backend_name]


def list_enabled_backends(config_path: str = None) -> List[str]:
    """
    列出所有启用的后端名称

    Args:
        config_path: 配置文件路径（可选）

    Returns:
        启用的后端名称列表（按优先级排序）

    Examples:
        >>> backends = list_enabled_backends()
        >>> print(backends)
        ['antigravity', 'copilot']
    """
    configs = load_gateway_config(config_path)
    enabled = [
        (name, config.priority)
        for name, config in configs.items()
        if config.enabled
    ]
    # 按优先级排序
    enabled.sort(key=lambda x: x[1])
    return [name for name, _ in enabled]


if __name__ == "__main__":
    # 测试代码
    try:
        configs = load_gateway_config()
        print("成功加载配置:")
        for name, config in configs.items():
            print(f"\n后端: {name}")
            print(f"  - 启用: {config.enabled}")
            print(f"  - 优先级: {config.priority}")
            print(f"  - URL: {config.base_url}")
            print(f"  - 模型: {config.models}")
            print(f"  - 超时: {config.timeout}s")
            if hasattr(config, "stream_timeout"):
                print(f"  - 流式超时: {config.stream_timeout}s")
            print(f"  - 最大重试: {config.max_retries}")

        print("\n启用的后端（按优先级）:")
        for backend in list_enabled_backends():
            print(f"  - {backend}")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
