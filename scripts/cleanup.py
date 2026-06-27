#!/usr/bin/env python3
"""过期数据清理脚本

清理内容：
1. 过期 session（已过期的登录 token）
2. 超过 30 天未活跃的对话及关联消息
3. 超过 30 天的 checkpoints（LangGraph checkpoint）

用法：
    python scripts/cleanup.py                    # 执行清理
    python scripts/cleanup.py --dry-run          # 仅预览，不执行
    python scripts/cleanup.py --days 60          # 自定义保留天数

cron 配置（每天凌晨 4 点执行）：
    0 4 * * * cd /opt/xuefeng-agent && /opt/xuefeng-agent/venv/bin/python scripts/cleanup.py >> /opt/xuefeng-agent/logs/cleanup.log 2>&1

ponytail: 简单脚本够用，升级路径：迁 PostgreSQL 后用 pg_cron 或 Celery Beat。
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_DIR = os.path.join(ROOT, "data", "user")
USER_DB = os.path.join(DB_DIR, "user_data.db")
LANGGRAPH_DB = os.path.join(DB_DIR, "langgraph_data.db")
CHECKPOINTS_DB = os.path.join(DB_DIR, "checkpoints.db")


def cleanup_sessions(conn, cutoff: str, dry_run: bool) -> int:
    """清理过期 session"""
    cursor = conn.execute("SELECT COUNT(*) FROM sessions WHERE expires_at < ?", (cutoff,))
    count = cursor.fetchone()[0]
    if count > 0 and not dry_run:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (cutoff,))
    return count


def cleanup_conversations(conn, cutoff: str, dry_run: bool) -> int:
    """清理超过 N 天未活跃的对话及关联消息"""
    # 找到过期对话
    cursor = conn.execute(
        "SELECT conversation_id FROM conversations WHERE updated_at < ?", (cutoff,)
    )
    conv_ids = [row[0] for row in cursor.fetchall()]
    if not conv_ids:
        return 0

    if not dry_run:
        # 删除关联消息
        placeholders = ",".join("?" * len(conv_ids))
        conn.execute(f"DELETE FROM messages WHERE conversation_id IN ({placeholders})", conv_ids)
        # 删除关联画像
        conn.execute(f"DELETE FROM profiles WHERE conversation_id IN ({placeholders})", conv_ids)
        # 删除对话
        conn.execute(f"DELETE FROM conversations WHERE conversation_id IN ({placeholders})", conv_ids)
    return len(conv_ids)


def cleanup_checkpoints(conn, cutoff: str, dry_run: bool) -> int:
    """清理过期 checkpoints"""
    # LangGraph checkpoint 表结构可能不同，尝试通用方式
    try:
        # 检查是否有 ts 列
        cols = [row[1] for row in conn.execute("PRAGMA table_info(checkpoints)").fetchall()]
        if "ts" in cols:
            cursor = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE ts < ?", (cutoff,))
        elif "created_at" in cols:
            cursor = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE created_at < ?", (cutoff,))
        else:
            return 0
        count = cursor.fetchone()[0]
        if count > 0 and not dry_run:
            if "ts" in cols:
                conn.execute("DELETE FROM checkpoints WHERE ts < ?", (cutoff,))
            else:
                conn.execute("DELETE FROM checkpoints WHERE created_at < ?", (cutoff,))
        return count
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="过期数据清理脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行删除")
    parser.add_argument("--days", type=int, default=30, help="保留天数（默认 30）")
    args = parser.parse_args()

    cutoff = (datetime.now() - timedelta(days=args.days)).isoformat()
    print(f"清理 {args.days} 天前的数据（截止: {cutoff}）")
    if args.dry_run:
        print("[DRY-RUN] 仅预览，不执行删除")
    print()

    total = 0

    # user_data.db: session 清理
    if os.path.exists(USER_DB):
        conn = sqlite3.connect(USER_DB)
        try:
            count = cleanup_sessions(conn, cutoff, args.dry_run)
            print(f"[user_data.db] 过期 session: {count}")
            total += count
            if not args.dry_run:
                conn.commit()
        finally:
            conn.close()
    else:
        print(f"[user_data.db] 不存在，跳过")

    # langgraph_data.db: 对话清理
    if os.path.exists(LANGGRAPH_DB):
        conn = sqlite3.connect(LANGGRAPH_DB)
        try:
            count = cleanup_conversations(conn, cutoff, args.dry_run)
            print(f"[langgraph_data.db] 过期对话: {count}")
            total += count
            if not args.dry_run:
                conn.commit()
        finally:
            conn.close()
    else:
        print(f"[langgraph_data.db] 不存在，跳过")

    # checkpoints.db: checkpoint 清理
    if os.path.exists(CHECKPOINTS_DB):
        conn = sqlite3.connect(CHECKPOINTS_DB)
        try:
            count = cleanup_checkpoints(conn, cutoff, args.dry_run)
            print(f"[checkpoints.db] 过期 checkpoints: {count}")
            total += count
            if not args.dry_run:
                conn.commit()
        finally:
            conn.close()
    else:
        print(f"[checkpoints.db] 不存在，跳过")

    print()
    print(f"总计清理: {total} 条记录")


if __name__ == "__main__":
    main()