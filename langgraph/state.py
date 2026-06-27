"""AgentState — 张雪峰志愿填报 Agent 的共享状态"""
from typing import TypedDict, Annotated
import operator


SLOT_KEYS = [
    "province", "subject", "score", "rank", "majors", "majors_reject",
    "region_pref", "region_avoid", "tags", "family_bg", "career_goal",
    "city_limit", "schools", "compare_targets",
]


def _default_profile() -> dict:
    """初始化考生画像，rank/score 为 0，列表为空列表，其余为空字符串"""
    result = {}
    for k in SLOT_KEYS:
        if k in ("score", "rank"):
            result[k] = 0
        elif k.endswith("s") and k not in ("subject",):
            result[k] = []
        else:
            result[k] = ""
    return result


# ponytail: 简单合并策略——新值覆盖旧值，列表去重追加。
# 更复杂的槽位校验在 analyze_node 中处理。
def merge_profile(left: dict, right: dict) -> dict:
    """合并两个 profile，right 非空值覆盖/追加到 left"""
    merged = dict(left)
    for k, v in right.items():
        if v is None or v == "" or v == []:
            continue
        if k in ("majors", "majors_reject", "region_pref", "region_avoid",
                 "tags", "schools", "compare_targets"):
            old = merged.get(k, [])
            merged[k] = list(dict.fromkeys(old + v))
        else:
            merged[k] = v
    return merged


class AgentState(TypedDict):
    messages: Annotated[list, operator.add]
    user_message: str
    mode: str  # "gaokao" | "fun"

    # 用户身份与对话标识：节点内可用于鉴权/日志/配额。
    user_id: str
    conversation_id: str

    # ponytail: per-request API 配置，前端透传，不写入全局文件。
    # 节点内优先用此值，fallback 到 get_api_config()。
    # 升级路径：正式运营后 key 存 DB，不再通过 state 传递。
    api_config: dict

    profile: Annotated[dict, merge_profile]  # 考生画像，逐步收集/合并
    intent: str    # collect / recommend / compare / explain / policy / chat
    needs_ask: bool
    ask_questions: list

    recommend_result: dict | None
    data_context: str
    graph_context: str
    web_context: str
    extra_context: str  # 对比/解释/政策等附加上下文

    reply: str
