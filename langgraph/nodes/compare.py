"""compare 节点 — 多校对比"""
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "GraphRAG"))

from engine.graph_subgraph import build_compare_context, format_profile, school_profile
from engine.sql_recommend import query_db

log = logging.getLogger("lg_agent.compare")


DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "GraphRAG", "admission_clean.db"
)


def _query_scores(province: str, school_names: list) -> list:
    """查询各校在目标省份的录取分数"""
    if not province or not school_names:
        return []

    all_rows = []
    for name in school_names[:4]:
        rows = query_db(DB_PATH, province=province, school=name, limit=3) or []
        for r in rows:
            r["school"] = name
        all_rows.extend(rows)
    return all_rows


def _format_score_rows(rows: list) -> str:
    if not rows:
        return ""
    lines = ["· 录取数据对比（来自本地数据库）："]
    by_school = {}
    for r in rows:
        school = r.get("school_name") or r.get("school", "未知")
        by_school.setdefault(school, []).append(r)

    for school, school_rows in by_school.items():
        line = f"  - {school}："
        parts = []
        for r in school_rows[:2]:
            year = r.get("year", "?")
            score = r.get("score", "?")
            rank = r.get("rank", "?")
            major = r.get("major_name", "")
            parts.append(f"{year}年 {major or '最低分'} {score}分/{rank}位")
        line += "；".join(parts) if parts else "暂无数据"
        lines.append(line)
    return "\n".join(lines)


def compare_node(state: dict) -> dict:
    """多校对比节点"""
    try:
        profile = state.get("profile", {})
        targets = profile.get("compare_targets", [])

        if len(targets) < 2:
            return {
                "extra_context": "对比目标不足两所学校，没法对比。",
            }

        # 图谱对比
        compare_ctx = ""
        try:
            compare_ctx = build_compare_context(targets)
        except Exception:
            pass

        # 分数对比
        province = profile.get("province", "")
        score_ctx = ""
        try:
            score_ctx = _format_score_rows(_query_scores(province, targets))
        except Exception:
            pass

        parts = []
        if compare_ctx:
            parts.append(compare_ctx)
        if score_ctx:
            parts.append(score_ctx)
        if not parts:
            parts.append("目前数据库中没有这几所学校的详细对比信息。")

        return {
            "extra_context": "【学校对比】\n" + "\n\n".join(parts),
        }
    except Exception:
        # ponytail: 异常详情仅写日志，不透传 str(e) 到 extra_context（会进 generate prompt，可能含 SQL/路径）。
        log.exception("compare_node failed")
        return {"extra_context": ""}
