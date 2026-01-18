#!/usr/bin/env python3
"""
测试签名缓存统计功能

验证三层缓存的统计和监控功能是否正常工作。

Author: Claude Sonnet 4.5
Date: 2026-01-17
"""

import sys
import json
from pathlib import Path

# 添加 src 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from signature_cache import (
    get_signature_cache,
    cache_signature,
    get_cached_signature,
    cache_tool_signature,
    get_tool_signature,
    cache_session_signature,
    get_session_signature,
    get_cache_stats,
    reset_cache_stats,
)


def print_stats(title: str):
    """打印统计信息"""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    stats = get_cache_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))


def test_layer2_cache():
    """测试 Layer 2 主缓存统计"""
    print("\n[TEST] Layer 2 (Thinking 内容哈希缓存)")

    thinking1 = "Let me analyze this problem step by step..."
    thinking2 = "I need to consider multiple approaches..."
    sig1 = "EqQBCg" + "A" * 100  # 模拟有效的 signature
    sig2 = "EqQBCg" + "B" * 100

    # 缓存写入
    cache_signature(thinking1, sig1)
    cache_signature(thinking2, sig2)

    # 缓存命中
    result1 = get_cached_signature(thinking1)
    assert result1 == sig1, "Layer 2 缓存命中失败"

    # 缓存命中
    result2 = get_cached_signature(thinking2)
    assert result2 == sig2, "Layer 2 缓存命中失败"

    # 缓存未命中
    result3 = get_cached_signature("Non-existent thinking...")
    assert result3 is None, "Layer 2 缓存未命中检测失败"

    print("[PASS] Layer 2 缓存测试通过")


def test_layer1_cache():
    """测试 Layer 1 工具ID缓存统计"""
    print("\n[TEST] Layer 1 (工具ID缓存)")

    tool_id1 = "tool_call_abc123"
    tool_id2 = "tool_call_def456"
    sig1 = "EqQBCg" + "C" * 100
    sig2 = "EqQBCg" + "D" * 100

    # 缓存写入
    cache_tool_signature(tool_id1, sig1)
    cache_tool_signature(tool_id2, sig2)

    # 缓存命中
    result1 = get_tool_signature(tool_id1)
    assert result1 == sig1, "Layer 1 缓存命中失败"

    # 缓存命中
    result2 = get_tool_signature(tool_id2)
    assert result2 == sig2, "Layer 1 缓存命中失败"

    # 缓存未命中
    result3 = get_tool_signature("non_existent_tool")
    assert result3 is None, "Layer 1 缓存未命中检测失败"

    print("[PASS] Layer 1 缓存测试通过")


def test_layer3_cache():
    """测试 Layer 3 Session 级别缓存统计"""
    print("\n[TEST] Layer 3 (Session 级别缓存)")

    session_id1 = "session_abc123"
    session_id2 = "session_def456"
    sig1 = "EqQBCg" + "E" * 100
    sig2 = "EqQBCg" + "F" * 100

    # 缓存写入
    cache_session_signature(session_id1, sig1, "thinking text 1")
    cache_session_signature(session_id2, sig2, "thinking text 2")

    # 缓存命中
    result1 = get_session_signature(session_id1)
    assert result1 == sig1, "Layer 3 缓存命中失败"

    # 缓存命中
    result2 = get_session_signature(session_id2)
    assert result2 == sig2, "Layer 3 缓存命中失败"

    # 缓存未命中
    result3 = get_session_signature("non_existent_session")
    assert result3 is None, "Layer 3 缓存未命中检测失败"

    print("[PASS] Layer 3 缓存测试通过")


def test_stats_calculation():
    """测试统计信息计算"""
    print("\n[TEST] 统计信息计算")

    stats = get_cache_stats()
    layer_stats = stats.get("layer_stats", {})

    # 验证统计字段存在
    assert "tool_cache_hits" in layer_stats, "缺少 tool_cache_hits 字段"
    assert "tool_cache_misses" in layer_stats, "缺少 tool_cache_misses 字段"
    assert "cache_hits" in layer_stats, "缺少 cache_hits 字段"
    assert "cache_misses" in layer_stats, "缺少 cache_misses 字段"
    assert "session_cache_hits" in layer_stats, "缺少 session_cache_hits 字段"
    assert "session_cache_misses" in layer_stats, "缺少 session_cache_misses 字段"

    # 验证命中率字段存在
    assert "tool_cache_hit_rate" in layer_stats, "缺少 tool_cache_hit_rate 字段"
    assert "cache_hit_rate" in layer_stats, "缺少 cache_hit_rate 字段"
    assert "session_cache_hit_rate" in layer_stats, "缺少 session_cache_hit_rate 字段"

    # 验证缓存大小字段存在
    assert "tool_cache_size" in layer_stats, "缺少 tool_cache_size 字段"
    assert "session_cache_size" in layer_stats, "缺少 session_cache_size 字段"

    # 验证命中率计算正确（应该大于 0，因为前面有命中）
    assert layer_stats["tool_cache_hit_rate"] > 0, "Layer 1 命中率计算错误"
    assert layer_stats["cache_hit_rate"] > 0, "Layer 2 命中率计算错误"
    assert layer_stats["session_cache_hit_rate"] > 0, "Layer 3 命中率计算错误"

    print("[PASS] 统计信息计算测试通过")


def test_reset_stats():
    """测试统计重置功能"""
    print("\n[TEST] 统计重置功能")

    # 重置统计
    reset_cache_stats()

    stats = get_cache_stats()
    layer_stats = stats.get("layer_stats", {})

    # 验证所有计数器已重置为 0
    assert layer_stats["tool_cache_hits"] == 0, "tool_cache_hits 未重置"
    assert layer_stats["tool_cache_misses"] == 0, "tool_cache_misses 未重置"
    assert layer_stats["cache_hits"] == 0, "cache_hits 未重置"
    assert layer_stats["cache_misses"] == 0, "cache_misses 未重置"
    assert layer_stats["session_cache_hits"] == 0, "session_cache_hits 未重置"
    assert layer_stats["session_cache_misses"] == 0, "session_cache_misses 未重置"

    print("[PASS] 统计重置测试通过")


def main():
    """主测试函数"""
    print("[START] 签名缓存统计功能测试")
    print("="*60)

    try:
        # 重置统计，确保从干净状态开始
        reset_cache_stats()
        print_stats("初始状态")

        # 测试各层缓存
        test_layer2_cache()
        print_stats("Layer 2 测试后")

        test_layer1_cache()
        print_stats("Layer 1 测试后")

        test_layer3_cache()
        print_stats("Layer 3 测试后")

        # 测试统计计算
        test_stats_calculation()

        # 测试统计重置
        test_reset_stats()
        print_stats("统计重置后")

        print("\n" + "="*60)
        print("[SUCCESS] 所有测试通过!")
        print("="*60)

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}")
        print_stats("失败时的状态")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
