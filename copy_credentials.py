#!/usr/bin/env python3
"""从 master 版本复制凭证数据到 official 版本"""
import sqlite3
import os
import shutil
from datetime import datetime

master_db = r'F:\antigravity2api\gcli2api\creds\credentials.db'
official_db = r'F:\antigravity2api-official\creds\credentials.db'

# 备份 official 数据库
backup_path = f'{official_db}.backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
if os.path.exists(official_db):
    shutil.copy2(official_db, backup_path)
    print(f'已备份 official 数据库到: {backup_path}')

# 连接两个数据库
master_conn = sqlite3.connect(master_db)
official_conn = sqlite3.connect(official_db)

master_cursor = master_conn.cursor()
official_cursor = official_conn.cursor()

# 复制 antigravity_credentials 表
print('\n复制 antigravity_credentials 表...')
master_cursor.execute('SELECT * FROM antigravity_credentials')
rows = master_cursor.fetchall()

# 获取列名
master_cursor.execute('PRAGMA table_info(antigravity_credentials)')
columns = [col[1] for col in master_cursor.fetchall()]
print(f'列名: {columns}')

# 清空 official 的表（如果存在）
official_cursor.execute('DELETE FROM antigravity_credentials')

# 插入数据
placeholders = ','.join(['?' for _ in columns])
insert_sql = f'INSERT INTO antigravity_credentials ({",".join(columns)}) VALUES ({placeholders})'

count = 0
for row in rows:
    official_cursor.execute(insert_sql, row)
    count += 1

official_conn.commit()
print(f'已复制 {count} 条 Antigravity 凭证记录')

# 复制普通凭证（可选）
print('\n复制 credentials 表...')
master_cursor.execute('SELECT * FROM credentials')
rows = master_cursor.fetchall()

master_cursor.execute('PRAGMA table_info(credentials)')
columns = [col[1] for col in master_cursor.fetchall()]

official_cursor.execute('DELETE FROM credentials')
placeholders = ','.join(['?' for _ in columns])
insert_sql = f'INSERT INTO credentials ({",".join(columns)}) VALUES ({placeholders})'

count = 0
for row in rows:
    official_cursor.execute(insert_sql, row)
    count += 1

official_conn.commit()
print(f'已复制 {count} 条普通凭证记录')

# 复制配置文件（可选）
print('\n复制 config 表...')
try:
    master_cursor.execute('SELECT * FROM config')
    rows = master_cursor.fetchall()
    
    if rows:
        master_cursor.execute('PRAGMA table_info(config)')
        columns = [col[1] for col in master_cursor.fetchall()]
        
        official_cursor.execute('DELETE FROM config')
        placeholders = ','.join(['?' for _ in columns])
        insert_sql = f'INSERT INTO config ({",".join(columns)}) VALUES ({placeholders})'
        
        count = 0
        for row in rows:
            official_cursor.execute(insert_sql, row)
            count += 1
        
        official_conn.commit()
        print(f'已复制 {count} 条配置记录')
except sqlite3.OperationalError as e:
    print(f'Config 表复制跳过: {e}')

# 验证
official_cursor.execute('SELECT COUNT(*) FROM antigravity_credentials')
ag_count = official_cursor.fetchone()[0]
print(f'\n验证: Official 数据库现在有 {ag_count} 条 Antigravity 凭证')

master_conn.close()
official_conn.close()

print('\n完成！')

