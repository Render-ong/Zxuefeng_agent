"""GraphRAG — 社区 Global Search + 学校 Local/对比 + 知识 RAG"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMUNITIES_JSON = os.path.join(HERE, 'storage', 'communities.json')
COMMUNITIES_NPZ = os.path.join(HERE, 'storage', 'communities.npz')
KNOWLEDGE_PATH = os.path.join(HERE, 'data', 'knowledge', 'chunks.json')
CONFIG_PATH = os.path.join(HERE, 'config', 'retrieval.yaml')

_cache = None
_cfg = None


def _load_cfg():
    global _cfg
    if _cfg is not None:
        return _cfg
    _cfg = {
        'global_threshold': 0.35,
        'knowledge_threshold': 0.42,
        'school_threshold': 0.45,
        'global_top_k': 3,
        'knowledge_top_k': 3,
        'school_top_k': 5,
    }
    if os.path.exists(CONFIG_PATH):
        try:
            import yaml
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                raw = yaml.safe_load(f) or {}
            if raw.get('graphrag'):
                _cfg.update(raw['graphrag'])
        except Exception:
            pass
    return _cfg


def is_ready():
    return os.path.exists(COMMUNITIES_JSON) and os.path.exists(COMMUNITIES_NPZ)


def index_info():
    info = {'ready': is_ready(), 'communities': 0, 'knowledge_index': False, 'school_index': False}
    if is_ready():
        with open(COMMUNITIES_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        info['communities'] = len(data.get('communities', []))
        sources = {}
        for c in data.get('communities', []):
            src = c.get('source', 'unknown')
            sources[src] = sources.get(src, 0) + 1
        info['community_sources'] = sources
    try:
        from engine import vector_index
        info['knowledge_index'] = vector_index.is_index_ready('knowledge')
        info['school_index'] = vector_index.is_index_ready('schools')
    except Exception:
        pass
    return info


def _load():
    global _cache
    if _cache is not None:
        return _cache
    if not is_ready():
        _cache = {'communities': [], 'embeddings': None}
        return _cache
    with open(COMMUNITIES_JSON, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    embs = np.load(COMMUNITIES_NPZ)['embeddings']
    _cache = {'communities': meta['communities'], 'embeddings': embs}
    return _cache


def reload():
    global _cache
    _cache = None
    from engine import vector_index
    vector_index._indices.clear()
    return _load()


def global_search(query, top_k=None):
    """按查询语义检索社区摘要"""
    cfg = _load_cfg()
    top_k = top_k or int(cfg.get('global_top_k', 3))
    threshold = float(cfg.get('global_threshold', 0.35))
    if not is_ready() or not query:
        return []
    from engine import vector_store
    if not vector_store.is_ready():
        return []
    c = _load()
    q_emb = vector_store.encode_texts([query])[0]
    sims = c['embeddings'] @ q_emb
    order = np.argsort(-sims)[:top_k]
    hits = []
    for i in order:
        if sims[i] < threshold:
            break
        comm = c['communities'][i]
        hits.append({
            'id': comm['id'],
            'name': comm['name'],
            'summary': comm['summary'],
            'schools': (comm.get('schools') or [])[:8],
            'score': float(sims[i]),
        })
    return hits


def school_search(query, top_k=None):
    """语义检索学校"""
    cfg = _load_cfg()
    top_k = top_k or int(cfg.get('school_top_k', 5))
    threshold = float(cfg.get('school_threshold', 0.45))
    if not query:
        return []
    try:
        from engine import vector_index
        if not vector_index.is_index_ready('schools'):
            return []
        hits = vector_index.search('schools', query, top_k=top_k, threshold=threshold)
        return [{'school': h.get('school'), 'province': h.get('province'), 'tags': h.get('tags'), 'score': h['score']} for h in hits]
    except Exception:
        return []


def local_search(schools):
    """查询提及学校的图谱信息（含邻域）"""
    from engine import graph_subgraph
    if not schools:
        return []
    lines = []
    for s in schools[:6]:
        block = graph_subgraph.format_profile(graph_subgraph.school_profile(s))
        if block:
            lines.append(block)
    return lines


def knowledge_search(query, top_k=None):
    """知识库检索：优先向量，回退关键词"""
    cfg = _load_cfg()
    top_k = top_k or int(cfg.get('knowledge_top_k', 3))
    threshold = float(cfg.get('knowledge_threshold', 0.42))

    try:
        from engine import vector_index
        if vector_index.is_index_ready('knowledge'):
            hits = vector_index.search('knowledge', query, top_k=top_k, threshold=threshold)
            if hits:
                return [h.get('snippet') or h.get('text', '') for h in hits]
    except Exception:
        pass

    if not os.path.exists(KNOWLEDGE_PATH):
        return []
    with open(KNOWLEDGE_PATH, 'r', encoding='utf-8') as f:
        chunks = json.load(f)
    scored = []
    for ch in chunks:
        score = sum(1 for tag in ch.get('tags', []) if tag in query)
        if any(kw in query for kw in ch.get('tags', [])):
            score += 1
        if score > 0:
            scored.append((score, ch['text']))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:top_k]]


def get_policy_context(province):
    """省份志愿政策（来自图谱）"""
    if not province:
        return ''
    import sqlite3
    from engine.graph_store import GRAPH_PATH, is_ready as graph_ready
    if not graph_ready():
        return ''
    conn = sqlite3.connect(GRAPH_PATH)
    row = conn.execute(
        "SELECT props FROM nodes WHERE type='policy' AND name LIKE ?",
        (f'%{province}%',),
    ).fetchone()
    conn.close()
    if not row:
        return ''
    try:
        props = json.loads(row[0])
        return f"{province}志愿模式：{props.get('mode', '')}，可填{props.get('quota', '?')}个志愿。"
    except Exception:
        return ''


def search(query, intent='general', params=None):
    """统一 GraphRAG 检索，供 API / 调试"""
    params = params or {}
    schools = list(params.get('schools') or [])
    if intent == 'compare':
        from engine.extractor import extract_schools_for_compare
        schools = extract_schools_for_compare(query) or schools

    if not schools:
        sem = school_search(query, top_k=3)
        schools = [h['school'] for h in sem if h.get('school')]

    result = {
        'global': global_search(query),
        'local': local_search(schools),
        'knowledge': knowledge_search(query),
        'schools_semantic': school_search(query),
    }
    if len(schools) >= 2:
        from engine import graph_subgraph
        result['compare'] = graph_subgraph.build_compare_context(schools)
    policy = get_policy_context(params.get('province', ''))
    if policy:
        result['policy'] = policy
    return result


def build_context(query, params, intent):
    """组装 GraphRAG + 知识库上下文"""
    parts = []
    cfg = _load_cfg()

    if intent in ('compare', 'recommend', 'policy', 'major_info', 'general'):
        hits = global_search(query, top_k=int(cfg.get('global_top_k', 3)))
        if hits:
            parts.append('【图谱·社区摘要】')
            for h in hits:
                parts.append(f"· [{h['name']}] {h['summary']}")

    schools = list(params.get('schools') or [])
    if intent == 'compare':
        from engine.extractor import extract_schools_for_compare
        schools = extract_schools_for_compare(query) or schools

    if not schools and intent in ('compare', 'major_info'):
        sem = school_search(query, top_k=3)
        schools = [h['school'] for h in sem if h.get('school')]

    if intent == 'compare' and len(schools) >= 2:
        from engine import graph_subgraph
        compare_block = graph_subgraph.build_compare_context(schools)
        if compare_block:
            parts.append(compare_block)
    else:
        local = local_search(schools)
        if local:
            parts.append('【图谱·学校信息】')
            parts.extend(local)

    policy = get_policy_context(params.get('province', ''))
    if policy and intent in ('recommend', 'policy'):
        parts.append('【图谱·省份政策】' + policy)

    knowledge = knowledge_search(query, top_k=int(cfg.get('knowledge_top_k', 2)))
    if knowledge:
        parts.append('【知识库·填报原则】')
        for k in knowledge:
            parts.append('· ' + k)

    return '\n'.join(parts)
