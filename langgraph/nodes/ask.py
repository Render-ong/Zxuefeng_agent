"""ask 节点 — 按缺失信息槽生成张雪峰风格追问"""
import json
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "GraphRAG"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llm_config import get_api_config
from llm_client import call_llm as _call_llm

log = logging.getLogger("lg_agent.ask")


ASK_PROMPT = """你是张雪峰本人，资深高考志愿规划师，东北口音，拍桌子讲大实话，不说"作为AI"。

用户还没给全信息，你得像老父亲一样催他把话说全——不然没法给他算位次、推学校。

【已收集信息】
{profile_str}

【需要追问】
{questions_text}

【语气要求】
1. 用张雪峰标志性语气："我跟你讲""听我一句劝""这事儿咱得说道说道""你别跟我整那些虚的""我跟你说实话"
2. 先接用户的话，再自然过渡到追问，别像查户口
3. 一次最多问2个，简短有力，每条50字以内
4. 禁用"请问""麻烦您"这种客气话
5. 最后加一个引导，让用户继续回答

直接说："""


def _format_profile(profile: dict) -> str:
    parts = []
    if profile.get("province"):
        parts.append(f"省份: {profile['province']}")
    if profile.get("subject"):
        parts.append(f"选科: {profile['subject']}")
    if profile.get("rank"):
        parts.append(f"位次: {profile['rank']}")
    if profile.get("score"):
        parts.append(f"分数: {profile['score']}")
    if profile.get("majors"):
        parts.append(f"意向专业: {', '.join(profile['majors'])}")
    if profile.get("career_goal"):
        parts.append(f"就业目标: {profile['career_goal']}")
    if profile.get("family_bg"):
        parts.append(f"家庭背景: {profile['family_bg']}")
    if profile.get("compare_targets"):
        parts.append(f"对比目标: {', '.join(profile['compare_targets'])}")
    return "\n".join(parts) if parts else "暂无信息"


def _build_rule_reply(questions: list) -> str:
    """规则兜底：拼一段张雪峰风格的追问"""
    if not questions:
        return "还有啥想补充的？"

    lines = []
    for q in questions:
        if "哪个省" in q:
            lines.append("你哪个省的？先把这个说清楚，不然没法给你算位次。")
        elif "物理还是历史" in q:
            lines.append("选科是物理还是历史？这直接决定你能报哪些专业。")
        elif "位次" in q:
            lines.append("位次多少？有全省排名说排名，没排名说分数。")
        elif "专业" in q and "排斥" in q:
            lines.append("想学什么专业？有明确不想学的也告诉我，我帮你避开。")
        elif "找工作还是考公" in q:
            lines.append("毕业想找工作还是考公？或者继续考研？这个很重要。")
        elif "对比哪两个学校" in q:
            lines.append("你想对比哪两个学校？把名字给我，我帮你分析。")
        elif "了解哪个专业" in q:
            lines.append("你想了解哪个专业或学校？")
        else:
            lines.append(q)

    return "\n".join(lines)


def ask_node(state: dict) -> dict:
    """生成追问回复"""
    questions = state.get("ask_questions", [])
    profile = state.get("profile", {})
    _global = get_api_config()
    _req = state.get("api_config") or {}
    api_config = {**_global, **{k: v for k, v in _req.items() if v}}

    if not questions:
        return {"reply": "还有啥想补充的？"}

    profile_str = _format_profile(profile)
    questions_text = "\n".join(f"- {q}" for q in questions)

    if not api_config.get("key"):
        return {"reply": _build_rule_reply(questions)}

    messages = [
        {"role": "system", "content": ASK_PROMPT.format(profile_str=profile_str, questions_text=questions_text)},
        {"role": "user", "content": state.get("user_message", "")},
    ]

    try:
        reply = _call_llm(api_config, messages, temperature=0.8, max_tokens=300)
    except Exception:
        log.warning('LLM failed, using rule fallback', exc_info=True)
        reply = _build_rule_reply(questions)

    log.info(f'ask reply="{reply[:40]}…"')
    return {"reply": reply}
