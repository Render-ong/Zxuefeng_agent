#!/usr/bin/env python3
"""学校语义索引 — 校名+地域+标签"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

GRAPH_PATH = os.path.join(HERE, 'storage', 'graph.db')


def _school_records(conn):
    rows = conn.execute(
        "SELECT n.name, p.name FROM nodes n "
        "LEFT JOIN edges e ON e.src = n.id AND e.rel = 'LOCATED_IN' "
        "LEFT JOIN nodes p ON p.id = e.dst AND p.type = 'province' "
        "WHERE n.type = 'school'"
    ).fetchall()
    school_prov = {r[0]: r[1] or '' for r in rows}

    tag_rows = conn.execute(
        "SELECT n.name, t.name FROM nodes n "
        "JOIN edges e ON e.src = n.id AND e.rel = 'HAS_TAG' "
        "JOIN nodes t ON t.id = e.dst AND t.type = 'tag'"
    ).fetchall()
    school_tags = {}
    for s, t in tag_rows:
        school_tags.setdefault(s, []).append(t)

    records = []
    for school, prov in school_prov.items():
        tags = '/'.join(sorted(school_tags.get(school, [])))
        text = f"{school} {prov} {tags}".strip()
        records.append({
            'id': school,
            'text': text,
            'school': school,
            'province': prov,
            'tags': tags,
        })
    return records


def build(limit=None):
    if not os.path.exists(GRAPH_PATH):
        raise FileNotFoundError('run build_graph.py first')
    conn = sqlite3.connect(GRAPH_PATH)
    records = _school_records(conn)
    conn.close()
    if limit:
        records = records[:limit]
    from engine import vector_index
    return vector_index.build_index('schools', records)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int, default=0)
    args = p.parse_args()
    build(limit=args.limit or None)
