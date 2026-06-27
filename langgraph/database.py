"""数据库管理模块 — 用户、对话、画像、消息的持久化存储"""
import json
import os
import sqlite3
import uuid
from datetime import datetime
from contextlib import contextmanager

_DB_PATH = None

# ponytail: 用户数据统一存放项目根 data/user/，便于备份/迁移。
# 升级路径：迁 PostgreSQL 后此目录废弃，改用 DB 连接串。
_HERE = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(_HERE, "..", "data", "user")


def init_db(db_path: str = None) -> None:
    """初始化数据库"""
    global _DB_PATH
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    _DB_PATH = db_path or os.path.join(USER_DATA_DIR, "langgraph_data.db")
    with get_conn() as conn:
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
        # 迁移：旧库 messages 表无 user_id 列（CREATE TABLE IF NOT EXISTS 不会改已有表结构）。
        # 用 PRAGMA 检测后 ALTER 补列，旧消息 user_id 为 NULL，新写入必须带 user_id。
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
        if cols and "user_id" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN user_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id)")


@contextmanager
def get_conn():
    """获取数据库连接

    ponytail: 加 WAL + busy_timeout 缓解多并发下 "database is locked"。
    WAL 让读写不互斥；busy_timeout 让短时锁等待 5s 而非立即报错。
    升级路径：迁 PostgreSQL（用户量上去后再做，SQLite WAL 单机够用）。
    """
    conn = sqlite3.connect(_DB_PATH or os.path.join(USER_DATA_DIR, "langgraph_data.db"), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────
#  用户管理
# ─────────────────────────────────────────────

def create_user(user_id: str, nickname: str = "") -> dict:
    """创建用户"""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, nickname, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, nickname, now, now)
        )
    return {"user_id": user_id, "nickname": nickname}


def get_user(user_id: str) -> dict | None:
    """获取用户信息"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


# ─────────────────────────────────────────────
#  对话管理
# ─────────────────────────────────────────────

def create_conversation(user_id: str, title: str = "") -> str:
    """创建新对话，返回 conversation_id

    ponytail: 用完整 uuid.hex（32 位）而非截断 8 位。
    旧版 [:8] 仅 16^8≈4 亿空间，可被枚举访问他人对话；
    完整 hex 后配合 user_id 归属校验，跨用户读写不再可行。
    """
    conv_id = uuid.uuid4().hex
    now = datetime.now().isoformat()
    if not title:
        title = f"对话 {now[:10]}"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (conversation_id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, user_id, title, now, now)
        )
    return conv_id


def get_user_conversations(user_id: str) -> list:
    """获取用户的所有对话"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation(conversation_id: str) -> dict | None:
    """获取对话信息"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_conversation(conversation_id: str, user_id: str) -> bool:
    """删除对话及其消息、画像（校验 user_id 归属，防跨用户删他人对话）"""
    with get_conn() as conn:
        conv = conn.execute(
            "SELECT user_id FROM conversations WHERE conversation_id = ?",
            (conversation_id,)
        ).fetchone()
        if not conv or conv[0] != user_id:
            return False
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM profiles WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
        return True


# ─────────────────────────────────────────────
#  画像管理
# ─────────────────────────────────────────────

def save_profile(user_id: str, conversation_id: str, profile: dict) -> None:
    """保存用户画像"""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profiles (user_id, conversation_id, profile_data, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, conversation_id, json.dumps(profile, ensure_ascii=False), now)
        )


def load_profile(user_id: str, conversation_id: str) -> dict:
    """加载用户画像"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT profile_data FROM profiles WHERE user_id = ? AND conversation_id = ?",
            (user_id, conversation_id)
        ).fetchone()
        if row and row["profile_data"]:
            return json.loads(row["profile_data"])
    return {}


def get_user_profiles(user_id: str) -> list:
    """获取用户所有对话的画像"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.*, c.title FROM profiles p JOIN conversations c ON p.conversation_id = c.conversation_id WHERE p.user_id = ? ORDER BY p.updated_at DESC",
            (user_id,)
        ).fetchall()
        return [{"conversation_id": r["conversation_id"], "title": r["title"], "profile": json.loads(r["profile_data"])} for r in rows]


# ─────────────────────────────────────────────
#  消息管理
# ─────────────────────────────────────────────

def save_message(conversation_id: str, user_id: str, role: str, content: str) -> str:
    """保存消息（user_id 必传，写入 user_id 列用于按用户隔离查询）"""
    msg_id = uuid.uuid4().hex
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, conversation_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, conversation_id, user_id, role, content, now)
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
            (now, conversation_id)
        )
    return msg_id


def get_conversation_messages(conversation_id: str, user_id: str, limit: int = 50) -> list:
    """获取对话消息历史（按 user_id 隔离，防跨用户读他人对话）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND user_id = ? ORDER BY created_at ASC LIMIT ?",
            (conversation_id, user_id, limit)
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]


def get_conversation_summary(conversation_id: str, user_id: str) -> dict:
    """获取对话摘要（按 user_id 隔离）"""
    with get_conn() as conn:
        msg_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id)
        ).fetchone()["cnt"]
        last_msg = conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1",
            (conversation_id, user_id)
        ).fetchone()
    return {
        "message_count": msg_count,
        "last_message": last_msg["content"][:50] if last_msg else "",
    }
