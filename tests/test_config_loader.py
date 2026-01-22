"""
Gateway 配置加载器测试

测试 config_loader.py 的各项功能。

作者: 浮浮酱 (Claude Sonnet 4.5)
创建日期: 2026-01-18
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.gateway.config_loader import (
    load_gateway_config,
    get_backend_config,
    list_enabled_backends,
    expand_env_vars,
)


def test_expand_env_vars():
    """测试环境变量展开"""
    print("=" * 60)
    print("测试 1: 环境变量展开")
    print("=" * 60)

    # 设置测试环境变量
    os.environ["TEST_STRING"] = "hello"
    os.environ["TEST_BOOL"] = "true"
    os.environ["TEST_INT"] = "42"
    os.environ["TEST_FLOAT"] = "3.14"

    tests = [
        ("${TEST_STRING:world}", "hello"),
        ("${MISSING:world}", "world"),
        ("${TEST_BOOL:false}", True),
        ("${TEST_INT:0}", 42),
        ("${TEST_FLOAT:0.0}", 3.14),
        ("${MISSING_BOOL:false}", False),
        ("${MISSING_LIST:[]}", []),
    ]

    for input_val, expected in tests:
        result = expand_env_vars(input_val)
        status = "✓" if result == expected else "✗"
        print(f"{status} {input_val} => {result} (期望: {expected})")

    print()


def test_load_config():
    """测试配置加载"""
    print("=" * 60)
    print("测试 2: 加载配置文件")
    print("=" * 60)

    try:
        configs = load_gateway_config()
        print(f"✓ 成功加载 {len(configs)} 个后端配置")

        for name, config in configs.items():
            print(f"\n后端: {name}")
            print(f"  - 启用: {config.enabled}")
            print(f"  - 优先级: {config.priority}")
            print(f"  - URL: {config.base_url}")
            print(f"  - 模型: {config.models[:3]}{'...' if len(config.models) > 3 else ''}")
            print(f"  - 超时: {config.timeout}s")
            if hasattr(config, "stream_timeout"):
                print(f"  - 流式超时: {config.stream_timeout}s")
            print(f"  - 最大重试: {config.max_retries}")

    except Exception as e:
        print(f"✗ 加载失败: {e}")
        import traceback
        traceback.print_exc()

    print()


def test_get_backend():
    """测试获取单个后端配置"""
    print("=" * 60)
    print("测试 3: 获取单个后端配置")
    print("=" * 60)

    try:
        config = get_backend_config("antigravity")
        print(f"✓ 成功获取 antigravity 配置")
        print(f"  - 名称: {config.name}")
        print(f"  - URL: {config.base_url}")
        print(f"  - 优先级: {config.priority}")
    except Exception as e:
        print(f"✗ 获取失败: {e}")

    try:
        config = get_backend_config("nonexistent")
        print(f"✗ 应该抛出 KeyError")
    except KeyError as e:
        print(f"✓ 正确抛出 KeyError: {e}")

    print()


def test_list_enabled():
    """测试列出启用的后端"""
    print("=" * 60)
    print("测试 4: 列出启用的后端")
    print("=" * 60)

    try:
        backends = list_enabled_backends()
        print(f"✓ 启用的后端（按优先级排序）:")
        for i, name in enumerate(backends, 1):
            print(f"  {i}. {name}")
    except Exception as e:
        print(f"✗ 列出失败: {e}")

    print()


def test_env_override():
    """测试环境变量覆盖"""
    print("=" * 60)
    print("测试 5: 环境变量覆盖")
    print("=" * 60)

    # 设置环境变量
    os.environ["COPILOT_ENABLED"] = "false"
    os.environ["KIRO_GATEWAY_ENABLED"] = "true"
    os.environ["KIRO_GATEWAY_ENDPOINT"] = "http://test.gateway.com/v1"

    try:
        configs = load_gateway_config()

        copilot_enabled = configs["copilot"].enabled
        kiro_enabled = configs["kiro-gateway"].enabled
        kiro_url = configs["kiro-gateway"].base_url

        print(f"✓ Copilot 启用状态: {copilot_enabled} (期望: False)")
        print(f"✓ Kiro Gateway 启用状态: {kiro_enabled} (期望: True)")
        print(f"✓ Kiro Gateway URL: {kiro_url}")

        # 验证
        assert copilot_enabled == False, "Copilot 应该被禁用"
        assert kiro_enabled == True, "Kiro Gateway 应该被启用"
        assert kiro_url == "http://test.gateway.com/v1", "URL 应该被覆盖"

        print("\n✓ 所有环境变量覆盖测试通过")

    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()

    # 清理环境变量
    del os.environ["COPILOT_ENABLED"]
    del os.environ["KIRO_GATEWAY_ENABLED"]
    del os.environ["KIRO_GATEWAY_ENDPOINT"]

    print()


def test_model_support():
    """测试模型支持检查"""
    print("=" * 60)
    print("测试 6: 模型支持检查")
    print("=" * 60)

    try:
        configs = load_gateway_config()

        # Antigravity 支持所有模型
        antigravity = configs["antigravity"]
        print(f"✓ Antigravity 支持 'claude-sonnet-4.5': {antigravity.supports_model('claude-sonnet-4.5')}")
        print(f"✓ Antigravity 支持 'gpt-5.2': {antigravity.supports_model('gpt-5.2')}")

        # Copilot 只支持特定模型
        copilot = configs["copilot"]
        print(f"✓ Copilot 支持 'gpt-4': {copilot.supports_model('gpt-4')}")
        print(f"✓ Copilot 支持 'claude-sonnet-4.5': {copilot.supports_model('claude-sonnet-4.5')}")

    except Exception as e:
        print(f"✗ 测试失败: {e}")

    print()


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("Gateway 配置加载器测试套件")
    print("=" * 60 + "\n")

    test_expand_env_vars()
    test_load_config()
    test_get_backend()
    test_list_enabled()
    test_env_override()
    test_model_support()

    print("=" * 60)
    print("所有测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
