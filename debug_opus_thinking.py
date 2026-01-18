#!/usr/bin/env python3
"""
调试 claude-opus-4-5-thinking 模型凭证选择问题
"""

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone


async def debug_opus_thinking():
    """调试 claude-opus-4-5-thinking 凭证选择问题"""

    print("[DEBUG] 开始调试 claude-opus-4-5-thinking 凭证选择问题...")

    # 1. 检查数据库中的凭证状态
    db_path = "gcli2api.db"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        print(f"\n📊 数据库路径: {db_path}")

        # 检查 antigravity_credentials 表结构
        cursor.execute("PRAGMA table_info(antigravity_credentials)")
        columns = cursor.fetchall()
        print(f"📝 表结构: {[col[1] for col in columns]}")

        # 检查所有 antigravity 凭证状态
        cursor.execute("""
            SELECT filename, disabled, model_cooldowns, last_success, error_codes
            FROM antigravity_credentials
        """)

        credentials = cursor.fetchall()
        print(f"\n📋 共找到 {len(credentials)} 个 Antigravity 凭证:")

        current_time = time.time()
        available_count = 0
        opus_thinking_available_count = 0

        for filename, disabled, model_cooldowns_json, last_success, error_codes_json in credentials:
            print(f"\n📁 凭证: {filename}")
            print(f"   ⏸️  禁用状态: {'是' if disabled else '否'}")

            if disabled:
                continue

            available_count += 1

            # 解析模型冷却
            model_cooldowns = {}
            if model_cooldowns_json:
                try:
                    model_cooldowns = json.loads(model_cooldowns_json)
                except:
                    print(f"   ❌ 无法解析 model_cooldowns: {model_cooldowns_json}")

            if model_cooldowns:
                print(f"   ❄️  模型冷却状态:")
                for model_key, cooldown_ts in model_cooldowns.items():
                    if cooldown_ts:
                        cooldown_time = datetime.fromtimestamp(cooldown_ts, timezone.utc)
                        remaining = cooldown_ts - current_time
                        status = "冷却中" if remaining > 0 else "可用"
                        print(f"      {model_key}: {cooldown_time.strftime('%Y-%m-%d %H:%M:%S')} UTC ({status}, 剩余 {remaining:.1f}s)")

            # 特别检查 claude-opus-4-5-thinking
            opus_thinking_cooldown = model_cooldowns.get("claude-opus-4-5-thinking")
            if opus_thinking_cooldown is None or current_time >= opus_thinking_cooldown:
                opus_thinking_available_count += 1
                print(f"   ✅ claude-opus-4-5-thinking: 可用")
            else:
                remaining = opus_thinking_cooldown - current_time
                print(f"   ❄️  claude-opus-4-5-thinking: 冷却中 (剩余 {remaining:.1f}s)")

            # 解析错误码
            if error_codes_json:
                try:
                    error_codes = json.loads(error_codes_json)
                    if error_codes:
                        print(f"   ⚠️  错误码: {error_codes}")
                except:
                    print(f"   ❌ 无法解析 error_codes: {error_codes_json}")

        print(f"\n📊 统计:")
        print(f"   🟢 可用凭证总数: {available_count}")
        print(f"   🎯 claude-opus-4-5-thinking 可用凭证数: {opus_thinking_available_count}")

        # 2. 模拟 CredentialManager 的选择逻辑
        print(f"\n🔧 模拟凭证选择逻辑...")

        from src.credential_manager import get_credential_manager
        credential_manager = await get_credential_manager()

        # 测试获取 claude-opus-4-5-thinking 凭证
        print(f"   🧪 测试 model_key='claude-opus-4-5-thinking'...")
        result = await credential_manager.get_valid_credential(
            is_antigravity=True,
            model_key="claude-opus-4-5-thinking"
        )

        if result:
            filename, cred_data = result
            print(f"   ✅ 成功获取凭证: {filename}")
        else:
            print(f"   ❌ 无法获取凭证")

        # 测试退化为任意凭证
        print(f"   🧪 测试 model_key=None (任意凭证)...")
        result_any = await credential_manager.get_valid_credential(
            is_antigravity=True,
            model_key=None
        )

        if result_any:
            filename, cred_data = result_any
            print(f"   ✅ 任意凭证可用: {filename}")
        else:
            print(f"   ❌ 连任意凭证都没有")

        # 3. 检查降级映射
        from src.fallback_manager import get_cross_pool_fallback
        fallback_model = get_cross_pool_fallback("claude-opus-4-5-thinking")
        print(f"\n🔄 降级映射: claude-opus-4-5-thinking -> {fallback_model}")

        if fallback_model:
            print(f"   🧪 测试降级模型凭证 '{fallback_model}'...")
            result_fallback = await credential_manager.get_valid_credential(
                is_antigravity=True,
                model_key=fallback_model
            )

            if result_fallback:
                filename, cred_data = result_fallback
                print(f"   ✅ 降级模型凭证可用: {filename}")
            else:
                print(f"   ❌ 降级模型凭证不可用")

        conn.close()

    except Exception as e:
        print(f"❌ 数据库操作失败: {e}")

    print(f"\n🏁 诊断完成")


if __name__ == "__main__":
    asyncio.run(debug_opus_thinking())