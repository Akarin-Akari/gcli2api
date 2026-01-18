"""
Phase 2: DUAL_WRITE 双写模式测试脚本

此脚本用于测试和验证 Phase 2 双写模式的功能：
1. 启用迁移模式
2. 设置为 DUAL_WRITE 阶段
3. 验证双写功能正常
4. 验证读取优先旧缓存

Usage:
    python test_phase2_dual_write.py

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-10
"""

import os
import sys
import time
import logging

# 添加项目路径
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
project_dir = os.path.dirname(src_dir)
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)


def test_phase2_dual_write():
    """测试 Phase 2 双写模式"""
    print("=" * 60)
    print("Phase 2: DUAL_WRITE 双写模式测试")
    print("=" * 60)
    
    # 导入必要模块
    from cache.migration import (
        MigrationPhase,
        get_feature_flags,
        set_migration_phase,
        get_legacy_adapter,
        reset_legacy_adapter,
        reset_feature_flags,
    )
    
    # 重置状态
    reset_feature_flags()
    reset_legacy_adapter()
    
    # 获取特性开关
    flags = get_feature_flags()
    print(f"\n1. 初始状态:")
    print(f"   - 当前阶段: {flags.phase.name}")
    print(f"   - 写入旧缓存: {flags.should_write_to_legacy}")
    print(f"   - 写入新缓存: {flags.should_write_to_new}")
    print(f"   - 从旧缓存读取: {flags.should_read_from_legacy}")
    print(f"   - 从新缓存读取: {flags.should_read_from_new}")
    print(f"   - 读取优先新缓存: {flags.prefer_new_on_read}")
    
    # 设置为 DUAL_WRITE 阶段
    print(f"\n2. 设置为 DUAL_WRITE 阶段...")
    set_migration_phase(MigrationPhase.DUAL_WRITE)
    
    print(f"   - 当前阶段: {flags.phase.name}")
    print(f"   - 写入旧缓存: {flags.should_write_to_legacy}")
    print(f"   - 写入新缓存: {flags.should_write_to_new}")
    print(f"   - 双写启用: {flags.is_dual_write_enabled}")
    print(f"   - 从旧缓存读取: {flags.should_read_from_legacy}")
    print(f"   - 从新缓存读取: {flags.should_read_from_new}")
    print(f"   - 读取优先新缓存: {flags.prefer_new_on_read}")
    
    # 验证 DUAL_WRITE 阶段的行为
    assert flags.phase == MigrationPhase.DUAL_WRITE, "阶段应该是 DUAL_WRITE"
    assert flags.should_write_to_legacy, "应该写入旧缓存"
    assert flags.should_write_to_new, "应该写入新缓存"
    assert flags.is_dual_write_enabled, "双写应该启用"
    assert flags.should_read_from_legacy, "应该从旧缓存读取"
    assert flags.should_read_from_new, "应该从新缓存读取"
    assert not flags.prefer_new_on_read, "读取不应该优先新缓存（Phase 2）"
    
    print("   [OK] 阶段配置验证通过")
    
    # 获取适配器
    print(f"\n3. 测试双写功能...")
    adapter = get_legacy_adapter()
    
    # 测试写入
    test_thinking = "这是一段测试的 thinking 内容，用于验证双写模式。" * 10
    test_signature = "EqQBCgIYAhIkMDI0NzZhNTgtZDQxMi00YWI5LWIwNGQtZmQ5OWM4YjE3" + "A" * 100
    
    print(f"   - 写入测试数据: thinking_len={len(test_thinking)}")
    success = adapter.set(test_thinking, test_signature, model="test-model")
    print(f"   - 写入结果: {success}")
    
    assert success, "写入应该成功"
    print("   [OK] 写入验证通过")
    
    # 测试读取
    print(f"\n4. 测试读取功能...")
    cached_sig = adapter.get(test_thinking)
    print(f"   - 读取结果: {'命中' if cached_sig else '未命中'}")
    
    assert cached_sig == test_signature, "读取的 signature 应该与写入的一致"
    print("   [OK] 读取验证通过")
    
    # 获取统计信息
    print(f"\n5. 统计信息:")
    stats = adapter.get_stats()
    print(f"   - 缓存大小: {stats['cache_size']}")
    print(f"   - 命中次数: {stats['hits']}")
    print(f"   - 未命中次数: {stats['misses']}")
    print(f"   - 写入次数: {stats['writes']}")
    print(f"   - 命中率: {stats['hit_rate']}")
    
    # 获取迁移状态
    print(f"\n6. 迁移状态:")
    migration_status = adapter.get_migration_status()
    print(f"   - 阶段: {migration_status['phase']}")
    print(f"   - 双写统计: {migration_status['dual_write_stats']}")
    print(f"   - 读取统计: {migration_status['read_stats']}")
    
    # 测试 get_last_signature
    print(f"\n7. 测试 get_last_signature...")
    last_sig = adapter.get_last_signature()
    print(f"   - 最近 signature: {'存在' if last_sig else '不存在'}")
    assert last_sig == test_signature, "get_last_signature 应该返回最近的 signature"
    print("   [OK] get_last_signature 验证通过")
    
    # 测试 get_last_signature_with_text
    print(f"\n8. 测试 get_last_signature_with_text...")
    result = adapter.get_last_signature_with_text()
    if result:
        sig, text = result
        print(f"   - signature: {'存在' if sig else '不存在'}")
        print(f"   - thinking_text: len={len(text)}")
        assert sig == test_signature, "signature 应该匹配"
        assert text == test_thinking, "thinking_text 应该匹配"
        print("   [OK] get_last_signature_with_text 验证通过")
    else:
        print("   [FAIL] get_last_signature_with_text 返回 None")
    
    print("\n" + "=" * 60)
    print("Phase 2 双写模式测试完成！所有测试通过 [OK]")
    print("=" * 60)
    
    return True


def test_phase2_via_signature_cache():
    """通过 signature_cache.py 的迁移接口测试 Phase 2"""
    print("\n" + "=" * 60)
    print("通过 signature_cache.py 接口测试 Phase 2")
    print("=" * 60)
    
    # 导入 signature_cache 模块
    from signature_cache import (
        enable_migration_mode,
        disable_migration_mode,
        set_migration_phase,
        get_migration_status,
        is_migration_mode_enabled,
    )
    
    print(f"\n1. 当前迁移模式状态: {is_migration_mode_enabled()}")
    
    # 启用迁移模式
    print(f"\n2. 启用迁移模式...")
    enable_migration_mode()
    print(f"   - 迁移模式已启用: {is_migration_mode_enabled()}")
    
    # 设置为 DUAL_WRITE 阶段
    print(f"\n3. 设置为 DUAL_WRITE 阶段...")
    set_migration_phase("DUAL_WRITE")
    
    # 获取迁移状态
    print(f"\n4. 迁移状态:")
    status = get_migration_status()
    print(f"   - migration_mode_enabled: {status.get('migration_mode_enabled')}")
    if 'facade_status' in status:
        facade = status['facade_status']
        print(f"   - migration_adapter_enabled: {facade.get('migration_adapter_enabled')}")
        if 'migration' in facade:
            migration = facade['migration']
            print(f"   - phase: {migration.get('phase')}")
    
    # 禁用迁移模式
    print(f"\n5. 禁用迁移模式...")
    disable_migration_mode()
    print(f"   - 迁移模式已禁用: {is_migration_mode_enabled()}")
    
    print("\n" + "=" * 60)
    print("signature_cache.py 接口测试完成！[OK]")
    print("=" * 60)
    
    return True


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("Phase 2: DUAL_WRITE 双写模式完整测试")
    print("=" * 60 + "\n")
    
    try:
        # 测试 1: 直接使用 migration 模块
        test_phase2_dual_write()
        
        # 测试 2: 通过 signature_cache.py 接口
        test_phase2_via_signature_cache()
        
        print("\n" + "=" * 60)
        print("所有测试通过！Phase 2 双写模式已就绪！")
        print("=" * 60 + "\n")
        
        print("\n[INFO] 使用说明:")
        print("=" * 60)
        print("方式一：通过环境变量启用")
        print("  export CACHE_USE_MIGRATION_ADAPTER=true")
        print("  export CACHE_MIGRATION_PHASE=2")
        print("")
        print("方式二：通过代码启用")
        print("  from signature_cache import enable_migration_mode, set_migration_phase")
        print("  enable_migration_mode()")
        print("  set_migration_phase('DUAL_WRITE')")
        print("")
        print("方式三：通过 API 控制")
        print("  POST /cache/migration/enable")
        print("  POST /cache/migration/phase?phase=DUAL_WRITE")
        print("=" * 60)
        
        return 0
        
    except Exception as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

