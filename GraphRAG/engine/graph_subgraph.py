"""图谱子图查询 — 学校邻域、对比分析"""
import json
import sqlite3

from engine.graph_store import GRAPH_PATH, is_ready, lookup_school, get_school_tags, get_school_province, _normalize_school

_cache = {}


def _conn():
    if not is_ready():
        return None
    return sqlite3.connect(GRAPH_PATH)


def _schools_with_tag(conn, tag, limit=15):
    rows = conn.execute(
        "SELECT n.name FROM nodes n "
        "JOIN edges e ON e.src = n.id AND e.rel = 'HAS_TAG' "
        "JOIN nodes t ON t.id = e.dst AND t.type = 'tag' AND t.name = ? "
        "WHERE n.type = 'school' LIMIT ?",
        (tag, limit),
    ).fetchall()
    return [r[0] for r in rows]


def _schools_in_province(conn, province, exclude='', limit=10):
    rows = conn.execute(
        "SELECT DISTINCT sn.name FROM nodes sn "
        "JOIN edges e ON e.src = sn.id AND e.rel = 'LOCATED_IN' "
        "JOIN nodes p ON p.id = e.dst AND p.type = 'province' AND p.name = ? "
        "WHERE sn.type = 'school' AND sn.name != ? LIMIT ?",
        (province, exclude, limit),
    ).fetchall()
    return [r[0] for r in rows]


def school_profile(school_name):
    """单个学校的图谱画像"""
    if not is_ready():
        return {}
    base = _normalize_school(school_name)
    info = lookup_school(base)
    tags = info.get('tags') or []
    prov = info.get('province') or ''

    peers_tag, peers_prov = [], []
    conn = _conn()
    if conn:
        for tag in tags[:2]:
            peers = [s for s in _schools_with_tag(conn, tag, 8) if s != base]
            if peers:
                peers_tag.extend(peers[:5])
        if prov:
            peers_prov = _schools_in_province(conn, prov, exclude=base, limit=6)
        conn.close()

    return {
        'school': base,
        'province': prov,
        'tags': tags,
        'peers_same_tag': list(dict.fromkeys(peers_tag))[:5],
        'peers_same_province': peers_prov[:5],
    }


def format_profile(profile):
    if not profile:
        return ''
    lines = [
        f"· {profile['school']}：所在地{profile.get('province') or '未知'}，"
        f"标签{'/'.join(profile.get('tags') or []) or '无'}"
    ]
    if profile.get('peers_same_tag'):
        lines.append(f"  同标签院校：{'、'.join(profile['peers_same_tag'][:5])}")
    if profile.get('peers_same_province'):
        lines.append(f"  同省院校：{'、'.join(profile['peers_same_province'][:5])}")
    return '\n'.join(lines)


def build_compare_context(school_names):
    """多校对比子图上下文"""
    profiles = [school_profile(s) for s in school_names[:4]]
    profiles = [p for p in profiles if p.get('school')]
    if len(profiles) < 2:
        return ''
    parts = ['【图谱·学校对比子图】']
    for p in profiles:
        parts.append(format_profile(p))
    # 标签交集
    if len(profiles) >= 2:
        tags0 = set(profiles[0].get('tags') or [])
        tags1 = set(profiles[1].get('tags') or [])
        shared = tags0 & tags1
        if shared:
            parts.append(f"· 共同标签：{'/'.join(shared)}")
        prov0, prov1 = profiles[0].get('province'), profiles[1].get('province')
        if prov0 and prov1:
            parts.append(f"· 地域：{profiles[0]['school']}在{prov0}，{profiles[1]['school']}在{prov1}")
    return '\n'.join(parts)


def find_community_schools(community_id):
    """从 communities.json 取学校列表"""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'storage', 'communities.json')
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for c in data.get('communities', []):
        if c.get('id') == community_id:
            return c.get('schools') or []
    return []
