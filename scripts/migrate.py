#!/usr/bin/env python3
"""数据库迁移脚本 — 版本管理 + 表结构检查

用法：
    python scripts/migrate.py          # 检查并执行迁移
    python scripts/migrate.py --check  # 仅检查，不执行
    python scripts/migrate.py --version # 显示当前版本

ponytail: 简单版本号管理，用 schema_version 表记录当前版本。
升级路径：迁 PostgreSQL 后用 Alembic 替代。
"""
import argparse
import os
import sqlite3
import sys

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 当前 schema 版本（新增迁移时递增）
CURRENT_VERSION = 2

# 数据库路径
DB_DIR = os.path.join(ROOT, "data", "user")
USER_DB = os.path.join(DB_DIR, "user_data.db")
LANGGRAPH_DB = os.path.join(DB_DIR, "langgraph_data.db")


def ensure_db_dir():
    os.makedirs(DB_DIR, exist_ok=True)


def get_version(db_path: str) -> int:
    """获取数据库当前 schema 版本"""
    if not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    try:
        # 检查 schema_version 表是否存在
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if not row:
            return 0
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def set_version(db_path: str, version: int):
    """设置数据库 schema 版本"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, datetime('now'))",
            (version,)
        )
        conn.commit()
    finally:
        conn.close()


def migrate_user_db():
    """迁移 user_data.db"""
    conn = sqlite3.connect(USER_DB)
    try:
        # V1: 初始表结构
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                province TEXT,
                score TEXT,
                rank TEXT,
                customProfile TEXT,
                api_key TEXT,
                tavily_key TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        """)
        # V2: 添加 api_key / tavily_key 列（已存在则忽略）
        for col in ('api_key', 'tavily_key'):
            try:
                conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在
        conn.commit()
    finally:
        conn.close()


def migrate_langgraph_db():
    """迁移 langgraph_data.db"""
    conn = sqlite3.connect(LANGGRAPH_DB)
    try:
        # V1: 初始表结构
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                nickname TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                profile_data TEXT,
                updated_at TEXT,
                PRIMARY KEY (user_id, conversation_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                user_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_profiles_user ON profiles(user_id);
        """)
        conn.commit()
    finally:
        conn.close()


def run_migrations(dry_run: bool = False):
    """执行迁移"""
    ensure_db_dir()

    print(f"目标版本: {CURRENT_VERSION}")
    print()

    # user_data.db
    user_ver = get_version(USER_DB)
    print(f"[user_data.db] 当前版本: {user_ver}")
    if user_ver < CURRENT_VERSION:
        if dry_run:
            print(f"  -> 需要迁移: {user_ver} -> {CURRENT_VERSION}")
        else:
            print(f"  -> 迁移中: {user_ver} -> {CURRENT_VERSION}")
            migrate_user_db()
            set_version(USER_DB, CURRENT_VERSION)
            print(f"  -> 完成")
    else:
        print(f"  -> 已是最新版本")

    # langgraph_data.db
    lg_ver = get_version(LANGGRAPH_DB)
    print(f"[langgraph_data.db] 当前版本: {lg_ver}")
    if lg_ver < CURRENT_VERSION:
        if dry_run:
            print(f"  -> 需要迁移: {lg_ver} -> {CURRENT_VERSION}")
        else:
            print(f"  -> 迁移中: {lg_ver} -> {CURRENT_VERSION}")
            migrate_langgraph_db()
            set_version(LANGGRAPH_DB, CURRENT_VERSION)
            print(f"  -> 完成")
    else:
        print(f"  -> 已是最新版本")


def show_version():
    """显示当前版本"""
    print(f"目标版本: {CURRENT_VERSION}")
    print()
    if os.path.exists(USER_DB):
        print(f"[user_data.db] 版本: {get_version(USER_DB)}")
    else:
        print(f"[user_data.db] 不存在")
    if os.path.exists(LANGGRAPH_DB):
        print(f"[langgraph_data.db] 版本: {get_version(LANGGRAPH_DB)}")
    else:
        print(f"[langgraph_data.db] 不存在")


def main():
    parser = argparse.ArgumentParser(description="数据库迁移脚本")
    parser.add_argument("--check", action="store_true", help="仅检查，不执行迁移")
    parser.add_argument("--version", action="store_true", help="显示当前版本")
    args = parser.parse_args()

    if args.version:
        show_version()
    elif args.check:
        run_migrations(dry_run=True)
    else:
        run_migrations(dry_run=False)


if __name__ == "__main__":
    main()