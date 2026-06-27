"""analyze 节点 — 解析用户消息，更新考生画像，识别意图，判断信息槽是否已满"""
import logging
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "GraphRAG"))

from engine.extractor import extract_info, extract_schools_for_compare

log = logging.getLogger("lg_agent.analyze")

# ponytail: 每个模式词必须是"爸妈从事X"语境下的明确表述，不能是考生日常用词。
# 旧版"学校"会导致"推荐学校""哪个学校好"被误判为家庭背景=教育系统。
# 同理"医院"也可能误判（"我想学医去医院工作"），改为"在医院工作/上班"等明确表述。
FAMILY_PATTERNS = [
    (r"(普通工薪|工薪阶层|打工|普通家庭|没什么资源|没背景)", "普通工薪"),
    (r"(做生意|经商|开公司|老板|家里条件好|做生意的)", "做生意"),
    (r"(电力系统|电网|国家电网|供电局|电力局)", "电力系统"),
    (r"(铁路|铁路局|铁道)", "铁路系统"),
    (r"(在医院工作|在医院上班|医疗系统|卫生系统|爸妈.*医生|爸妈.*护士)", "医疗系统"),
    (r"(教育系统|在教育系统|爸妈.*老师|爸妈.*教师)", "教育系统"),
    (r"(公务员|体制内|政府|机关)", "体制内"),
    (r"(金融|银行|证券|基金)", "金融行业"),
]

CAREER_PATTERNS = [
    (r"(找工作|就业|好就业|挣钱|赚钱|高薪)", "找好工作"),
    (r"(考公|公务员|体制内|编制|铁饭碗)", "考公务员"),
    (r"(考研|读研|深造|学术|科研)", "考研深造"),
    (r"(稳定|安稳|稳当|混口饭吃)", "求稳定"),
    (r"(创业|当老板|自己干)", "创业"),
]

COMPARE_PAT = re.compile(r"怎么选|有什么区别|对比|哪个好|vs|VS|还是|纠结")
POLICY_PAT = re.compile(r"政策|志愿|填几个|什么模式|专业\+院校|专业组|选科|分科|平行志愿|顺序志愿")
EXPLAIN_PAT = re.compile(r"专业.*怎么样|专业.*坑|专业.*前景|就业|学什么|薪资|值不值|适合我吗|好不好")
MAJOR_REJ_PAT = re.compile(r"不想学|不要学|不学|排除|讨厌|反感|不能学")
MAJOR_NOPAT_PAT = re.compile(
    r"^没有$|^不确定$|^都行$|^随便$|^不知道$|^没想好$|^都可以$|^暂无$|^无$|^没偏好$|^没要求$"
    r"|^不晓得$|^不懂$|^没概念$|^先看看$|^再说$|^还没定$|^看看再说$|^不清楚$|^没方向$|^没特别想的$"
)
CHAT_PAT = re.compile(r"考研|专科|大专|高一|高二|未来|人生|怎么办|出路|迷茫")


def _extract_family(text: str) -> str:
    for pat, label in FAMILY_PATTERNS:
        if re.search(pat, text):
            return label
    return ""


def _extract_career(text: str) -> str:
    for pat, label in CAREER_PATTERNS:
        if re.search(pat, text):
            return label
    return ""


def _extract_majors_reject(text: str) -> list:
    """提取不想学的专业，例如：我不想学金融和生物"""
    if not MAJOR_REJ_PAT.search(text):
        return []

    # 简单规则：从"不学/不想学"后面截取常见专业词
    # ponytail: 硬编码常见专业词列表，后续应接入知识库/图谱
    common_majors = [
        "数学", "物理", "化学", "生物", "金融", "会计", "英语", "计算机",
        "医学", "临床", "法学", "汉语言", "工商管理", "市场营销", "行政管理",
        "机械", "电气", "土木", "建筑", "材料", "环境", "护理", "学前教育",
    ]
    rejects = [m for m in common_majors if m in text]
    return rejects


def _merge_profile(old: dict, new_info: dict) -> dict:
    """把新提取的信息合并到已有画像中，列表追加，标量覆盖"""
    merged = dict(old)

    scalar_keys = ["province", "subject", "family_bg", "career_goal", "city_limit", "major_no_pref"]
    for k in scalar_keys:
        if new_info.get(k):
            merged[k] = new_info[k]

    # rank/score 单独处理：只有明确提取到非零值时才覆盖
    for k in ("rank", "score"):
        if new_info.get(k):
            try:
                v = int(new_info[k])
                if v > 0:
                    merged[k] = v
            except (ValueError, TypeError):
                pass

    # subject 特殊处理：提取到"物理/历史"就覆盖
    if new_info.get("subject"):
        merged["subject"] = new_info["subject"]

    list_keys = ["majors", "majors_reject", "region_pref", "region_avoid", "tags", "schools", "compare_targets"]
    for k in list_keys:
        if new_info.get(k):
            merged[k] = list(dict.fromkeys((merged.get(k) or []) + new_info[k]))

    return merged


def _detect_intent(text: str, profile: dict) -> str:
    """识别用户意图。

    优先看当前消息内容，再 fallback 到已有画像。
    """
    text = text.lower()

    if COMPARE_PAT.search(text):
        return "compare"

    if POLICY_PAT.search(text):
        return "policy"

    if EXPLAIN_PAT.search(text):
        return "explain"

    if CHAT_PAT.search(text) and not (profile.get("province") and (profile.get("rank") or profile.get("score"))):
        return "chat"

    # 信息齐全后，默认走推荐
    if profile.get("province") and (profile.get("rank") or profile.get("score")):
        return "recommend"

    if profile.get("majors") or profile.get("province"):
        return "recommend"

    return "collect"


def _missing_slots(profile: dict, intent: str) -> list:
    """检查信息槽缺失情况，返回缺失槽位名称。

    ask_rounds >= 2 时停止追问可选槽（majors/career_goal/family_bg），
    避免用户反复模糊回复时陷入无限追问循环。
    """
    missing = []
    ask_rounds = profile.get("ask_rounds", 0)

    # 通用必填（即使追问超限也要问，否则推荐完全没法做）
    if not profile.get("province"):
        missing.append("province")
    if not profile.get("subject"):
        missing.append("subject")
    if not (profile.get("rank") or profile.get("score")):
        missing.append("rank_or_score")

    # recommend 额外需要
    if intent == "recommend":
        if not profile.get("majors") and not profile.get("major_no_pref"):
            missing.append("majors")
        if not profile.get("career_goal"):
            missing.append("career_goal")

    # compare 额外需要
    if intent == "compare":
        targets = profile.get("compare_targets", [])
        if len(targets) < 2:
            missing.append("compare_targets")

    # explain 额外需要
    if intent == "explain":
        if not (profile.get("majors") or profile.get("schools")):
            missing.append("explain_target")

    # P2: recommend 意图下，必填项都已填时，主动追问家庭背景（可选，但建议收集）
    if intent == "recommend" and not missing and not profile.get("family_bg"):
        missing.append("family_bg")

    # ponytail: 追问超过 1 轮后，可选槽不再阻塞推荐。
    # 用户连续模糊回复（"不知道""随便"）时，2 轮后用已有信息直接推荐。
    # 必填槽（province/subject/rank_or_score）始终保留，否则推荐无意义。
    if ask_rounds >= 2:
        optional = {"majors", "career_goal", "family_bg"}
        missing = [s for s in missing if s not in optional]

    return missing


SLOT_QUESTIONS = {
    "province": "你哪个省的？",
    "subject": "选科是物理还是历史？",
    "rank_or_score": "位次多少？或者考了多少分？",
    "majors": "想学什么专业？有明确不想学的没有？",
    "career_goal": "毕业想干什么？找工作还是考公？",
    "compare_targets": "你想对比哪两个学校？",
    "explain_target": "你想了解哪个专业或学校？",
    "family_bg": "家里是干什么的？爸妈在什么行业？",
}


def analyze_node(state: dict) -> dict:
    """主节点函数：解析 → 更新画像 → 识别意图 → 判断追问"""
    msg = state.get("user_message", "")
    profile = state.get("profile") or {}

    extracted = extract_info(msg)

    extra = {
        "family_bg": _extract_family(msg),
        "career_goal": _extract_career(msg),
        "majors_reject": _extract_majors_reject(msg),
    }
    if extra["family_bg"]:
        log.info(f'family_bg extracted: "{extra["family_bg"]}" from msg="{msg[:60]}"')
    # 用户明确表示无专业偏好时标记，避免反复追问同一问题
    if MAJOR_NOPAT_PAT.search(msg.strip()):
        extra["major_no_pref"] = True

    full_extracted = {**extracted, **extra}
    merged_profile = _merge_profile(profile, full_extracted)

    # 对比意图：提取学校
    compare_schools = extract_schools_for_compare(msg) or []
    if compare_schools:
        merged_profile["compare_targets"] = list(dict.fromkeys(
            merged_profile.get("compare_targets", []) + compare_schools
        ))

    intent = _detect_intent(msg, merged_profile)

    # 检查信息槽
    missing = _missing_slots(merged_profile, intent)
    ask_questions = [SLOT_QUESTIONS[s] for s in missing if s in SLOT_QUESTIONS]

    # 信息槽未满且不是闲聊类，就需要追问
    # policy/explain/chat 即使信息不全也先回答，避免过度追问
    needs_ask = len(ask_questions) > 0 and intent == "recommend"

    # ponytail: 追问轮次计数，每轮 +1，超 2 轮后 _missing_slots 会自动放行可选槽。
    if needs_ask:
        merged_profile["ask_rounds"] = merged_profile.get("ask_rounds", 0) + 1
    # 不是 recommend 意图时重置计数（避免切换意图后残留旧计数）
    elif intent != "recommend":
        merged_profile["ask_rounds"] = 0

    prov = merged_profile.get('province', '')
    rank = merged_profile.get('rank', 0)
    log.info(f'intent={intent} needs_ask={needs_ask} prov={prov} rank={rank} slots_missing={missing}')

    return {
        "profile": merged_profile,
        "intent": intent,
        "needs_ask": needs_ask,
        "ask_questions": ask_questions,
        # 新消息来了，清空上一轮上下文，避免污染本轮回复
        "recommend_result": None,
        "data_context": "",
        "graph_context": "",
        "web_context": "",
        "extra_context": "",
        "reply": "",
    }
