"""LangGraph 志愿填报 Agent — 张雪峰风格

图结构：
    analyze
      │
      ├─ 信息槽未满 → ask → END（等待用户下一轮输入）
      │
      └─ 信息槽已满 → intent_router
                          │
                          ├─ recommend → recommend_node
                          ├─ compare   → compare_node
                          ├─ explain   → explain_node
                          ├─ policy    → policy_node
                          └─ chat/other→ (跳过专业节点)
                          │
                          ▼
                    graphrag → web_search → generate → END
"""
import sys
import os
import logging
import atexit
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_GRAPH_RAG = os.path.join(_HERE, "..", "GraphRAG")
sys.path.insert(0, _HERE)
sys.path.insert(0, _GRAPH_RAG)

from langgraph.graph import StateGraph, END
from checkpointer_manager import CheckpointerManager

# ponytail: 用 logging 取代裸 print，便于生产环境调级别/接 Sentry。
# 升级路径：接入 structlog 后替换 getLogger。
log = logging.getLogger("lg_agent")

from state import AgentState, _default_profile
from nodes.analyze import analyze_node
from nodes.ask import ask_node
from nodes.recommend import recommend_node
from nodes.compare import compare_node
from nodes.explain import explain_node
from nodes.policy import policy_node
from nodes.graphrag import graphrag_node
from nodes.web_search import web_search_node
from nodes.generate import generate_node

import user_manager
import database as db


# ═══════════════════════════════════════════
#  路由函数
# ═══════════════════════════════════════════

def route_after_analyze(state: AgentState) -> str:
    """分析后路由：信息槽未满则追问，满了按意图分流"""
    if state.get("needs_ask"):
        return "ask"

    intent = state.get("intent", "chat")
    if intent == "recommend":
        return "recommend"
    if intent == "compare":
        return "compare"
    if intent == "explain":
        return "explain"
    if intent == "policy":
        return "policy"
    return "graphrag"  # chat / collect 默认直接检索生成


def route_after_special(state: AgentState) -> str:
    """compare / explain / policy / recommend 之后统一走检索生成"""
    return "graphrag"


def route_after_ask(state: AgentState) -> str:
    """追问后结束本轮，等用户下一轮输入"""
    return END


# ═══════════════════════════════════════════
#  建图（使用持久化 checkpoint）
# ═══════════════════════════════════════════

# ponytail: 用户数据统一存放项目根 data/user/，与 langgraph_data.db 同目录便于备份。
_CHECKPOINT_DB = os.path.join(_HERE, "..", "data", "user", "checkpoints.db")
os.makedirs(os.path.dirname(_CHECKPOINT_DB), exist_ok=True)

# Checkpointer 生命周期由 CheckpointerManager 统一管理（封装创建/配置/清理），
# 取代之前手动 __enter__() + atexit 的临时方案。
# 升级路径：换 PostgresSaver 后由连接池管理生命周期，Manager 可简化或移除。
_checkpointer_manager = CheckpointerManager(_CHECKPOINT_DB)
_checkpointer = _checkpointer_manager.start()

# atexit 兜底：进程退出时关闭连接，避免 WAL 文件残留 + 句柄泄漏。
# （SIGKILL 等强制终止仍无法保证，已是 Python 层面最佳兜底）
atexit.register(_checkpointer_manager.close)


def build_agent():
    builder = StateGraph(AgentState)

    builder.add_node("analyze", analyze_node)
    builder.add_node("ask", ask_node)
    builder.add_node("recommend", recommend_node)
    builder.add_node("compare", compare_node)
    builder.add_node("explain", explain_node)
    builder.add_node("policy", policy_node)
    builder.add_node("graphrag", graphrag_node)
    builder.add_node("web_search", web_search_node)
    builder.add_node("generate", generate_node)

    builder.set_entry_point("analyze")

    # analyze → 分流
    builder.add_conditional_edges(
        "analyze",
        route_after_analyze,
        {
            "ask": "ask",
            "recommend": "recommend",
            "compare": "compare",
            "explain": "explain",
            "policy": "policy",
            "graphrag": "graphrag",
        },
    )

    # ask → END
    builder.add_conditional_edges("ask", route_after_ask, {END: END})

    # 专业节点 → graphrag
    builder.add_edge("recommend", "graphrag")
    builder.add_edge("compare", "graphrag")
    builder.add_edge("explain", "graphrag")
    builder.add_edge("policy", "graphrag")

    # 通用检索链路
    builder.add_edge("graphrag", "web_search")
    builder.add_edge("web_search", "generate")
    builder.add_edge("generate", END)

    return builder.compile(checkpointer=_checkpointer)


# ═══════════════════════════════════════════
#  对外接口
# ═══════════════════════════════════════════

# ponytail: _agent 单例初始化加锁，防多线程 gunicorn 下重复建图/checkpointer 句柄泄漏。
# 升级路径：换 PostgresSaver 后单例可移除（每次 build 无状态句柄）。
_agent = None
_agent_lock = threading.Lock()


def get_agent():
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                _agent = build_agent()
    return _agent


def run(message: str, user_id: str = "default", conversation_id: str = None,
        mode: str = "gaokao", api_config: dict = None) -> dict:
    """
    外部调用入口。

    鉴权契约（调用方必读）：
    - user_id 必须是调用方在服务端通过 wx code2session 换取的真实 openid，
      不可由客户端任意指定或透传请求头。
    - 调用方需在 HTTP 网关层完成 Authorization: Bearer <token> 校验，
      反查 session 表得到 user_id 后再传入本函数。
    - 直接信任客户端传入的 user_id 会导致 IDOR（任意伪造身份读写他人对话/画像）。

    参数
    ----
    message : 用户消息
    user_id : 用户 ID（微信 openid，必填，不可为空字符串）
    conversation_id : 对话 ID（为空则自动创建；非空时校验归属，防跨用户读写）
    mode : "gaokao" | "fun"

    返回
    ----
    {"reply": "...", "debug": {"intent", "params", "pipeline", "sources"},
     "profile": {...}, "needs_ask": bool, "ask_questions": [...],
     "conversation_id": "..."}
    返回结构对齐 GraphRAG/API接口规范.md 中 /api/chat 的响应格式。
    """
    # 鉴权契约硬校验：调用方必须传入非空 user_id（已通过网关 token 反查得到）
    if not user_id or not user_id.strip():
        raise ValueError("user_id 不可为空，调用方必须完成 openid→user_id 服务端映射")
    user_id = user_id.strip()

    # 输入长度校验（防超长消息打爆 LLM token 配额与 DB 行）
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message 不可为空")
    if len(message) > 4000:
        message = message[:4000]

    # 初始化用户系统
    user_manager.init_user_system()

    # 获取或创建对话，并校验归属（防跨用户读写他人对话）
    if not conversation_id:
        conversation_id = user_manager.start_conversation(user_id)
    else:
        conv = db.get_conversation(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return {
                "reply": "对话不存在或无权访问",
                "intent": "",
                "profile": {},
                "needs_ask": False,
                "ask_questions": [],
                "conversation_id": conversation_id,
            }

    # thread_id = user_id:conversation_id（多用户多对话隔离）
    thread_id = f"{user_id}:{conversation_id}"

    # 保存用户消息
    user_manager.save_user_message(user_id, conversation_id, message)

    # 从数据库加载已有画像
    profile = user_manager.load_conversation_state(user_id, conversation_id)["profile"]

    agent = get_agent()
    config = {"configurable": {"thread_id": thread_id}}

    # 从 checkpoint 恢复状态
    # ponytail: checkpoint 恢复时过滤掉 family_bg/customProfile ——
    # 这些字段应由当前对话的消息重新提取（analyze._extract_family），
    # 不应从旧 checkpoint 继承，否则旧对话的"家庭背景：教育系统"会泄漏到续接的对话中。
    # 升级路径：迁 PostgreSQL 后 profile 按 conversation_id 隔离，checkpoint 不再存 profile。
    ckpt_source = "db"
    _ckpt_stale_fields = {"family_bg", "customProfile"}
    try:
        current_state = agent.get_state(config)
        if current_state and current_state.values:
            existing_profile = current_state.values.get("profile")
            if existing_profile and any(existing_profile.values()):
                profile = dict(existing_profile)
                for f in _ckpt_stale_fields:
                    profile.pop(f, None)
                ckpt_source = "checkpoint"
    except Exception:
        pass

    # ponytail: 诊断日志 — 打印 profile 来源和 family_bg，定位跨对话泄漏问题。
    # 升级路径：确认无泄漏后可降级为 debug 级别或移除。
    log.info(f'INVOKE user={user_id[:8]}… conv={conversation_id[:8]}… msg="{message[:40]}" src={ckpt_source}')
    if profile.get("family_bg"):
        log.warning(f'family_bg="{profile["family_bg"]}" in conv={conversation_id[:8]}… (src={ckpt_source})')
    if profile.get("customProfile"):
        log.warning(f'customProfile="{profile["customProfile"][:60]}" in conv={conversation_id[:8]}… (src={ckpt_source})')

    # ponytail: invoke 失败时记录完整异常栈到服务端日志，再向上抛出由网关统一兜底。
    # 升级路径：接 Sentry 后此处改为 log.exception + capture。
    try:
        result = agent.invoke(
            {
                "messages": [],
                "user_message": message,
                "mode": mode,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "api_config": api_config,
                "profile": profile,
            },
            config,
        )
    except Exception:
        log.exception("agent.invoke failed user=%s conv=%s", user_id, conversation_id)
        raise

    reply = result.get("reply", "")
    log.info(f'DONE intent={result.get("intent")} needs_ask={result.get("needs_ask")} reply={len(reply)} chars')
    user_manager.save_assistant_message(user_id, conversation_id, reply)

    # 保存画像到数据库
    profile = result.get("profile", {})
    user_manager.save_conversation_state(user_id, conversation_id, {
        "profile": profile
    })

    # 返回结构对齐 API接口规范.md：debug 包裹 intent/params/pipeline/sources
    # ponytail: pipeline/sources 当前未在节点链路中采集，留空数组占位。
    # 升级路径：在 recommend/compare/graphrag 节点回填 pipeline 步骤与 sources 来源。
    intent = result.get("intent", "")
    params = {k: profile.get(k) for k in (
        "province", "subject", "score", "rank", "majors",
        "schools", "region_avoid", "region_pref", "tags"
    ) if profile.get(k) not in (None, "", [], 0)}

    return {
        "reply": reply,
        "debug": {
            "intent": intent,
            "params": params,
            "pipeline": [],
            "sources": [],
        },
        "profile": profile,
        "needs_ask": result.get("needs_ask", False),
        "ask_questions": result.get("ask_questions", []),
        "conversation_id": conversation_id,
    }


def get_user_conversations(user_id: str) -> list:
    """获取用户的所有对话"""
    user_manager.init_user_system()
    return user_manager.list_conversations(user_id)


def get_conversation_detail(user_id: str, conversation_id: str) -> dict:
    """获取对话详情"""
    user_manager.init_user_system()
    return user_manager.get_conversation_detail(user_id, conversation_id)
