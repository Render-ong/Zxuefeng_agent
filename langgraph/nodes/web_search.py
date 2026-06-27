"""web_search 节点 — 联网搜索最新信息"""
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "GraphRAG"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llm_config import get_api_config

from engine.web_search import web_search as _web_search

log = logging.getLogger("lg_agent.web_search")


DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "GraphRAG", "admission_clean.db"
)


def _build_search_query(user_msg: str, profile: dict, intent: str) -> str:
    """根据意图构建搜索查询"""
    province = profile.get("province", "")
    major = (profile.get("majors") or [""])[0]
    school = (profile.get("schools") or profile.get("compare_targets") or [""])[0]

    if intent == "recommend" and province and major:
        return f"{province} {major} 2025 录取分数线 就业"
    if intent == "compare" and school:
        return f"{school} 2025 录取分数线 就业"
    if intent == "explain" and major:
        return f"{major} 专业 就业前景 2025"
    if intent == "policy" and province:
        return f"{province} 2025 高考志愿填报政策"

    return user_msg


def web_search_node(state: dict) -> dict:
    """联网搜索，补充最新分数线、就业信息等"""
    # ponytail: 优先用前端透传的 per-request config，fallback 到后端全局配置。
    # 与 generate/ask/recommend 节点保持一致的合并逻辑。
    _global = get_api_config()
    _req = state.get("api_config") or {}
    api_config = {**_global, **{k: v for k, v in _req.items() if v}}
    tavily_key = api_config.get("tavily", "")

    msg = state.get("user_message", "")
    profile = state.get("profile", {})
    intent = state.get("intent", "chat")

    query = _build_search_query(msg, profile, intent)

    # chat 意图跳过搜索以节省 API 调用
    if intent == "chat":
        log.info('intent=chat, skip search')
        return {"web_context": ""}

    try:
        results = _web_search(query, tavily_key=tavily_key, n=3)
        ctx = "【联网信息】\n" + "\n".join(f"- {r}" for r in results if r)
    except Exception:
        log.warning('web_search failed', exc_info=True)
        results = []
        ctx = ""

    log.info(f'query="{query[:40]}" results={len(results)} ctx={len(ctx)} chars')
    return {"web_context": ctx}
