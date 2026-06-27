"""Prompt 拼装 — 将检索结果组织为 LLM 消息"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(HERE, 'data', 'seeds', 'province_policy.json')

PROMPT_GAOKAO = """现在是2026年6月，2026年高考已结束，志愿填报正在进行中。你是资深高考志愿规划师，风格直爽接地气。

【核心规则】
1. 数据使用铁律：DB数据逐条引用；联网数据标注"据网上公开信息，仅供参考"；没有数据的学校禁止编造分数位次。
2. 专业过滤：用户说了想学什么专业，只推荐对口专业；明确排斥的专业一律不提。
3. 冲稳保比例：冲20%稳50%保30%，保底至少3个。
4. 有自定义数据表时只推荐表内学校。

【回答结构】确认省份政策 → 冲 → 稳 → 保 → 补充建议（缺失关键信息时自然追问1-2个）。"""

PROMPT_FUN = """你是张雪峰本人，东北口音，拍桌子讲大实话，不说作为AI，不编造具体数据。"""


def _format_tier(name, rows):
    if not rows:
        return ''
    lines = [f'\n▎{name}']
    for d in rows[:10]:
        if d.get('school') == '【死命令】':
            continue
        lines.append(
            f"· {d.get('school', '')} {d.get('major', '')} "
            f"{d.get('year', '')}年 最低{d.get('score', '?')}分 位次{d.get('rank', '?')} [DB]"
        )
    return '\n'.join(lines) if len(lines) > 1 else ''


def format_recommend_result(result):
    """格式化 SQL 推荐结果为文本"""
    if not result or result.get('source') == 'custom_only':
        parts = ['【自定义数据·只准推荐下列学校】']
        for tier, label in [('chong', '冲'), ('wen', '稳'), ('bao', '保')]:
            block = _format_tier(label, result.get(tier, []))
            if block:
                parts.append(block)
        return '\n'.join(parts)

    parts = [f"【本地数据库·冲稳保】省份={result.get('province')} 位次={result.get('rank')} 科类={result.get('category') or '未指定'}"]
    if result.get('keyword_expanded'):
        parts.append(f"专业扩展：{result.get('keyword_original')} → {result.get('keyword_expanded')}")
    for tier, label in [('chong', '冲'), ('wen', '稳'), ('bao', '保')]:
        block = _format_tier(label, result.get(tier, []))
        if block:
            parts.append(block)
    if not any(result.get(t) for t in ('chong', 'wen', 'bao')):
        parts.append('(数据库暂无匹配结果)')
    return '\n'.join(parts)


def build_messages(mode, user_message, history, data_context='', graph_context='', web_context='', params=None):
    """构建 LLM messages 列表"""
    system = PROMPT_FUN if mode == 'fun' else PROMPT_GAOKAO
    messages = [{'role': 'system', 'content': system}]

    if params and mode == 'gaokao':
        profile = (
            f"【用户画像】省份={params.get('province') or '未知'} "
            f"科类={params.get('subject') or '未知'} "
            f"位次={params.get('rank') or '未知'} 分数={params.get('score') or '未知'} "
            f"专业={','.join(params.get('majors') or []) or '未指定'}"
        )
        if params.get('region_avoid'):
            profile += f" 排斥区域={','.join(params['region_avoid'])}"
        if params.get('region_pref'):
            profile += f" 偏好区域={','.join(params['region_pref'])}"
        messages.append({'role': 'system', 'content': profile})

    if data_context and mode == 'gaokao':
        messages.append({'role': 'system', 'content': data_context})
    if graph_context and mode == 'gaokao':
        messages.append({'role': 'system', 'content': graph_context})
    if web_context and mode == 'gaokao':
        messages.append({'role': 'system', 'content': web_context})

    for msg in (history or [])[-20:]:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})

    if not history or history[-1].get('content') != user_message:
        messages.append({'role': 'user', 'content': user_message})

    return messages
