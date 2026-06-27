"""一次性脚本：给 user_profiles 表添加 api_key / tavily_key 列"""
import sqlite3
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db = os.path.join(ROOT, "data", "user", "user_data.db")

if not os.path.exists(db):
    print(f"数据库不存在: {db}")
    exit(1)

conn = sqlite3.connect(db)
cols = [row[1] for row in conn.execute("PRAGMA table_info(user_profiles)").fetchall()]
print(f"当前列: {cols}")

for col in ("api_key", "tavily_key"):
    if col not in cols:
        conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} TEXT")
        print(f"  已添加列: {col}")
    else:
        print(f"  列已存在: {col}")

conn.commit()
cols = [row[1] for row in conn.execute("PRAGMA table_info(user_profiles)").fetchall()]
print(f"最终列: {cols}")
conn.close()
print("完成，请重启后端服务")
