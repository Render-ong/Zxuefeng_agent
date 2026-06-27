"""用户管理模块 — 处理用户、对话、画像的业务逻辑"""
import uuid
from datetime import datetime
import database as db
from state import _default_profile


def init_user_system(db_path: str = None) -> None:
    """初始化用户系统"""
    db.init_db(db_path)


def get_or_create_user(user_id: str, nickname: str = "") -> dict:
    """获取或创建用户"""
    user = db.get_user(user_id)
    if not user:
        user = db.create_user(user_id, nickname)
    return user


def start_conversation(user_id: str, title: str = "") -> str:
    """开始新对话"""
    db.create_user(user_id)
    conv_id = db.create_conversation(user_id, title)
    return conv_id


def get_conversation_id(user_id: str) -> str:
    """获取用户的最新对话 ID，如果没有则创建"""
    convs = db.get_user_conversations(user_id)
    if convs:
        return convs[0]["conversation_id"]
    return start_conversation(user_id)


def load_conversation_state(user_id: str, conversation_id: str) -> dict:
    """加载对话状态（用于恢复 LangGraph 状态）"""
    profile = db.load_profile(user_id, conversation_id)
    messages = db.get_conversation_messages(conversation_id, user_id)

    return {
        "profile": profile or _default_profile(),
        "messages": messages,
    }


def save_conversation_state(user_id: str, conversation_id: str, state: dict) -> None:
    """保存对话状态"""
    if "profile" in state:
        db.save_profile(user_id, conversation_id, state["profile"])


def save_user_message(user_id: str, conversation_id: str, content: str) -> None:
    """保存用户消息（user_id 必传，用于归属校验与按用户隔离）"""
    db.save_message(conversation_id, user_id, "user", content)


def save_assistant_message(user_id: str, conversation_id: str, content: str) -> None:
    """保存助手消息"""
    db.save_message(conversation_id, user_id, "assistant", content)


def get_history_messages(user_id: str, conversation_id: str, limit: int = 20) -> list:
    """获取历史消息（用于 LangGraph 的 messages）"""
    return db.get_conversation_messages(conversation_id, user_id, limit)


def get_user_info(user_id: str) -> dict:
    """获取用户完整信息"""
    user = db.get_user(user_id)
    convs = db.get_user_conversations(user_id)
    profiles = db.get_user_profiles(user_id)

    return {
        "user": user,
        "conversations": convs,
        "profiles": profiles,
    }


def get_conversation_detail(user_id: str, conversation_id: str) -> dict:
    """获取对话详情（校验 conversation 归属当前 user_id）"""
    conv = db.get_conversation(conversation_id)
    if not conv or conv["user_id"] != user_id:
        return None

    messages = db.get_conversation_messages(conversation_id, user_id)
    profile = db.load_profile(user_id, conversation_id)
    summary = db.get_conversation_summary(conversation_id, user_id)

    return {
        "conversation": conv,
        "messages": messages,
        "profile": profile,
        "summary": summary,
    }


def list_conversations(user_id: str) -> list:
    """列出用户的所有对话"""
    convs = db.get_user_conversations(user_id)
    result = []
    for conv in convs:
        summary = db.get_conversation_summary(conv["conversation_id"], user_id)
        result.append({
            **conv,
            **summary,
        })
    return result
