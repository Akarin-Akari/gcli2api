"""
测试 P2 修复功能

验证：
1. P2-1: 结构化日志
2. P2-2: 性能监控
"""

import sys
import os
import unittest
import json
import time
import asyncio

# 设置 UTF-8 编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 确保可以导入父目录的模块
gcli2api_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, gcli2api_path)

from log import log, LOG_LEVELS


class TestStructuredLogging(unittest.TestCase):
    """P2-1: 测试结构化日志"""

    def test_log_levels(self):
        """测试日志级别"""
        print("\n--- Testing log levels ---")
        
        expected_levels = ["debug", "info", "route", "success", "fallback", "perf", "warning", "error", "critical"]
        for level in expected_levels:
            self.assertIn(level, LOG_LEVELS)
            print(f"  Level '{level}' exists: OK")

    def test_basic_logging(self):
        """测试基本日志功能"""
        print("\n--- Testing basic logging ---")
        
        # 这些调用不应该抛出异常
        log.debug("Debug message")
        log.info("Info message")
        log.warning("Warning message")
        log.success("Success message")
        log.route("Route message")
        log.fallback("Fallback message")
        log.perf("Performance message")
        
        print("  All basic log calls: OK")

    def test_structured_logging_with_extra(self):
        """测试带额外字段的结构化日志"""
        print("\n--- Testing structured logging with extra fields ---")
        
        # 测试带额外字段的日志
        log.info(
            "Request processed",
            tag="API",
            request_id="req_123",
            duration_ms=150.5,
            status_code=200
        )
        
        log.warning(
            "Rate limit approaching",
            tag="RATE_LIMIT",
            current_rate=95,
            max_rate=100
        )
        
        print("  Structured logging with extra fields: OK")

    def test_logger_methods(self):
        """测试 Logger 类的方法"""
        print("\n--- Testing Logger methods ---")
        
        # 测试获取当前日志级别
        level = log.get_current_level()
        self.assertIn(level, LOG_LEVELS)
        print(f"  Current log level: {level}")
        
        # 测试颜色状态
        color_enabled = log.is_color_enabled()
        print(f"  Color enabled: {color_enabled}")
        
        # 测试结构化日志状态
        structured_enabled = log.is_structured_enabled()
        print(f"  Structured logging enabled: {structured_enabled}")


class TestPerformanceMonitoring(unittest.TestCase):
    """P2-2: 测试性能监控"""

    def setUp(self):
        """清除之前的性能指标"""
        log.clear_metrics()

    def test_timer_context_manager(self):
        """测试计时器上下文管理器"""
        print("\n--- Testing timer context manager ---")
        
        with log.timer("test_operation", tag="TEST"):
            time.sleep(0.1)  # 模拟 100ms 操作
        
        metrics = log.get_metrics("test_operation")
        self.assertEqual(metrics["count"], 1)
        self.assertGreater(metrics["avg_ms"], 90)  # 至少 90ms
        self.assertLess(metrics["avg_ms"], 200)  # 不超过 200ms
        
        print(f"  Timer recorded: {metrics['avg_ms']:.2f}ms")

    def test_multiple_timings(self):
        """测试多次计时"""
        print("\n--- Testing multiple timings ---")
        
        for i in range(5):
            with log.timer("repeated_operation"):
                time.sleep(0.01)  # 10ms
        
        metrics = log.get_metrics("repeated_operation")
        self.assertEqual(metrics["count"], 5)
        self.assertIsNotNone(metrics["min_ms"])
        self.assertIsNotNone(metrics["max_ms"])
        self.assertIsNotNone(metrics["avg_ms"])
        self.assertIsNotNone(metrics["p50_ms"])
        
        print(f"  Count: {metrics['count']}")
        print(f"  Min: {metrics['min_ms']:.2f}ms")
        print(f"  Max: {metrics['max_ms']:.2f}ms")
        print(f"  Avg: {metrics['avg_ms']:.2f}ms")
        print(f"  P50: {metrics['p50_ms']:.2f}ms")

    def test_timed_decorator_sync(self):
        """测试同步函数计时装饰器"""
        print("\n--- Testing timed decorator (sync) ---")
        
        @log.timed("sync_function")
        def slow_function():
            time.sleep(0.05)
            return "result"
        
        result = slow_function()
        self.assertEqual(result, "result")
        
        metrics = log.get_metrics("sync_function")
        self.assertEqual(metrics["count"], 1)
        self.assertGreater(metrics["avg_ms"], 40)
        
        print(f"  Sync function timing: {metrics['avg_ms']:.2f}ms")

    def test_timed_decorator_async(self):
        """测试异步函数计时装饰器"""
        print("\n--- Testing timed decorator (async) ---")
        
        @log.timed("async_function")
        async def async_slow_function():
            await asyncio.sleep(0.05)
            return "async_result"
        
        result = asyncio.run(async_slow_function())
        self.assertEqual(result, "async_result")
        
        metrics = log.get_metrics("async_function")
        self.assertEqual(metrics["count"], 1)
        self.assertGreater(metrics["avg_ms"], 40)
        
        print(f"  Async function timing: {metrics['avg_ms']:.2f}ms")

    def test_get_all_metrics(self):
        """测试获取所有性能指标"""
        print("\n--- Testing get all metrics ---")
        
        # 创建多个操作的指标
        with log.timer("operation_a"):
            time.sleep(0.01)
        
        with log.timer("operation_b"):
            time.sleep(0.02)
        
        all_metrics = log.get_metrics()
        self.assertIn("operation_a", all_metrics)
        self.assertIn("operation_b", all_metrics)
        
        print(f"  Operations tracked: {list(all_metrics.keys())}")

    def test_clear_metrics(self):
        """测试清除性能指标"""
        print("\n--- Testing clear metrics ---")
        
        with log.timer("temp_operation"):
            pass
        
        metrics_before = log.get_metrics("temp_operation")
        self.assertEqual(metrics_before["count"], 1)
        
        log.clear_metrics("temp_operation")
        
        metrics_after = log.get_metrics("temp_operation")
        self.assertEqual(metrics_after["count"], 0)
        
        print("  Clear specific metric: OK")
        
        # 测试清除所有
        with log.timer("another_op"):
            pass
        
        log.clear_metrics()
        all_metrics = log.get_metrics()
        self.assertEqual(len(all_metrics), 0)
        
        print("  Clear all metrics: OK")

    def test_timer_with_extra_fields(self):
        """测试带额外字段的计时器"""
        print("\n--- Testing timer with extra fields ---")
        
        with log.timer("api_call", tag="EXTERNAL_API", 
                       request_id="req_456", 
                       endpoint="/v1/chat/completions"):
            time.sleep(0.01)
        
        metrics = log.get_metrics("api_call")
        self.assertEqual(metrics["count"], 1)
        
        print(f"  Timer with extra fields: {metrics['avg_ms']:.2f}ms")


class TestMetricsStatistics(unittest.TestCase):
    """测试性能指标统计计算"""

    def setUp(self):
        log.clear_metrics()

    def test_percentile_calculation(self):
        """测试百分位数计算（需要足够的样本）"""
        print("\n--- Testing percentile calculation ---")
        
        # 创建 100 个样本
        for i in range(100):
            with log.timer("percentile_test"):
                time.sleep(0.001)  # 1ms
        
        metrics = log.get_metrics("percentile_test")
        self.assertEqual(metrics["count"], 100)
        self.assertIsNotNone(metrics["p50_ms"])
        self.assertIsNotNone(metrics["p95_ms"])
        self.assertIsNotNone(metrics["p99_ms"])
        
        print(f"  Count: {metrics['count']}")
        print(f"  P50: {metrics['p50_ms']:.2f}ms")
        print(f"  P95: {metrics['p95_ms']:.2f}ms")
        print(f"  P99: {metrics['p99_ms']:.2f}ms")


if __name__ == '__main__':
    print("=" * 60)
    print("Testing P2 Fixes: Structured Logging & Performance Monitoring")
    print("=" * 60)
    unittest.main(verbosity=2)

