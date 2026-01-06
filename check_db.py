#!/usr/bin/env python3
"""检查数据库中的凭证数据"""
import sqlite3
import os

db_path = os.path.join('creds', 'credentials.db')
if not os.path.exists(db_path):
    print(f"数据库文件不存在: {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 检查表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cursor.fetchall()]
print(f"数据库表: {tables}")

# 检查 antigravity_credentials
if 'antigravity_credentials' in tables:
    cursor.execute("SELECT COUNT(*) FROM antigravity_credentials")
    count = cursor.fetchone()[0]
    print(f"\nAntigravity 凭证数量: {count}")
    
    if count > 0:
        cursor.execute("SELECT filename, disabled, error_codes FROM antigravity_credentials LIMIT 10")
        rows = cursor.fetchall()
        print("\n凭证列表:")
        for row in rows:
            print(f"  - {row[0]} (disabled={row[1]}, errors={row[2]})")
    else:
        print("⚠️  没有 Antigravity 凭证！")
else:
    print("⚠️  antigravity_credentials 表不存在！")

# 检查普通凭证
if 'credentials' in tables:
    cursor.execute("SELECT COUNT(*) FROM credentials")
    count = cursor.fetchone()[0]
    print(f"\n普通凭证数量: {count}")

conn.close()

