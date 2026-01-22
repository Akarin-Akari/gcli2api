"""
Rate Limiting Tests - 风控限流模块单元测试

[FIX 2026-01-21] 测试新增的风控限流模块：
- retry_utils: Duration 解析和 RetryInfo 提取
- rate_limit_registry: 限流状态池管理
- rate_limiter: 请求防抖
- antigravity_retry_policies: 重试策略决策
"""

import asyncio
import json
import time
import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestRetryUtils:
    """测试 retry_utils 模块"""

    def test_parse_duration_ms_seconds(self):
        """测试秒级解析"""
        from src.retry_utils import parse_duration_ms

        assert parse_duration_ms("1.5s") == 1500
        assert parse_duration_ms("2s") == 2000
        assert parse_duration_ms("0.5s") == 500

    def test_parse_duration_ms_milliseconds(self):
        """测试毫秒级解析"""
        from src.retry_utils import parse_duration_ms

        assert parse_duration_ms("200ms") == 200
        assert parse_duration_ms("1500ms") == 1500

    def test_parse_duration_ms_minutes(self):
        """测试分钟级解析"""
        from src.retry_utils import parse_duration_ms

        assert parse_duration_ms("1m") == 60000
        assert parse_duration_ms("30m") == 1800000

    def test_parse_duration_ms_hours(self):
        """测试小时级解析"""
        from src.retry_utils import parse_duration_ms

        assert parse_duration_ms("1h") == 3600000
        assert parse_duration_ms("2h") == 7200000

    def test_parse_duration_ms_combined(self):
        """测试组合格式解析"""
        from src.retry_utils import parse_duration_ms

        # 1h16m0.667s = 3600000 + 960000 + 667 = 4560667
        assert parse_duration_ms("1h16m0.667s") == 4560667

    def test_parse_duration_ms_invalid(self):
        """测试无效输入"""
        from src.retry_utils import parse_duration_ms

        assert parse_duration_ms("invalid") is None
        assert parse_duration_ms("") is None
        assert parse_duration_ms(None) is None

    def test_parse_retry_delay_retry_info(self):
        """测试 RetryInfo 解析"""
        from src.retry_utils import parse_retry_delay

        error_json = json.dumps({
            "error": {
                "details": [{
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "1.203608125s"
                }]
            }
        })

        result = parse_retry_delay(error_json)
        assert result == 1204  # 四舍五入

    def test_parse_retry_delay_quota_reset(self):
        """测试 quotaResetDelay 解析"""
        from src.retry_utils import parse_retry_delay

        error_json = json.dumps({
            "error": {
                "details": [{
                    "metadata": {
                        "quotaResetDelay": "30s"
                    }
                }]
            }
        })

        result = parse_retry_delay(error_json)
        assert result == 30000

    def test_parse_retry_delay_priority(self):
        """测试解析优先级：RetryInfo > quotaResetDelay"""
        from src.retry_utils import parse_retry_delay

        error_json = json.dumps({
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "5s"
                    },
                    {
                        "metadata": {
                            "quotaResetDelay": "30s"
                        }
                    }
                ]
            }
        })

        result = parse_retry_delay(error_json)
        assert result == 5000  # 应该返回 RetryInfo 的值

    def test_parse_retry_delay_invalid(self):
        """测试无效输入"""
        from src.retry_utils import parse_retry_delay

        assert parse_retry_delay("not json") is None
        assert parse_retry_delay("{}") is None
        assert parse_retry_delay('{"error": {}}') is None

    def test_parse_retry_delay_seconds(self):
        """测试秒级返回"""
        from src.retry_utils import parse_retry_delay_seconds

        error_json = json.dumps({
            "error": {
                "details": [{
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "1.5s"
                }]
            }
        })

        result = parse_retry_delay_seconds(error_json)
        assert result == 1.5


class TestRateLimitRegistry:
    """测试 rate_limit_registry 模块"""

    @pytest.mark.asyncio
    async def test_mark_rate_limited(self):
        """测试标记限流"""
        from src.rate_limit_registry import RateLimitRegistry

        registry = RateLimitRegistry()
        await registry.mark_rate_limited(
            "cred1",
            "model1",
            status_code=429,
            error_text="Rate limited",
            cooldown_until=time.time() + 60,
            reason="rate_limit",
        )

        state = await registry.get_state("cred1", "model1")
        assert state is not None
        assert state.last_status == 429
        assert state.reason == "rate_limit"
        assert state.is_in_cooldown()

    @pytest.mark.asyncio
    async def test_clear_rate_limit(self):
        """测试清除限流"""
        from src.rate_limit_registry import RateLimitRegistry

        registry = RateLimitRegistry()
        await registry.mark_rate_limited(
            "cred1",
            "model1",
            status_code=429,
            cooldown_until=time.time() + 60,
        )

        await registry.clear_rate_limit("cred1", "model1")

        state = await registry.get_state("cred1", "model1")
        assert state is not None
        assert not state.is_in_cooldown()
        assert state.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_is_rate_limited(self):
        """测试限流检查"""
        from src.rate_limit_registry import RateLimitRegistry

        registry = RateLimitRegistry()

        # 未标记的应该返回 False
        assert not await registry.is_rate_limited("cred1", "model1")

        # 标记后应该返回 True
        await registry.mark_rate_limited(
            "cred1",
            "model1",
            status_code=429,
            cooldown_until=time.time() + 60,
        )
        assert await registry.is_rate_limited("cred1", "model1")

    @pytest.mark.asyncio
    async def test_consecutive_failures(self):
        """测试连续失败计数"""
        from src.rate_limit_registry import RateLimitRegistry

        registry = RateLimitRegistry()

        for i in range(3):
            await registry.mark_rate_limited(
                "cred1",
                "model1",
                status_code=429,
            )

        state = await registry.get_state("cred1", "model1")
        assert state.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_get_cooldown_entries(self):
        """测试获取冷却中的条目"""
        from src.rate_limit_registry import RateLimitRegistry

        registry = RateLimitRegistry()

        # 添加一个在冷却中的
        await registry.mark_rate_limited(
            "cred1",
            "model1",
            status_code=429,
            cooldown_until=time.time() + 60,
        )

        # 添加一个已过期的
        await registry.mark_rate_limited(
            "cred2",
            "model2",
            status_code=429,
            cooldown_until=time.time() - 10,  # 已过期
        )

        entries = await registry.get_cooldown_entries()
        assert len(entries) == 1
        assert entries[0]["credential"] == "cred1"


class TestRateLimiter:
    """测试 rate_limiter 模块"""

    @pytest.mark.asyncio
    async def test_first_call_immediate(self):
        """测试第一次调用立即返回"""
        from src.rate_limiter import RateLimiter

        limiter = RateLimiter(min_interval_ms=500)
        start = time.monotonic()
        await limiter.wait()
        elapsed = time.monotonic() - start

        assert elapsed < 0.1  # 应该几乎立即返回

    @pytest.mark.asyncio
    async def test_second_call_waits(self):
        """测试第二次调用需要等待"""
        from src.rate_limiter import RateLimiter

        limiter = RateLimiter(min_interval_ms=200)

        await limiter.wait()  # 第一次
        start = time.monotonic()
        await limiter.wait()  # 第二次
        elapsed = time.monotonic() - start

        assert elapsed >= 0.15  # 应该等待约 200ms（允许一些误差）

    @pytest.mark.asyncio
    async def test_keyed_rate_limiter(self):
        """测试按键分组的限制器"""
        from src.rate_limiter import KeyedRateLimiter

        limiter = KeyedRateLimiter(min_interval_ms=200)

        # 不同键应该独立
        start = time.monotonic()
        await limiter.wait("key1")
        await limiter.wait("key2")  # 不同键，应该立即返回
        elapsed = time.monotonic() - start

        assert elapsed < 0.1  # 两个不同键应该几乎立即完成

    @pytest.mark.asyncio
    async def test_adaptive_rate_limiter(self):
        """测试自适应限制器"""
        from src.rate_limiter import AdaptiveRateLimiter

        limiter = AdaptiveRateLimiter(
            base_interval_ms=100,
            min_interval_ms=50,
            max_interval_ms=500,
        )

        initial_interval = limiter.current_interval_ms
        assert initial_interval == 100

        # 成功后应该减少间隔
        limiter.record_success()
        assert limiter.current_interval_ms < initial_interval

        # 失败后应该增加间隔
        limiter.record_failure()
        assert limiter.current_interval_ms > limiter.current_interval_ms * 0.5


class TestRetryPolicies:
    """测试 antigravity_retry_policies 模块"""

    def test_determine_strategy_429(self):
        """测试 429 策略"""
        from src.antigravity_retry_policies import determine_retry_strategy

        strategy = determine_retry_strategy(429, "")
        assert strategy.kind == "exponential"
        assert strategy.base_ms == 1000

    def test_determine_strategy_429_capacity_exhausted(self):
        """测试 429 额度耗尽策略"""
        from src.antigravity_retry_policies import determine_retry_strategy

        strategy = determine_retry_strategy(429, "MODEL_CAPACITY_EXHAUSTED")
        assert strategy.kind == "exponential"
        assert strategy.base_ms == 5000  # 更长的起始延迟

    def test_determine_strategy_5xx(self):
        """测试 5xx 策略"""
        from src.antigravity_retry_policies import determine_retry_strategy

        for code in [500, 502, 503, 504, 529]:
            strategy = determine_retry_strategy(code, "")
            assert strategy.kind == "exponential"
            assert strategy.max_ms == 60000  # 60s 上限

    def test_determine_strategy_400(self):
        """测试 400 不重试"""
        from src.antigravity_retry_policies import determine_retry_strategy

        strategy = determine_retry_strategy(400, "")
        assert strategy.kind == "none"

    def test_determine_strategy_401_403(self):
        """测试 401/403 不重试"""
        from src.antigravity_retry_policies import determine_retry_strategy

        for code in [401, 403]:
            strategy = determine_retry_strategy(code, "")
            assert strategy.kind == "none"

    def test_compute_delay_exponential(self):
        """测试指数退避延迟计算"""
        from src.antigravity_retry_policies import RetryStrategy

        strategy = RetryStrategy(
            kind="exponential",
            base_ms=1000,
            max_ms=10000,
            jitter_ratio=0,  # 禁用抖动以便精确测试
        )

        # attempt 0: 1000ms = 1s
        assert strategy.compute_delay(0) == 1.0
        # attempt 1: 2000ms = 2s
        assert strategy.compute_delay(1) == 2.0
        # attempt 2: 4000ms = 4s
        assert strategy.compute_delay(2) == 4.0
        # attempt 3: 8000ms = 8s
        assert strategy.compute_delay(3) == 8.0
        # attempt 4: 16000ms -> 10000ms (max) = 10s
        assert strategy.compute_delay(4) == 10.0

    def test_compute_delay_with_override(self):
        """测试覆盖延迟"""
        from src.antigravity_retry_policies import RetryStrategy

        strategy = RetryStrategy(
            kind="exponential",
            base_ms=1000,
            jitter_ratio=0,
        )

        # 覆盖延迟应该优先
        assert strategy.compute_delay(0, override_delay_ms=5000) == 5.0

    def test_get_retry_delay_from_error(self):
        """测试从错误获取延迟"""
        from src.antigravity_retry_policies import get_retry_delay_from_error

        error_json = json.dumps({
            "error": {
                "details": [{
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "2s"
                }]
            }
        })

        delay = get_retry_delay_from_error(429, error_json, attempt=0)
        # 由于默认有 20% 的抖动，结果应该在 1.6s 到 2.4s 之间
        assert 1.6 <= delay <= 2.4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
