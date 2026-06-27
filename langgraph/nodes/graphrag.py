"""graphrag 节点 — 图谱检索上下文"""
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "GraphRAG"))

from engine import graphrag

log = logging.getLogger("lg_agent.graphrag")


def graphrag_node(state: dict) -> dict:
    """图谱检索：社区摘要 + 学校画像 + 知识库"""
    if not graphrag.is_ready():
        log.info('graph not ready, skip')
        return {"graph_context": ""}

    msg = state.get("user_message", "")
    profile = state.get("profile", {})
    intent = state.get("intent", "general")

    try:
        ctx = graphrag.build_context(msg, profile, intent)
    except Exception:
        log.warning('build_context failed', exc_info=True)
        ctx = ""

    log.info(f'graph_context={len(ctx)} chars')
    return {"graph_context": ctx}
