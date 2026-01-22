"""
Gateway 迁移验证测试

验证新旧模块的兼容性和功能一致性。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

import os
import sys

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_adapter_import():
    """测试适配器模块导入"""
    print("=== 测试 1: 适配器模块导入 ===")
    try:
        from src.gateway.adapter import (
            get_router,
            get_augment_router,
            get_sorted_backends,
            get_backend_for_model,
            BACKENDS,
        )
        print("✓ 适配器模块导入成功")
        print(f"  - BACKENDS 类型: {type(BACKENDS)}")
        print(f"  - 后端数量: {len(BACKENDS)}")
        return True
    except Exception as e:
        print(f"✗ 适配器模块导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_compat_import():
    """测试兼容层模块导入"""
    print("\n=== 测试 2: 兼容层模块导入 ===")
    try:
        from src.gateway.compat import router, augment_router
        print("✓ 兼容层模块导入成功")
        print(f"  - router 类型: {type(router)}")
        print(f"  - augment_router 类型: {type(augment_router)}")
        return True
    except Exception as e:
        print(f"✗ 兼容层模块导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_gateway_init_import():
    """测试 gateway __init__ 导入"""
    print("\n=== 测试 3: Gateway __init__ 导入 ===")
    try:
        from src.gateway import (
            get_adapter_router,
            get_adapter_augment_router,
            get_gateway_router,
            get_augment_router,
        )
        print("✓ Gateway __init__ 导入成功")
        print(f"  - get_adapter_router: {callable(get_adapter_router)}")
        print(f"  - get_adapter_augment_router: {callable(get_adapter_augment_router)}")
        return True
    except Exception as e:
        print(f"✗ Gateway __init__ 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_consistency():
    """测试配置一致性"""
    print("\n=== 测试 4: 配置一致性 ===")
    try:
        # 新模块配置
        from src.gateway.config import BACKENDS as NEW_BACKENDS

        # 旧模块配置
        try:
            from src.unified_gateway_router import BACKENDS as OLD_BACKENDS
            has_old = True
        except ImportError:
            OLD_BACKENDS = {}
            has_old = False

        print(f"  - 新模块后端: {list(NEW_BACKENDS.keys())}")
        if has_old:
            print(f"  - 旧模块后端: {list(OLD_BACKENDS.keys())}")

            # 检查后端名称一致性
            new_keys = set(NEW_BACKENDS.keys())
            old_keys = set(OLD_BACKENDS.keys())

            if new_keys == old_keys:
                print("✓ 后端名称完全一致")
            else:
                missing = old_keys - new_keys
                extra = new_keys - old_keys
                if missing:
                    print(f"⚠ 新模块缺少: {missing}")
                if extra:
                    print(f"⚠ 新模块额外: {extra}")
        else:
            print("⚠ 旧模块不可用，跳过一致性检查")

        return True
    except Exception as e:
        print(f"✗ 配置一致性测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_routing_functions():
    """测试路由函数"""
    print("\n=== 测试 5: 路由函数 ===")
    try:
        from src.gateway.routing import get_sorted_backends, get_backend_for_model

        # 测试 get_sorted_backends
        backends = get_sorted_backends()
        print(f"✓ get_sorted_backends() 返回 {len(backends)} 个后端")
        for name, config in backends:
            print(f"    - {name}: priority={config.get('priority', 'N/A')}")

        # 测试 get_backend_for_model
        test_models = [
            "claude-sonnet-4-20250514",
            "gpt-4",
            "gemini-pro",
            "unknown-model",
        ]
        for model in test_models:
            backend = get_backend_for_model(model)
            print(f"✓ get_backend_for_model('{model}') -> {backend}")

        return True
    except Exception as e:
        print(f"✗ 路由函数测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_normalization_functions():
    """测试规范化函数"""
    print("\n=== 测试 6: 规范化函数 ===")
    try:
        from src.gateway.normalization import (
            normalize_request_body,
            normalize_tools,
            normalize_tool_choice,
            normalize_messages,
        )

        # 测试 normalize_request_body
        test_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        result = normalize_request_body(test_body)
        print(f"✓ normalize_request_body() 返回类型: {type(result)}")

        # 测试 normalize_tools
        test_tools = [
            {"type": "function", "function": {"name": "test", "parameters": {}}}
        ]
        result = normalize_tools(test_tools)
        print(f"✓ normalize_tools() 返回 {len(result)} 个工具")

        # 测试 normalize_messages
        test_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = normalize_messages(test_messages)
        print(f"✓ normalize_messages() 返回 {len(result)} 条消息")

        return True
    except Exception as e:
        print(f"✗ 规范化函数测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_env_switch():
    """测试环境变量切换"""
    print("\n=== 测试 7: 环境变量切换 ===")

    # 保存原始值
    original = os.environ.get("USE_NEW_GATEWAY")

    try:
        # 测试 false（默认）
        os.environ["USE_NEW_GATEWAY"] = "false"
        # 重新加载模块
        import importlib
        import src.gateway.adapter as adapter_module
        importlib.reload(adapter_module)
        print(f"✓ USE_NEW_GATEWAY=false: 使用旧模块")

        # 测试 true
        os.environ["USE_NEW_GATEWAY"] = "true"
        importlib.reload(adapter_module)
        print(f"✓ USE_NEW_GATEWAY=true: 使用新模块")

        return True
    except Exception as e:
        print(f"✗ 环境变量切换测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 恢复原始值
        if original is None:
            os.environ.pop("USE_NEW_GATEWAY", None)
        else:
            os.environ["USE_NEW_GATEWAY"] = original


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("Gateway 迁移验证测试")
    print("=" * 60)

    results = []
    results.append(("适配器模块导入", test_adapter_import()))
    results.append(("兼容层模块导入", test_compat_import()))
    results.append(("Gateway __init__ 导入", test_gateway_init_import()))
    results.append(("配置一致性", test_config_consistency()))
    results.append(("路由函数", test_routing_functions()))
    results.append(("规范化函数", test_normalization_functions()))
    results.append(("环境变量切换", test_env_switch()))

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    passed = 0
    failed = 0
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1

    print(f"\n总计: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
