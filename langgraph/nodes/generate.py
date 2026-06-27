"""generate 节点 — 张雪峰风格回复生成"""
import json
import os
import sys
import time
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llm_config import get_api_config
from llm_client import call_llm as _call_llm

log = logging.getLogger("lg_agent.generate")

SYSTEM_PROMPT = """你是张雪峰本人，资深高考志愿规划师，东北口音，拍桌子讲大实话，不说"作为AI"。

【核心风格】
1. 直来直去，不绕弯子，该劝退就劝退
2. 用老百姓听得懂的话，别整那些专业术语
3. 普通家庭的孩子，优先保就业，别瞎搞那些虚的
4. 家里有资源的，顺着资源走，别放着现成的路不走
5. 冲稳保说清楚，别模棱两可

【张雪峰语气·参考】
- 口头禅："我跟你讲""听我一句劝""这事儿咱得说道说道""你别跟我整那些虚的""我跟你说实话"
- 劝退句式："这专业咱别报""家里没矿别碰""就业面太窄，慎重"
- 推荐句式："这学校我跟你讲，性价比高""位次卡在这，稳""保底必须留，别赌"

【数据铁律】
- 数据库里的学校和分数可以直接说
- 网上搜的信息必须标注"据网上公开信息，仅供参考"
- 没有数据的学校和专业，别瞎编分数和位次

【回答结构】
1. 先接用户的话，给个总体判断
2. 冲稳保分开说（冲20%、稳50%、保30%）
3. 解释专业时要说清楚就业方向和坑点
4. 对比学校时要直接说哪个更合适
5. 最后给一句掏心窝子的话
6. 信息不够就自然追问，别像查户口

【最重要的规则】
- 每次回复的最后一句话，必须是一个开放式问题，引导用户继续对话
- 推荐后问："这几个学校你有想细聊的吗？"或"还有啥纠结的？"
- 解读后问："这个专业你能接受吗？"或"你数学/物理怎么样？"
- 对比后问："这两个你倾向哪个？"或"还有别的选项吗？"
- 闲聊/政策后问："还有啥想问的？"或"你想让我帮你推荐学校吗？"

现在开始："""


def generate_node(state: dict) -> dict:
    """组织所有上下文，生成最终回复"""
    # ponytail: 优先用前端透传的 per-request config，fallback 到后端全局配置。
    _global = get_api_config()
    _req = state.get("api_config") or {}
    api_config = {**_global, **{k: v for k, v in _req.items() if v}}
    mode = state.get("mode", "gaokao")

    if not api_config.get("key"):
        return {"reply": "请先配置 API Key"}

    data_ctx = state.get("data_context", "")
    graph_ctx = state.get("graph_context", "")
    web_ctx = state.get("web_context", "")
    extra_ctx = state.get("extra_context", "")
    profile = state.get("profile", {})

    profile_text = _format_profile(profile)
    context_text = _build_context_text(data_ctx, graph_ctx, web_ctx, extra_ctx)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"【考生画像】\n{profile_text}"},
    ]

    if context_text:
        messages.append({"role": "system", "content": f"【参考资料】\n{context_text}"})

    # ponytail: 多轮历史从 DB 取（checkpoint 不存消息，state.messages 始终为空）。
    # agent.run 在 invoke 前已把当前用户消息存入 DB，故 history 末尾即当前消息，无需再追加。
    try:
        from user_manager import get_history_messages
        history = get_history_messages(state.get("user_id", ""), state.get("conversation_id", ""), limit=20)
    except Exception:
        log.exception("load history failed")
        history = []

    for msg in (history or [])[-20:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    user_msg = state.get("user_message", "")
    if not history or history[-1].get("content") != user_msg:
        messages.append({"role": "user", "content": user_msg})

    try:
        t0 = time.time()
        reply = _call_llm(api_config, messages)
        log.info(f'llm ok {int((time.time()-t0)*1000)}ms reply={len(reply)} chars')
    except Exception:
        # ponytail: LLM 调用失败不透传 str(e) 给用户（可能含 URL/key 片段），仅写日志。
        log.exception("generate _call_llm failed")
        reply = "服务繁忙，请稍后重试"

    return {"reply": reply}


def _format_profile(profile: dict) -> str:
    parts = []
    if profile.get("province"):
        parts.append(f"省份：{profile['province']}")
    if profile.get("subject"):
        parts.append(f"选科：{profile['subject']}")
    if profile.get("rank"):
        parts.append(f"位次：{profile['rank']}")
    if profile.get("score"):
        parts.append(f"分数：{profile['score']}")
    if profile.get("majors"):
        parts.append(f"意向专业：{', '.join(profile['majors'])}")
    if profile.get("majors_reject"):
        parts.append(f"排斥专业：{', '.join(profile['majors_reject'])}")
    if profile.get("region_pref"):
        parts.append(f"偏好地域：{', '.join(profile['region_pref'])}")
    if profile.get("region_avoid"):
        parts.append(f"排斥地域：{', '.join(profile['region_avoid'])}")
    if profile.get("tags"):
        parts.append(f"标签偏好：{', '.join(profile['tags'])}")
    if profile.get("family_bg"):
        parts.append(f"家庭背景：{profile['family_bg']}")
    if profile.get("career_goal"):
        parts.append(f"就业目标：{profile['career_goal']}")
    return "\n".join(parts) if parts else "信息不完整"


def _build_context_text(data_ctx, graph_ctx, web_ctx, extra_ctx) -> str:
    # ponytail: 给每段 context 加来源前缀，让 LLM 能区分数据库/图谱/网搜，
    # 配合 SYSTEM_PROMPT 的"网搜必须标注仅供参考"规则才生效。
    # 不改 state 字段结构（字段层已分清），只在拼 prompt 时加分段标记。
    parts = []
    if data_ctx:
        parts.append("【数据库·可信】\n" + data_ctx)
    if graph_ctx:
        parts.append("【知识图谱】\n" + graph_ctx)
    if web_ctx:
        parts.append("【联网搜索·仅供参考】\n" + web_ctx)
    if extra_ctx:
        parts.append("【扩展上下文】\n" + extra_ctx)
    return "\n\n".join(parts)

