#!/usr/bin/env python3
"""调试脚本：查看数据库中的 model_cooldowns"""

import sqlite3
import json
import time
from datetime import datetime, timezone

db_path = "creds/credentials.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 先查看所有表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f"数据库中的表: {[t[0] for t in tables]}\n")

# 尝试查找正确的表名
table_name = None
for t in tables:
    if 'antigravity' in t[0].lower():
        table_name = t[0]
        break

if not table_name:
    for t in tables:
        if 'credential' in t[0].lower():
            table_name = t[0]
            break

if not table_name and tables:
    table_name = tables[0][0]

if not table_name:
    print("数据库中没有表！")
    conn.close()
    exit(1)

print(f"使用表: {table_name}\n")

# 查询所有凭证的 model_cooldowns
cursor.execute(f"""
    SELECT filename, model_cooldowns, disabled
    FROM {table_name}
""")

rows = cursor.fetchall()

print(f"找到 {len(rows)} 个凭证\n")
print("=" * 80)

current_time = time.time()

for filename, model_cooldowns_json, disabled in rows:
    print(f"\n凭证: {filename}")
    print(f"禁用状态: {disabled}")

    if model_cooldowns_json:
        try:
            model_cooldowns = json.loads(model_cooldowns_json)
            if model_cooldowns:
                print(f"模型冷却:")
                for model_key, cooldown_until in model_cooldowns.items():
                    if cooldown_until > current_time:
                        remaining = cooldown_until - current_time
                        cooldown_time = datetime.fromtimestamp(cooldown_until, timezone.utc)
                        print(f"  - {model_key}: 冷却中，剩余 {remaining:.1f}秒，到期时间 {cooldown_time.isoformat()}")
                    else:
                        print(f"  - {model_key}: 已过期")
            else:
                print("模型冷却: 无")
        except Exception as e:
            print(f"解析失败: {e}")
    else:
        print("模型冷却: 无")

    print("-" * 80)

conn.close()
