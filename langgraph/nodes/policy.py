"""policy 节点 — 省份政策 / 选科建议解读"""
import json
import os
import sys
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "GraphRAG"))

from engine.graphrag import get_policy_context

log = logging.getLogger("lg_agent.policy")


POLICY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "GraphRAG", "data", "seeds", "province_policy.json"
)


def _load_province_policy(province: str) -> dict:
    if not province or not os.path.exists(POLICY_PATH):
        return {}
    try:
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(province, {})
    except Exception:
        return {}


def policy_node(state: dict) -> dict:
    """政策解读节点"""
    try:
        profile = state.get("profile", {})
        province = profile.get("province", "")
        subject = profile.get("subject", "")

        parts = []

        # 省份政策
        if province:
            graph_policy = ""
            try:
                graph_policy = get_policy_context(province)
            except Exception:
                pass

            file_policy = _load_province_policy(province)

            parts.append(f"· {province}志愿政策：")
            if graph_policy:
                parts.append(f"  - {graph_policy}")
            if file_policy:
                mode = file_policy.get("mode", "")
                quota = file_policy.get("quota", "")
                if mode:
                    parts.append(f"  - 志愿模式：{mode}")
                if quota:
                    parts.append(f"  - 可填志愿数：{quota}")
            if len(parts) == 1:
                parts.append("  - 暂无该省份详细政策数据")

        # 选科建议
        if subject:
            parts.append(f"· 选科为{subject}，建议关注对应科类招生计划和专业限制。")

        if not parts:
            parts.append("你哪个省的？想咨询什么政策？")

        return {
            "extra_context": "【政策解读】\n" + "\n".join(parts),
        }
    except Exception:
        log.exception("policy_node failed")
        return {"extra_context": ""}
