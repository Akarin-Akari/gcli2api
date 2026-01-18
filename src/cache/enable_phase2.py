"""
Phase 2: DUAL_WRITE 启用脚本

快速启用 Phase 2 双写模式的便捷脚本。

Usage:
    # 启用 Phase 2
    python enable_phase2.py
    
    # 启用 Phase 2 并验证
    python enable_phase2.py --verify

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-10
"""

import os
import sys
import argparse

# 添加项目路径
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
project_dir = os.path.dirname(src_dir)
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)


def enable_phase2(verify: bool = False):
    """
    启用 Phase 2 双写模式
    
    Args:
        verify: 是否进行验证测试
    """
    print("=" * 60)
    print("🚀 启用 Phase 2: DUAL_WRITE 双写模式")
    print("=" * 60)
    
    # 导入模块
    from signature_cache import (
        enable_migration_mode,
        set_migration_phase,
        get_migration_status,
        is_migration_mode_enabled,
    )
    
    # 1. 启用迁移模式
    print("\n步骤 1: 启用迁移模式...")
    enable_migration_mode()
    print(f"   ✅ 迁移模式已启用: {is_migration_mode_enabled()}")
    
    # 2. 设置为 DUAL_WRITE 阶段
    print("\n步骤 2: 设置为 DUAL_WRITE 阶段...")
    set_migration_phase("DUAL_WRITE")
    print("   ✅ 阶段已设置为 DUAL_WRITE")
    
    # 3. 验证状态
    print("\n步骤 3: 验证迁移状态...")
    status = get_migration_status()
    print(f"   - migration_mode_enabled: {status.get('migration_mode_enabled')}")
    
    if 'facade_status' in status:
        facade = status['facade_status']
        print(f"   - migration_adapter_enabled: {facade.get('migration_adapter_enabled')}")
        if 'migration' in facade:
            migration = facade['migration']
            print(f"   - phase: {migration.get('phase')}")
            if 'flags' in migration:
                flags = migration['flags']
                print(f"   - write_to_legacy: {flags.get('write', {}).get('to_legacy')}")
                print(f"   - write_to_new: {flags.get('write', {}).get('to_new')}")
                print(f"   - dual_write: {flags.get('write', {}).get('dual_write')}")
    
    print("\n" + "=" * 60)
    print("✅ Phase 2 双写模式已启用！")
    print("=" * 60)
    
    # 可选验证
    if verify:
        print("\n🔍 执行验证测试...")
        
        from cache.migration import get_legacy_adapter
        
        adapter = get_legacy_adapter()
        
        # 写入测试
        test_thinking = "Phase 2 验证测试 - " + "x" * 100
        test_signature = "EqQBCgIYAhIkMDI0NzZhNTgtZDQxMi00YWI5LWIwNGQtZmQ5OWM4YjE3" + "B" * 100
        
        success = adapter.set(test_thinking, test_signature, model="test")
        print(f"   - 写入测试: {'✅ 成功' if success else '❌ 失败'}")
        
        # 读取测试
        cached = adapter.get(test_thinking)
        read_ok = cached == test_signature
        print(f"   - 读取测试: {'✅ 成功' if read_ok else '❌ 失败'}")
        
        # 统计
        stats = adapter.get_stats()
        print(f"   - 缓存大小: {stats['cache_size']}")
        print(f"   - 命中率: {stats['hit_rate']}")
        
        if success and read_ok:
            print("\n✅ 验证测试全部通过！")
        else:
            print("\n❌ 验证测试失败！")
            return False
    
    # 输出使用说明
    print("\n📝 使用说明:")
    print("-" * 60)
    print("Phase 2 双写模式特点:")
    print("  - 写入: 同时写入旧缓存和新缓存")
    print("  - 读取: 优先从旧缓存读取")
    print("  - 风险: 低（旧缓存仍然是主要来源）")
    print("")
    print("监控建议:")
    print("  - 观察 dual_write_stats 中的成功率")
    print("  - 检查新缓存的写入是否正常")
    print("  - 确认没有性能下降")
    print("")
    print("下一步:")
    print("  - 稳定运行后，可升级到 Phase 3 (NEW_PREFERRED)")
    print("  - 使用: set_migration_phase('NEW_PREFERRED')")
    print("-" * 60)
    
    return True


def main():
    parser = argparse.ArgumentParser(description="启用 Phase 2 双写模式")
    parser.add_argument(
        "--verify", "-v",
        action="store_true",
        help="启用后进行验证测试"
    )
    args = parser.parse_args()
    
    try:
        success = enable_phase2(verify=args.verify)
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ 启用失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())



