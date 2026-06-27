#!/usr/bin/env python3

"""构建 GraphRAG 社区摘要 + 向量索引（标签/区域种子 + Louvain 图发现）"""

import json

import os

import sqlite3

import sys

import argparse



HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, HERE)

os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')



GRAPH_PATH = os.path.join(HERE, 'storage', 'graph.db')

OUT_JSON = os.path.join(HERE, 'storage', 'communities.json')

OUT_NPZ = os.path.join(HERE, 'storage', 'communities.npz')

CONFIG_PATH = os.path.join(HERE, 'config', 'retrieval.yaml')





def _load_ollama_cfg():

    cfg = {'model': 'qwen2.5:7b-instruct-q4_K_M', 'url': 'http://127.0.0.1:11434/api/generate'}

    if os.path.exists(CONFIG_PATH):

        try:

            import yaml

            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:

                raw = yaml.safe_load(f) or {}

            if raw.get('ollama'):

                cfg.update(raw['ollama'])

        except Exception:

            pass

    return cfg





def _template_summary(name, schools, extra=''):

    sample = '、'.join(schools[:12])

    if len(schools) > 12:

        sample += f'等共{len(schools)}所'

    return f'【{name}】{extra}代表院校包括：{sample}。适合关注该类院校的考生对比地理位置、王牌专业和录取位次。'


def _make_summary(name, schools, extra, use_ollama, ollama_cfg):
    summary = _template_summary(name, schools, extra=extra)
    summary_source = 'template'
    if use_ollama:
        ollama = _ollama_summarize(name, schools, ollama_cfg)
        if ollama:
            summary = ollama
            summary_source = 'ollama'
    return summary, summary_source


def _ollama_summarize(name, schools, cfg):
    try:
        import urllib.request
        print(f'[build_communities] ollama → {name}...', flush=True)
        prompt = (
            f'你是高考志愿顾问。用3-4句话总结「{name}」高校群体特点，'
            f'包括：{", ".join(schools[:10])}。不要编造具体分数。'
        )

        payload = json.dumps({

            'model': cfg.get('model', 'qwen2.5:7b-instruct-q4_K_M'),

            'prompt': prompt,

            'stream': False,

        }).encode('utf-8')

        req = urllib.request.Request(

            cfg.get('url', 'http://127.0.0.1:11434/api/generate'),

            data=payload,

            headers={'Content-Type': 'application/json'},

        )

        with urllib.request.urlopen(req, timeout=120) as resp:

            data = json.loads(resp.read().decode())

        text = (data.get('response') or '').strip()
        if len(text) > 20:
            print(f'[build_communities] ollama ok {name} ({len(text)} chars)', flush=True)
            return text
        return None

    except Exception as e:

        print(f'[build_communities] ollama skip ({name}): {e}')

        return None





def _schools_with_tag(conn, tag):

    rows = conn.execute(

        "SELECT n.name FROM nodes n "

        "JOIN edges e ON e.src = n.id AND e.rel = 'HAS_TAG' "

        "JOIN nodes t ON t.id = e.dst AND t.type = 'tag' AND t.name = ? "

        "WHERE n.type = 'school'",

        (tag,),

    ).fetchall()

    return sorted({r[0] for r in rows})





def _schools_in_region(conn, region):

    rows = conn.execute(

        "SELECT DISTINCT sn.name FROM nodes sn "

        "JOIN edges e ON e.src = sn.id AND e.rel = 'LOCATED_IN' "

        "JOIN nodes p ON p.id = e.dst AND p.type = 'province' "

        "JOIN edges e2 ON e2.src = p.id AND e2.rel = 'IN_REGION' "

        "JOIN nodes r ON r.id = e2.dst AND r.type = 'region' AND r.name = ? "

        "WHERE sn.type = 'school'",

        (region,),

    ).fetchall()

    return sorted({r[0] for r in rows})





def _discover_louvain_communities(conn, min_size=4, max_communities=20):

    """在标签院校子图上做 Louvain 社区发现"""

    try:

        import networkx as nx

    except ImportError:

        print('[build_communities] networkx not installed, skip discover')

        return []



    tagged = set()

    for tag in ('C9', '985', '211', '两电一邮', '热门'):

        tagged.update(_schools_with_tag(conn, tag))

    if len(tagged) < min_size:

        return []



    # 学校 → 标签、省份

    tag_map = {}

    for tag in ('C9', '985', '211', '两电一邮', '热门'):

        for s in _schools_with_tag(conn, tag):

            tag_map.setdefault(s, set()).add(tag)



    prov_rows = conn.execute(

        "SELECT sn.name, p.name FROM nodes sn "

        "JOIN edges e ON e.src = sn.id AND e.rel = 'LOCATED_IN' "

        "JOIN nodes p ON p.id = e.dst AND p.type = 'province' "

        "WHERE sn.type = 'school'"

    ).fetchall()

    prov_map = {r[0]: r[1] for r in prov_rows}



    G = nx.Graph()

    schools = sorted(tagged)

    G.add_nodes_from(schools)



    # 同标签连边（限制度数避免完全图爆炸）

    by_tag = {}

    for s in schools:

        for t in tag_map.get(s, []):

            by_tag.setdefault(t, []).append(s)

    for tag, group in by_tag.items():

        for i, s1 in enumerate(group):

            for s2 in group[i + 1: i + 4]:

                G.add_edge(s1, s2, weight=2, kind='tag', tag=tag)



    # 同省连边

    by_prov = {}

    for s in schools:

        p = prov_map.get(s)

        if p:

            by_prov.setdefault(p, []).append(s)

    for prov, group in by_prov.items():

        for i, s1 in enumerate(group):

            for s2 in group[i + 1: i + 3]:

                if not G.has_edge(s1, s2):

                    G.add_edge(s1, s2, weight=1, kind='province', province=prov)



    if G.number_of_edges() == 0:

        return []



    parts = nx.community.louvain_communities(G, weight='weight', seed=42)

    out = []

    for i, part in enumerate(sorted(parts, key=len, reverse=True)):

        if len(part) < min_size:

            continue

        schools_list = sorted(part)

        # 推断社区名：最多标签 + 最多省份

        tag_cnt = {}

        prov_cnt = {}

        for s in schools_list:

            for t in tag_map.get(s, []):

                tag_cnt[t] = tag_cnt.get(t, 0) + 1

            p = prov_map.get(s)

            if p:

                prov_cnt[p] = prov_cnt.get(p, 0) + 1

        top_tag = max(tag_cnt, key=tag_cnt.get) if tag_cnt else ''

        top_prov = max(prov_cnt, key=prov_cnt.get) if prov_cnt else ''

        if top_tag and top_prov:

            name = f'{top_tag}·{top_prov}高校群'

        elif top_tag:

            name = f'{top_tag}关联高校群'

        else:

            name = f'图社区{i + 1}'

        out.append({

            'id': f'louvain_{i}',

            'name': name,

            'schools': schools_list,

            'extra': f'图结构发现（{len(schools_list)}所）。',

        })

        if len(out) >= max_communities:

            break

    print(f'[build_communities] louvain discovered {len(out)} communities from {len(schools)} tagged schools')

    return out





def build(use_ollama=False, discover=False):

    if not os.path.exists(GRAPH_PATH):

        raise FileNotFoundError('graph.db not found, run build_graph.py first')



    ollama_cfg = _load_ollama_cfg()

    conn = sqlite3.connect(GRAPH_PATH)

    communities = []

    seen_ids = set()



    def _add(comm):

        if comm['id'] in seen_ids:

            return

        seen_ids.add(comm['id'])

        communities.append(comm)



    for tag in ('C9', '985', '211', '两电一邮', '热门'):

        schools = _schools_with_tag(conn, tag)

        if not schools:

            continue

        name = f'{tag}高校群体'
        summary, summary_source = _make_summary(
            name, schools, '国内重点院校标签。', use_ollama, ollama_cfg,
        )
        _add({
            'id': f'tag_{tag}',
            'name': name,
            'summary': summary,
            'schools': schools[:30],
            'source': 'seed_tag',
            'summary_source': summary_source,
        })



    for region in ('东北', '江浙沪', '京津冀', '华南', '西北', '西南'):

        schools = _schools_in_region(conn, region)

        if len(schools) < 3:

            continue

        name = f'{region}地区高校'
        summary, summary_source = _make_summary(
            name, schools, f'位于{region}区域。', use_ollama, ollama_cfg,
        )
        _add({
            'id': f'region_{region}',
            'name': name,
            'summary': summary,
            'schools': schools[:30],
            'source': 'seed_region',
            'summary_source': summary_source,
        })



    if discover:

        for raw in _discover_louvain_communities(conn):
            name = raw['name']
            schools = raw['schools']
            summary, summary_source = _make_summary(
                name, schools, raw.get('extra', ''), use_ollama, ollama_cfg,
            )
            _add({
                'id': raw['id'],
                'name': name,
                'summary': summary,
                'schools': schools[:30],
                'source': 'louvain',
                'summary_source': summary_source,
            })



    conn.close()



    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)

    with open(OUT_JSON, 'w', encoding='utf-8') as f:

        json.dump({'communities': communities}, f, ensure_ascii=False, indent=2)



    from engine import vector_store

    summaries = [c['summary'] for c in communities]

    embs = vector_store.encode_texts(summaries)

    import numpy as np

    np.savez_compressed(OUT_NPZ, embeddings=embs)



    if use_ollama:
        ollama_n = sum(1 for c in communities if c.get('summary_source') == 'ollama')
        print(f'[build_communities] ollama summaries: {ollama_n}/{len(communities)}')

    print(f'[build_communities] {len(communities)} communities → {OUT_JSON}')

    return len(communities)





if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument('--ollama', action='store_true', help='use Ollama for summaries')

    parser.add_argument('--discover', action='store_true', help='Louvain graph community discovery')

    args = parser.parse_args()

    build(use_ollama=args.ollama, discover=args.discover)

