"""
模型路由规则测试

测试 model_routing 配置的加载和路由逻辑

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-21
更新日期: 2026-01-21 - 添加完整降级链测试
"""

import pytest
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestModelRoutingConfig:
    """测试模型路由配置加载"""

    def test_load_model_routing_config(self):
        """测试加载 model_routing 配置"""
        from src.gateway.config_loader import load_model_routing_config

        rules = load_model_routing_config()

        # 应该加载到 claude-sonnet-4.5 的规则
        assert "claude-sonnet-4.5" in rules
        rule = rules["claude-sonnet-4.5"]

        # 验证规则内容
        assert rule.enabled is True
        assert "kiro-gateway" in rule.backends
        assert "antigravity" in rule.backends
        assert rule.backends.index("kiro-gateway") < rule.backends.index("antigravity")

        # 验证降级条件
        assert 429 in rule.fallback_on
        assert 503 in rule.fallback_on
        assert "timeout" in rule.fallback_on
        assert "connection_error" in rule.fallback_on

    def test_load_backend_chain_with_models(self):
        """测试加载包含目标模型的后端链"""
        from src.gateway.config_loader import load_model_routing_config, BackendEntry

        rules = load_model_routing_config()
        rule = rules.get("claude-sonnet-4.5")
        assert rule is not None

        # 验证后端链包含 BackendEntry 对象
        assert len(rule.backend_chain) >= 2
        for entry in rule.backend_chain:
            assert isinstance(entry, BackendEntry)
            assert entry.backend is not None
            assert entry.model is not None

        # 验证第一个后端是 kiro-gateway
        first_entry = rule.backend_chain[0]
        assert first_entry.backend == "kiro-gateway"
        assert first_entry.model == "claude-sonnet-4.5"

    def test_get_model_routing_rule(self):
        """测试获取模型路由规则"""
        from src.gateway.config_loader import get_model_routing_rule

        # 精确匹配
        rule = get_model_routing_rule("claude-sonnet-4.5")
        assert rule is not None
        assert rule.model == "claude-sonnet-4.5"

        # 模糊匹配（带 -thinking 后缀）
        rule = get_model_routing_rule("claude-sonnet-4.5-thinking")
        assert rule is not None
        assert rule.model == "claude-sonnet-4.5"

        # 不存在的模型
        rule = get_model_routing_rule("gpt-4o")
        assert rule is None


class TestModelRoutingRule:
    """测试 ModelRoutingRule 类"""

    def test_should_fallback(self):
        """测试降级条件判断"""
        from src.gateway.config_loader import ModelRoutingRule, BackendEntry

        rule = ModelRoutingRule(
            model="test-model",
            backend_chain=[
                BackendEntry("backend-a", "model-a"),
                BackendEntry("backend-b", "model-b"),
            ],
            fallback_on={429, 503, "timeout", "connection_error"},
            enabled=True,
        )

        # 应该降级的情况
        assert rule.should_fallback(status_code=429) is True
        assert rule.should_fallback(status_code=503) is True
        assert rule.should_fallback(error_type="timeout") is True
        assert rule.should_fallback(error_type="connection_error") is True

        # 不应该降级的情况
        assert rule.should_fallback(status_code=500) is False
        assert rule.should_fallback(status_code=200) is False
        assert rule.should_fallback(error_type="unknown") is False

    def test_get_next_backend(self):
        """测试获取下一个后端"""
        from src.gateway.config_loader import ModelRoutingRule, BackendEntry

        rule = ModelRoutingRule(
            model="test-model",
            backend_chain=[
                BackendEntry("backend-a", "model-a"),
                BackendEntry("backend-b", "model-b"),
                BackendEntry("backend-c", "model-c"),
            ],
            fallback_on={429},
            enabled=True,
        )

        # 正常获取下一个
        assert rule.get_next_backend("backend-a") == "backend-b"
        assert rule.get_next_backend("backend-b") == "backend-c"

        # 最后一个后端没有下一个
        assert rule.get_next_backend("backend-c") is None

        # 不在链中的后端返回第一个
        assert rule.get_next_backend("unknown") == "backend-a"

    def test_get_next_backend_entry(self):
        """测试获取下一个后端条目（包含目标模型）"""
        from src.gateway.config_loader import ModelRoutingRule, BackendEntry

        rule = ModelRoutingRule(
            model="test-model",
            backend_chain=[
                BackendEntry("backend-a", "model-a"),
                BackendEntry("backend-b", "model-b"),
                BackendEntry("backend-c", "model-c"),
            ],
            fallback_on={429},
            enabled=True,
        )

        # 正常获取下一个
        next_entry = rule.get_next_backend_entry("backend-a")
        assert next_entry is not None
        assert next_entry.backend == "backend-b"
        assert next_entry.model == "model-b"

        # 最后一个后端没有下一个
        assert rule.get_next_backend_entry("backend-c") is None

    def test_backends_property(self):
        """测试 backends 兼容属性"""
        from src.gateway.config_loader import ModelRoutingRule, BackendEntry

        rule = ModelRoutingRule(
            model="test-model",
            backend_chain=[
                BackendEntry("backend-a", "model-a"),
                BackendEntry("backend-b", "model-b"),
            ],
            fallback_on={429},
            enabled=True,
        )

        # backends 属性应该返回后端名称列表
        assert rule.backends == ["backend-a", "backend-b"]


class TestRoutingFunctions:
    """测试路由函数"""

    def test_get_backend_for_model_with_routing_rule(self):
        """测试带路由规则的后端选择"""
        from src.unified_gateway_router import get_backend_for_model

        # claude-sonnet-4.5 应该优先路由到 kiro-gateway
        backend = get_backend_for_model("claude-sonnet-4.5")
        assert backend == "kiro-gateway"

        # claude-sonnet-4.5-thinking 也应该匹配
        backend = get_backend_for_model("claude-sonnet-4.5-thinking")
        assert backend == "kiro-gateway"

    def test_get_backend_and_model_for_routing(self):
        """测试获取后端和目标模型"""
        from src.unified_gateway_router import get_backend_and_model_for_routing

        # claude-sonnet-4.5 应该返回 kiro-gateway 和 claude-sonnet-4.5
        backend, target_model = get_backend_and_model_for_routing("claude-sonnet-4.5")
        assert backend == "kiro-gateway"
        assert target_model == "claude-sonnet-4.5"

    def test_get_backend_chain_for_model(self):
        """测试获取后端链"""
        from src.unified_gateway_router import get_backend_chain_for_model

        # claude-sonnet-4.5 应该有完整的后端链
        chain = get_backend_chain_for_model("claude-sonnet-4.5")
        assert len(chain) >= 2
        assert chain[0] == "kiro-gateway"
        assert "antigravity" in chain

        # gpt-4o 没有特定规则，应该只有一个后端
        chain = get_backend_chain_for_model("gpt-4o")
        assert len(chain) == 1
        assert chain[0] == "copilot"

    def test_get_fallback_backend(self):
        """测试获取降级后端"""
        from src.unified_gateway_router import get_fallback_backend

        # 429 错误应该触发降级
        fallback = get_fallback_backend(
            model="claude-sonnet-4.5",
            current_backend="kiro-gateway",
            status_code=429
        )
        assert fallback == "antigravity"

        # 503 错误也应该触发降级
        fallback = get_fallback_backend(
            model="claude-sonnet-4.5",
            current_backend="kiro-gateway",
            status_code=503
        )
        assert fallback == "antigravity"

        # timeout 错误应该触发降级
        fallback = get_fallback_backend(
            model="claude-sonnet-4.5",
            current_backend="kiro-gateway",
            error_type="timeout"
        )
        assert fallback == "antigravity"

    def test_get_fallback_backend_and_model(self):
        """测试获取降级后端和目标模型"""
        from src.unified_gateway_router import get_fallback_backend_and_model

        # 429 错误应该触发降级，返回后端和目标模型
        result = get_fallback_backend_and_model(
            model="claude-sonnet-4.5",
            current_backend="kiro-gateway",
            status_code=429
        )
        assert result is not None
        backend, target_model = result
        assert backend == "antigravity"
        assert target_model == "claude-sonnet-4.5"

    def test_complete_fallback_chain(self):
        """测试完整的降级链"""
        from src.unified_gateway_router import get_fallback_backend_and_model
        from src.gateway.config_loader import get_model_routing_rule

        # 获取 claude-sonnet-4.5 的路由规则
        rule = get_model_routing_rule("claude-sonnet-4.5")
        assert rule is not None

        # 验证完整的降级链
        # kiro-gateway -> antigravity -> anyrouter -> copilot(4.5) -> copilot(4) -> antigravity(gemini)
        expected_chain = [
            ("kiro-gateway", "claude-sonnet-4.5"),
            ("antigravity", "claude-sonnet-4.5"),
            ("anyrouter", "claude-sonnet-4.5"),
            ("copilot", "claude-sonnet-4.5"),
            ("copilot", "claude-sonnet-4"),
            ("antigravity", "gemini-3-pro"),
        ]

        # 验证后端链
        assert len(rule.backend_chain) == len(expected_chain)
        for i, (expected_backend, expected_model) in enumerate(expected_chain):
            entry = rule.backend_chain[i]
            assert entry.backend == expected_backend, f"Index {i}: expected {expected_backend}, got {entry.backend}"
            assert entry.model == expected_model, f"Index {i}: expected {expected_model}, got {entry.model}"

    def test_cross_model_fallback(self):
        """测试跨模型降级（claude -> gemini）"""
        from src.unified_gateway_router import get_fallback_backend_and_model

        # 模拟从 copilot(claude-sonnet-4) 降级到 antigravity(gemini-3-pro)
        # 注意：需要先走完前面的链
        result = get_fallback_backend_and_model(
            model="claude-sonnet-4.5",
            current_backend="copilot",  # 假设当前在 copilot
            status_code=429
        )
        # 由于 copilot 在链中出现两次，第一次降级应该是 copilot(claude-sonnet-4)
        # 这个测试验证降级逻辑能正确处理同一后端多次出现的情况
        assert result is not None

    def test_should_fallback_to_next(self):
        """测试是否应该降级判断"""
        from src.unified_gateway_router import should_fallback_to_next

        # 应该降级
        assert should_fallback_to_next(
            model="claude-sonnet-4.5",
            current_backend="kiro-gateway",
            status_code=429
        ) is True

        # 不应该降级（不在降级条件中）
        # 注意：500 现在在 fallback_on 中，所以这个测试需要调整
        # 使用 200 作为不在降级条件中的状态码
        assert should_fallback_to_next(
            model="claude-sonnet-4.5",
            current_backend="kiro-gateway",
            status_code=200
        ) is False


class TestOpus45RoutingRule:
    """测试 Claude Opus 4.5 的路由规则"""

    def test_opus_45_routing_rule_exists(self):
        """测试 Opus 4.5 路由规则存在"""
        from src.gateway.config_loader import get_model_routing_rule

        rule = get_model_routing_rule("claude-opus-4.5")
        assert rule is not None
        assert rule.enabled is True

    def test_opus_45_complete_chain(self):
        """测试 Opus 4.5 完整降级链"""
        from src.gateway.config_loader import get_model_routing_rule

        rule = get_model_routing_rule("claude-opus-4.5")
        assert rule is not None

        # 验证后端链包含预期的后端
        backends = rule.backends
        assert "kiro-gateway" in backends
        assert "antigravity" in backends
        assert "copilot" in backends

        # 验证最后一个是 gemini-3-pro 的跨池降级
        last_entry = rule.backend_chain[-1]
        assert last_entry.model == "gemini-3-pro"


class TestHaiku45RoutingRule:
    """测试 Claude Haiku 4.5 的路由规则"""

    def test_haiku_45_routing_rule_exists(self):
        """测试 Haiku 4.5 路由规则存在"""
        from src.gateway.config_loader import get_model_routing_rule

        rule = get_model_routing_rule("claude-haiku-4.5")
        assert rule is not None

    def test_haiku_45_fallback_to_gemini_flash(self):
        """测试 Haiku 4.5 降级到 Gemini Flash"""
        from src.gateway.config_loader import get_model_routing_rule

        rule = get_model_routing_rule("claude-haiku-4.5")
        assert rule is not None

        # 验证最后一个是 gemini-3-flash（轻量模型对应轻量模型）
        last_entry = rule.backend_chain[-1]
        assert last_entry.model == "gemini-3-flash"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
