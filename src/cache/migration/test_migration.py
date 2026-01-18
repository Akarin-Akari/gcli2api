"""
Migration Adapter Tests - 迁移适配层测试

验证适配层的功能正确性和接口兼容性。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-09
"""

import sys
import os
import tempfile
import time

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_feature_flags():
    """测试特性开关"""
    print("\n=== 测试 Feature Flags ===")

    from migration.feature_flags import (
        MigrationPhase,
        MigrationFeatureFlags,
        reset_feature_flags,
        get_feature_flags,
        set_migration_phase,
        get_migration_phase,
    )

    # 重置全局实例
    reset_feature_flags()

    # 测试默认阶段
    flags = get_feature_flags()
    assert flags.phase == MigrationPhase.LEGACY_ONLY, f"默认阶段应为 LEGACY_ONLY，实际为 {flags.phase}"
    print(f"  [OK] 默认阶段: {flags.phase.name}")

    # 测试 Phase 1 行为
    assert flags.should_write_to_legacy == True
    assert flags.should_write_to_new == False
    assert flags.should_read_from_legacy == True
    assert flags.should_read_from_new == False
    print("  [OK] Phase 1 行为正确")

    # 切换到 Phase 2
    set_migration_phase(MigrationPhase.DUAL_WRITE)
    assert get_migration_phase() == MigrationPhase.DUAL_WRITE
    assert flags.should_write_to_legacy == True
    assert flags.should_write_to_new == True
    assert flags.is_dual_write_enabled == True
    print("  [OK] Phase 2 (DUAL_WRITE) 行为正确")

    # 切换到 Phase 3
    set_migration_phase(MigrationPhase.NEW_PREFERRED)
    assert flags.prefer_new_on_read == True
    print("  [OK] Phase 3 (NEW_PREFERRED) 行为正确")

    # 切换到 Phase 4
    set_migration_phase(MigrationPhase.NEW_ONLY)
    assert flags.should_write_to_legacy == False
    assert flags.should_write_to_new == True
    assert flags.should_read_from_legacy == False
    assert flags.should_read_from_new == True
    print("  [OK] Phase 4 (NEW_ONLY) 行为正确")

    # 测试手动覆盖
    flags.override_write_to_legacy(True)
    assert flags.should_write_to_legacy == True
    flags.clear_overrides()
    assert flags.should_write_to_legacy == False  # 回到 Phase 4 默认行为
    print("  [OK] 手动覆盖功能正确")

    # 测试状态获取
    status = flags.get_status()
    assert "phase" in status
    assert "write" in status
    assert "read" in status
    print("  [OK] 状态获取正确")

    print("=== Feature Flags 测试通过 ===")


def test_migration_config():
    """测试迁移配置"""
    print("\n=== 测试 Migration Config ===")

    from migration.migration_config import (
        MigrationConfig,
        reset_migration_config,
        get_migration_config,
    )

    # 重置全局实例
    reset_migration_config()

    # 测试默认配置
    config = get_migration_config()
    assert config.legacy_max_size == 10000
    assert config.legacy_ttl_seconds == 3600.0
    print(f"  [OK] 默认旧缓存配置: max_size={config.legacy_max_size}")

    assert config.new_l1_max_size == 5000
    assert config.new_l1_ttl_seconds == 1800.0
    print(f"  [OK] 默认新缓存 L1 配置: max_size={config.new_l1_max_size}")

    # 测试配置获取
    legacy_config = config.get_legacy_config()
    assert "max_size" in legacy_config
    assert "ttl_seconds" in legacy_config
    print("  [OK] 旧缓存配置获取正确")

    new_l1_config = config.get_new_l1_config()
    assert "max_size" in new_l1_config
    print("  [OK] 新缓存 L1 配置获取正确")

    # 测试转换为字典
    config_dict = config.to_dict()
    assert "legacy" in config_dict
    assert "new_l1" in config_dict
    assert "new_l2" in config_dict
    assert "migration" in config_dict
    print("  [OK] 配置转换为字典正确")

    print("=== Migration Config 测试通过 ===")


def test_dual_write_strategy():
    """测试双写策略"""
    print("\n=== 测试 Dual Write Strategy ===")

    from migration.feature_flags import (
        MigrationPhase,
        reset_feature_flags,
        get_feature_flags,
        set_migration_phase,
    )
    from migration.migration_config import reset_migration_config, get_migration_config
    from migration.dual_write_strategy import (
        WriteResult,
        reset_dual_write_strategy,
        get_dual_write_strategy,
    )

    # 重置所有全局实例
    reset_feature_flags()
    reset_migration_config()
    reset_dual_write_strategy()

    flags = get_feature_flags()
    config = get_migration_config()
    strategy = get_dual_write_strategy(flags, config)

    # 测试 Phase 1：只写旧缓存
    set_migration_phase(MigrationPhase.LEGACY_ONLY)

    legacy_writes = []
    new_writes = []

    def legacy_writer(text, sig, model):
        legacy_writes.append((text, sig))
        return True

    def new_writer(text, sig, model):
        new_writes.append((text, sig))
        return True

    result = strategy.write(
        thinking_text="test thinking",
        signature="test_signature_" + "x" * 50,
        model=None,
        legacy_writer=legacy_writer,
        new_writer=new_writer,
    )

    assert result == WriteResult.LEGACY_ONLY
    assert len(legacy_writes) == 1
    assert len(new_writes) == 0
    print("  [OK] Phase 1 只写旧缓存")

    # 测试 Phase 2：双写
    legacy_writes.clear()
    new_writes.clear()
    set_migration_phase(MigrationPhase.DUAL_WRITE)

    # 禁用异步写入以便测试
    flags.async_write_to_new = False

    result = strategy.write(
        thinking_text="test thinking 2",
        signature="test_signature_" + "y" * 50,
        model=None,
        legacy_writer=legacy_writer,
        new_writer=new_writer,
    )

    assert result == WriteResult.SUCCESS
    assert len(legacy_writes) == 1
    assert len(new_writes) == 1
    print("  [OK] Phase 2 双写成功")

    # 测试统计
    stats = strategy.get_stats()
    assert stats["total_writes"] >= 2
    assert stats["legacy_writes"] >= 2
    print(f"  [OK] 统计信息: total={stats['total_writes']}, legacy={stats['legacy_writes']}")

    print("=== Dual Write Strategy 测试通过 ===")


def test_read_strategy():
    """测试读取策略"""
    print("\n=== 测试 Read Strategy ===")

    from migration.feature_flags import (
        MigrationPhase,
        reset_feature_flags,
        get_feature_flags,
        set_migration_phase,
    )
    from migration.migration_config import reset_migration_config, get_migration_config
    from migration.read_strategy import (
        ReadSource,
        reset_read_strategy,
        get_read_strategy,
    )

    # 重置所有全局实例
    reset_feature_flags()
    reset_migration_config()
    reset_read_strategy()

    flags = get_feature_flags()
    config = get_migration_config()
    strategy = get_read_strategy(flags, config)

    # 模拟缓存数据
    legacy_data = {"hash1": "sig_legacy"}
    new_data = {"hash1": "sig_new", "hash2": "sig_new_only"}

    def legacy_reader(text):
        return legacy_data.get(text)

    def new_reader(text, ns):
        return new_data.get(text)

    # 测试 Phase 1：只读旧缓存
    set_migration_phase(MigrationPhase.LEGACY_ONLY)

    result, source = strategy.read(
        thinking_text="hash1",
        legacy_reader=legacy_reader,
        new_reader=new_reader,
    )

    assert result == "sig_legacy"
    assert source == ReadSource.LEGACY
    print("  [OK] Phase 1 只读旧缓存")

    # 测试 Phase 2：优先旧缓存
    set_migration_phase(MigrationPhase.DUAL_WRITE)

    result, source = strategy.read(
        thinking_text="hash1",
        legacy_reader=legacy_reader,
        new_reader=new_reader,
    )

    assert result == "sig_legacy"
    assert source == ReadSource.LEGACY
    print("  [OK] Phase 2 优先旧缓存")

    # 测试 Phase 2：旧缓存未命中，fallback 到新缓存
    result, source = strategy.read(
        thinking_text="hash2",
        legacy_reader=legacy_reader,
        new_reader=new_reader,
    )

    assert result == "sig_new_only"
    assert source == ReadSource.NEW
    print("  [OK] Phase 2 Fallback 到新缓存")

    # 测试 Phase 3：优先新缓存
    set_migration_phase(MigrationPhase.NEW_PREFERRED)

    result, source = strategy.read(
        thinking_text="hash1",
        legacy_reader=legacy_reader,
        new_reader=new_reader,
    )

    assert result == "sig_new"
    assert source == ReadSource.NEW
    print("  [OK] Phase 3 优先新缓存")

    # 测试统计
    stats = strategy.get_stats()
    assert stats["total_reads"] >= 4
    print(f"  [OK] 统计信息: total={stats['total_reads']}")

    print("=== Read Strategy 测试通过 ===")


def test_legacy_adapter():
    """测试旧接口适配器"""
    print("\n=== 测试 Legacy Adapter ===")

    from migration.feature_flags import (
        MigrationPhase,
        reset_feature_flags,
        set_migration_phase,
    )
    from migration.migration_config import reset_migration_config
    from migration.dual_write_strategy import reset_dual_write_strategy
    from migration.read_strategy import reset_read_strategy
    from migration.legacy_adapter import (
        LegacySignatureCacheAdapter,
        reset_legacy_adapter,
    )

    # 重置所有全局实例
    reset_feature_flags()
    reset_migration_config()
    reset_dual_write_strategy()
    reset_read_strategy()
    reset_legacy_adapter()

    # 创建适配器
    adapter = LegacySignatureCacheAdapter(
        max_size=100,
        ttl_seconds=60,
    )

    # 确保在 Phase 1（只用旧缓存）
    set_migration_phase(MigrationPhase.LEGACY_ONLY)

    # 测试基本写入
    test_thinking = "This is a test thinking block with enough content to be valid."
    test_signature = "EqQBCg" + "A" * 50  # 有效的 signature 格式

    result = adapter.set(
        thinking_text=test_thinking,
        signature=test_signature,
        model="claude-opus-4-5"
    )
    assert result == True, "写入应该成功"
    print("  [OK] 基本写入成功")

    # 测试基本读取
    cached = adapter.get(test_thinking)
    assert cached == test_signature, f"读取结果应为 {test_signature}，实际为 {cached}"
    print("  [OK] 基本读取成功")

    # 测试缓存大小
    assert adapter.size == 1
    assert len(adapter) == 1
    print(f"  [OK] 缓存大小: {adapter.size}")

    # 测试统计
    stats = adapter.get_stats()
    assert stats["hits"] >= 1
    assert stats["writes"] >= 1
    assert "migration" in stats
    print(f"  [OK] 统计信息: hits={stats['hits']}, writes={stats['writes']}")

    # 测试 get_last_signature
    last_sig = adapter.get_last_signature()
    assert last_sig == test_signature
    print("  [OK] get_last_signature 成功")

    # 测试 get_last_signature_with_text
    result = adapter.get_last_signature_with_text()
    assert result is not None
    assert result[0] == test_signature
    assert result[1] == test_thinking
    print("  [OK] get_last_signature_with_text 成功")

    # 测试无效 signature 被拒绝
    result = adapter.set(
        thinking_text="another thinking",
        signature="short",  # 太短，无效
    )
    assert result == False
    print("  [OK] 无效 signature 被正确拒绝")

    # 测试空值被拒绝
    result = adapter.set(thinking_text="", signature=test_signature)
    assert result == False
    result = adapter.set(thinking_text=test_thinking, signature="")
    assert result == False
    print("  [OK] 空值被正确拒绝")

    # 测试 invalidate
    result = adapter.invalidate(test_thinking)
    assert result == True
    cached = adapter.get(test_thinking)
    assert cached is None
    print("  [OK] invalidate 成功")

    # 测试 clear
    adapter.set(test_thinking, test_signature)
    count = adapter.clear()
    assert count >= 1
    assert adapter.size == 0
    print("  [OK] clear 成功")

    # 测试迁移阶段切换
    adapter.set_migration_phase(MigrationPhase.DUAL_WRITE)
    assert adapter.get_migration_phase() == MigrationPhase.DUAL_WRITE
    print("  [OK] 迁移阶段切换成功")

    # 测试迁移状态
    status = adapter.get_migration_status()
    assert "phase" in status
    assert "flags" in status
    print("  [OK] 迁移状态获取成功")

    # 测试 __repr__
    repr_str = repr(adapter)
    assert "LegacySignatureCacheAdapter" in repr_str
    print(f"  [OK] __repr__: {repr_str}")

    # 关闭适配器
    adapter.shutdown()
    print("  [OK] shutdown 成功")

    print("=== Legacy Adapter 测试通过 ===")


def test_api_compatibility():
    """测试与原 SignatureCache 的 API 兼容性"""
    print("\n=== 测试 API 兼容性 ===")

    from migration.legacy_adapter import LegacySignatureCacheAdapter

    adapter = LegacySignatureCacheAdapter()

    # 验证所有必需的方法存在
    required_methods = [
        "set",
        "get",
        "invalidate",
        "clear",
        "cleanup_expired",
        "get_stats",
        "get_last_signature",
        "get_last_signature_with_text",
    ]

    for method in required_methods:
        assert hasattr(adapter, method), f"缺少方法: {method}"
        assert callable(getattr(adapter, method)), f"方法不可调用: {method}"

    print(f"  [OK] 所有 {len(required_methods)} 个必需方法都存在")

    # 验证属性
    assert hasattr(adapter, "size")
    assert hasattr(adapter, "__len__")
    assert hasattr(adapter, "__repr__")
    print("  [OK] 所有必需属性都存在")

    # 验证方法签名兼容性
    # set(thinking_text, signature, model=None) -> bool
    result = adapter.set("test", "x" * 60)
    assert isinstance(result, bool)

    # get(thinking_text) -> Optional[str]
    result = adapter.get("test")
    assert result is None or isinstance(result, str)

    # invalidate(thinking_text) -> bool
    result = adapter.invalidate("test")
    assert isinstance(result, bool)

    # clear() -> int
    result = adapter.clear()
    assert isinstance(result, int)

    # cleanup_expired() -> int
    result = adapter.cleanup_expired()
    assert isinstance(result, int)

    # get_stats() -> Dict
    result = adapter.get_stats()
    assert isinstance(result, dict)

    print("  [OK] 所有方法签名兼容")

    adapter.shutdown()
    print("=== API 兼容性测试通过 ===")


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("Migration Adapter Tests")
    print("=" * 60)

    try:
        test_feature_flags()
        test_migration_config()
        test_dual_write_strategy()
        test_read_strategy()
        test_legacy_adapter()
        test_api_compatibility()

        print("\n" + "=" * 60)
        print("All tests passed! (^_^)")
        print("=" * 60)
        return True

    except AssertionError as e:
        print(f"\n[FAILED] 断言失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    except Exception as e:
        print(f"\n[ERROR] 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
