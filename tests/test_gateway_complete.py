"""
Gateway 重构完整测试集

包含 Phase 1, 2, 3 所有模块的测试。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18

运行方式:
    python tests/test_gateway_complete.py
"""

import os
import sys
import time
from typing import List, Tuple

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestResult:
    """测试结果"""
    def __init__(self, name: str, passed: bool, message: str = "", duration: float = 0):
        self.name = name
        self.passed = passed
        self.message = message
        self.duration = duration


def run_test(name: str, test_func) -> TestResult:
    """运行单个测试"""
    start = time.time()
    try:
        result = test_func()
        duration = time.time() - start
        if result is True:
            return TestResult(name, True, "OK", duration)
        elif result is False:
            return TestResult(name, False, "Failed", duration)
        else:
            return TestResult(name, True, str(result), duration)
    except Exception as e:
        duration = time.time() - start
        return TestResult(name, False, str(e), duration)


# ============== Phase 1 测试 ==============

def test_phase1_config_import():
    """Phase 1: 配置模块导入"""
    from src.gateway.config import BACKENDS, normalize_model_name, ROUTABLE_MODELS
    assert isinstance(BACKENDS, dict), "BACKENDS 应该是字典"
    assert len(BACKENDS) >= 2, "至少应有 2 个后端配置"
    assert callable(normalize_model_name), "normalize_model_name 应该是函数"
    return True


def test_phase1_routing_import():
    """Phase 1: 路由模块导入"""
    from src.gateway.routing import get_sorted_backends, get_backend_for_model
    assert callable(get_sorted_backends), "get_sorted_backends 应该是函数"
    assert callable(get_backend_for_model), "get_backend_for_model 应该是函数"
    return True


def test_phase1_normalization_import():
    """Phase 1: 规范化模块导入"""
    from src.gateway.normalization import (
        normalize_request_body,
        normalize_tools,
        normalize_tool_choice,
        normalize_messages,
    )
    assert callable(normalize_request_body)
    assert callable(normalize_tools)
    assert callable(normalize_tool_choice)
    assert callable(normalize_messages)
    return True


def test_phase1_proxy_import():
    """Phase 1: 代理模块导入（语法检查）"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/proxy.py"),
        doraise=True
    )
    return True


def test_phase1_tool_loop_import():
    """Phase 1: 工具循环模块导入（语法检查）"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/tool_loop.py"),
        doraise=True
    )
    return True


def test_phase1_sse_converter_import():
    """Phase 1: SSE 转换器导入（语法检查）"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/sse/converter.py"),
        doraise=True
    )
    return True


def test_phase1_augment_state_import():
    """Phase 1: Augment 状态管理导入（语法检查）"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/augment/state.py"),
        doraise=True
    )
    return True


def test_phase1_augment_endpoints_import():
    """Phase 1: Augment 端点导入（语法检查）"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/augment/endpoints.py"),
        doraise=True
    )
    return True


def test_phase1_augment_nodes_bridge_import():
    """Phase 1: Augment Nodes Bridge 导入（语法检查）"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/augment/nodes_bridge.py"),
        doraise=True
    )
    return True


def test_phase1_endpoints_import():
    """Phase 1: 端点模块导入（语法检查）"""
    import py_compile
    for f in ["openai.py", "anthropic.py", "models.py", "__init__.py"]:
        py_compile.compile(
            os.path.join(PROJECT_ROOT, f"src/gateway/endpoints/{f}"),
            doraise=True
        )
    return True


# ============== Phase 2 测试 ==============

def test_phase2_backend_interface_import():
    """Phase 2: 后端接口导入"""
    from src.gateway.backends.interface import GatewayBackend, BackendConfig
    assert BackendConfig is not None
    return True


def test_phase2_backend_registry_import():
    """Phase 2: 后端注册中心导入"""
    from src.gateway.backends.registry import GatewayRegistry
    registry = GatewayRegistry.get_instance()
    assert registry is not None
    return True


def test_phase2_antigravity_backend_syntax():
    """Phase 2: AntigravityBackend 语法检查"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/backends/antigravity.py"),
        doraise=True
    )
    return True


def test_phase2_copilot_backend_syntax():
    """Phase 2: CopilotBackend 语法检查"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/backends/copilot.py"),
        doraise=True
    )
    return True


def test_phase2_kiro_backend_syntax():
    """Phase 2: KiroGatewayBackend 语法检查"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/backends/kiro.py"),
        doraise=True
    )
    return True


def test_phase2_config_loader_import():
    """Phase 2: 配置加载器导入"""
    from src.gateway.config_loader import load_gateway_config, expand_env_vars
    assert callable(load_gateway_config)
    assert callable(expand_env_vars)
    return True


def test_phase2_config_loader_env_expand():
    """Phase 2: 环境变量展开"""
    from src.gateway.config_loader import expand_env_vars
    os.environ["TEST_VAR_12345"] = "hello"
    result = expand_env_vars("${TEST_VAR_12345:default}")
    assert result == "hello", f"Expected 'hello', got '{result}'"

    result2 = expand_env_vars("${MISSING_VAR_12345:fallback}")
    assert result2 == "fallback", f"Expected 'fallback', got '{result2}'"
    return True


def test_phase2_yaml_config_load():
    """Phase 2: YAML 配置加载"""
    from src.gateway.config_loader import load_gateway_config
    config_path = os.path.join(PROJECT_ROOT, "config/gateway.yaml")
    if os.path.exists(config_path):
        configs = load_gateway_config(config_path)
        assert len(configs) >= 2, "至少应有 2 个后端配置"
        assert "antigravity" in configs, "应包含 antigravity 后端"
        return f"加载 {len(configs)} 个后端"
    else:
        return "YAML 配置文件不存在，跳过"


# ============== Phase 3 测试 ==============

def test_phase3_adapter_import():
    """Phase 3: 适配器模块导入"""
    from src.gateway.adapter import (
        get_router,
        get_augment_router,
        BACKENDS,
    )
    assert callable(get_router)
    assert callable(get_augment_router)
    assert isinstance(BACKENDS, dict)
    return True


def test_phase3_compat_syntax():
    """Phase 3: 兼容层语法检查"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/compat.py"),
        doraise=True
    )
    return True


def test_phase3_gateway_init_import():
    """Phase 3: Gateway __init__ 导入"""
    from src.gateway import (
        get_adapter_router,
        get_adapter_augment_router,
        get_gateway_router,
        get_augment_router,
    )
    assert callable(get_adapter_router)
    assert callable(get_adapter_augment_router)
    return True


def test_phase3_env_switch():
    """Phase 3: 环境变量切换"""
    original = os.environ.get("USE_NEW_GATEWAY")
    try:
        os.environ["USE_NEW_GATEWAY"] = "false"
        import importlib
        import src.gateway.adapter as adapter_module
        importlib.reload(adapter_module)

        os.environ["USE_NEW_GATEWAY"] = "true"
        importlib.reload(adapter_module)
        return True
    finally:
        if original is None:
            os.environ.pop("USE_NEW_GATEWAY", None)
        else:
            os.environ["USE_NEW_GATEWAY"] = original


# ============== 功能测试 ==============

def test_func_get_sorted_backends():
    """功能: get_sorted_backends"""
    from src.gateway.routing import get_sorted_backends
    backends = get_sorted_backends()
    assert len(backends) >= 2, "至少应有 2 个后端"
    # 验证按优先级排序
    priorities = [b[1].get("priority", 999) for b in backends]
    assert priorities == sorted(priorities), "后端应按优先级排序"
    return f"{len(backends)} 个后端"


def test_func_get_backend_for_model():
    """功能: get_backend_for_model"""
    from src.gateway.routing import get_backend_for_model

    # 测试几个模型
    test_cases = [
        ("claude-sonnet-4-20250514", None),  # 可能是任意后端
        ("gpt-4", None),
        ("gemini-pro", None),
    ]

    results = []
    for model, expected in test_cases:
        backend = get_backend_for_model(model)
        results.append(f"{model}->{backend}")

    return ", ".join(results)


def test_func_normalize_request_body():
    """功能: normalize_request_body"""
    from src.gateway.normalization import normalize_request_body

    body = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    result = normalize_request_body(body)
    assert isinstance(result, dict)
    assert "messages" in result
    return True


def test_func_normalize_tools():
    """功能: normalize_tools"""
    from src.gateway.normalization import normalize_tools

    tools = [
        {"type": "function", "function": {"name": "test", "parameters": {}}}
    ]
    result = normalize_tools(tools)
    assert isinstance(result, list)
    assert len(result) == 1
    return True


def test_func_normalize_messages():
    """功能: normalize_messages"""
    from src.gateway.normalization import normalize_messages

    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    result = normalize_messages(messages)
    assert isinstance(result, list)
    assert len(result) == 2
    return True


def test_admin_endpoints_syntax():
    """功能: admin 端点语法检查"""
    import py_compile
    py_compile.compile(
        os.path.join(PROJECT_ROOT, "src/gateway/endpoints/admin.py"),
        doraise=True
    )
    return True


def test_admin_router_import():
    """功能: admin 路由器导入"""
    try:
        from src.gateway.endpoints.admin import router
        assert router is not None
        # 验证端点存在
        routes = [r.path for r in router.routes]
        assert "/health" in routes, f"缺少 /health 端点, 现有: {routes}"
        assert "/config/backend/{backend_key}/toggle" in routes, f"缺少 toggle 端点, 现有: {routes}"
        assert "/usage/api/balance" in routes, f"缺少 balance 端点, 现有: {routes}"
        assert "/usage/api/getLoginToken" in routes, f"缺少 getLoginToken 端点, 现有: {routes}"
        return f"{len(routes)} 个端点"
    except ImportError as e:
        # 测试环境可能没有 fastapi，使用语法检查代替
        if "fastapi" in str(e) or "httpx" in str(e):
            import py_compile
            py_compile.compile(
                os.path.join(PROJECT_ROOT, "src/gateway/endpoints/admin.py"),
                doraise=True
            )
            return "语法检查通过 (缺少 fastapi)"
        raise


# ============== 主测试运行器 ==============

def run_all_tests():
    """运行所有测试"""
    print("=" * 70)
    print("Gateway 重构完整测试集")
    print("=" * 70)
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"Python 版本: {sys.version}")
    print("=" * 70)

    # 定义测试组
    test_groups = [
        ("Phase 1: 模块抽取", [
            ("配置模块导入", test_phase1_config_import),
            ("路由模块导入", test_phase1_routing_import),
            ("规范化模块导入", test_phase1_normalization_import),
            ("代理模块语法", test_phase1_proxy_import),
            ("工具循环语法", test_phase1_tool_loop_import),
            ("SSE 转换器语法", test_phase1_sse_converter_import),
            ("Augment 状态语法", test_phase1_augment_state_import),
            ("Augment 端点语法", test_phase1_augment_endpoints_import),
            ("Augment Nodes Bridge 语法", test_phase1_augment_nodes_bridge_import),
            ("端点模块语法", test_phase1_endpoints_import),
        ]),
        ("Phase 2: 后端抽象", [
            ("后端接口导入", test_phase2_backend_interface_import),
            ("后端注册中心导入", test_phase2_backend_registry_import),
            ("AntigravityBackend 语法", test_phase2_antigravity_backend_syntax),
            ("CopilotBackend 语法", test_phase2_copilot_backend_syntax),
            ("KiroGatewayBackend 语法", test_phase2_kiro_backend_syntax),
            ("配置加载器导入", test_phase2_config_loader_import),
            ("环境变量展开", test_phase2_config_loader_env_expand),
            ("YAML 配置加载", test_phase2_yaml_config_load),
        ]),
        ("Phase 3: 渐进迁移", [
            ("适配器模块导入", test_phase3_adapter_import),
            ("兼容层语法", test_phase3_compat_syntax),
            ("Gateway __init__ 导入", test_phase3_gateway_init_import),
            ("环境变量切换", test_phase3_env_switch),
        ]),
        ("功能测试", [
            ("get_sorted_backends", test_func_get_sorted_backends),
            ("get_backend_for_model", test_func_get_backend_for_model),
            ("normalize_request_body", test_func_normalize_request_body),
            ("normalize_tools", test_func_normalize_tools),
            ("normalize_messages", test_func_normalize_messages),
            ("admin 端点语法", test_admin_endpoints_syntax),
            ("admin 路由器导入", test_admin_router_import),
        ]),
    ]

    all_results: List[TestResult] = []

    for group_name, tests in test_groups:
        print(f"\n{'─' * 70}")
        print(f"  {group_name}")
        print(f"{'─' * 70}")

        for test_name, test_func in tests:
            result = run_test(test_name, test_func)
            all_results.append(result)

            status = "✓" if result.passed else "✗"
            duration_str = f"({result.duration*1000:.1f}ms)"

            if result.passed:
                if result.message and result.message != "OK":
                    print(f"  {status} {test_name}: {result.message} {duration_str}")
                else:
                    print(f"  {status} {test_name} {duration_str}")
            else:
                print(f"  {status} {test_name}: {result.message} {duration_str}")

    # 汇总
    print("\n" + "=" * 70)
    print("测试结果汇总")
    print("=" * 70)

    passed = sum(1 for r in all_results if r.passed)
    failed = sum(1 for r in all_results if not r.passed)
    total_time = sum(r.duration for r in all_results)

    print(f"  总计: {len(all_results)} 项测试")
    print(f"  通过: {passed} 项")
    print(f"  失败: {failed} 项")
    print(f"  耗时: {total_time*1000:.1f}ms")
    print("=" * 70)

    if failed > 0:
        print("\n失败的测试:")
        for r in all_results:
            if not r.passed:
                print(f"  ✗ {r.name}: {r.message}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
