"""explain 节点 — 专业/学校解读"""
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "GraphRAG"))

from engine import graphrag
from engine.graph_store import lookup_school

log = logging.getLogger("lg_agent.explain")


# ponytail: 简单劝退规则，后续可扩展为知识库/图谱查询
WARNING_MAJORS = {
    "金融": ["普通家庭没资源慎选", "就业分化严重"],
    "工商管理": ["假大空专业", "毕业不好找工作"],
    "市场营销": ["专科本科都在教", "门槛低竞争大"],
    "行政管理": ["考公还行", "私企就业差"],
    "生物": ["本科就业难", "想干这行必须读研"],
    "化学": ["化工厂环境一般", "本科出路窄"],
    "环境": ["四大天坑之一", "谨慎选择"],
    "材料": ["四大天坑之一", "除非去好学校"],
    "英语": ["考公岗位少", "除教师外就业窄"],
}

GOOD_MAJORS = {
    "计算机": ["就业面广", "薪资高", "但要卷"],
    "电气": ["进电网稳", "原电力部院校更吃香"],
    "电子信息": ["硬件软件都能干", "就业不错"],
    "临床医学": ["越老越吃香", "但要读很多年"],
    "口腔": ["收入高", "可以自己开诊所"],
    "汉语言": ["考公大户", "当老师也方便"],
    "法学": ["考公岗位多", "但要过法考"],
    "护理": ["好就业", "工作稳定但累"],
}


def _major_warnings(major: str) -> list:
    for key, reasons in WARNING_MAJORS.items():
        if key in major:
            return reasons
    return []


def _major_pros(major: str) -> list:
    for key, reasons in GOOD_MAJORS.items():
        if key in major:
            return reasons
    return []


def _school_explain(school_name: str) -> str:
    info = lookup_school(school_name)
    parts = [f"· {school_name}解读："]
    if info.get("province"):
        parts.append(f"  - 所在地：{info['province']}")
    if info.get("tags"):
        parts.append(f"  - 标签：{'/'.join(info['tags'])}")
    if len(parts) == 1:
        parts.append("  - 暂无详细数据")
    return "\n".join(parts)


def explain_node(state: dict) -> dict:
    """专业/学校解读节点"""
    try:
        profile = state.get("profile", {})
        user_msg = state.get("user_message", "")

        majors = profile.get("majors", [])
        schools = profile.get("schools", [])

        parts = []

        # 专业解读
        if majors:
            for major in majors[:3]:
                warnings = _major_warnings(major)
                pros = _major_pros(major)
                lines = [f"· {major}专业解读："]
                if warnings:
                    lines.append(f"  ⚠ 注意：{'；'.join(warnings)}")
                if pros:
                    lines.append(f"  ✓ 优势：{'；'.join(pros)}")
                if not warnings and not pros:
                    lines.append("  - 普通专业，就业看学校层次和个人能力")
                parts.append("\n".join(lines))

        # 学校解读
        if schools:
            for school in schools[:2]:
                try:
                    parts.append(_school_explain(school))
                except Exception:
                    pass

        # 知识库补充
        if graphrag.is_ready():
            try:
                knowledge = graphrag.knowledge_search(user_msg, top_k=2)
                if knowledge:
                    parts.append("· 相关知识：\n" + "\n".join(f"  - {k}" for k in knowledge))
            except Exception:
                pass

        if not parts:
            parts.append("你想了解哪个专业或学校？把名字告诉我。")

        return {
            "extra_context": "【专业/学校解读】\n" + "\n\n".join(parts),
        }
    except Exception:
        log.exception("explain_node failed")
        return {"extra_context": ""}
