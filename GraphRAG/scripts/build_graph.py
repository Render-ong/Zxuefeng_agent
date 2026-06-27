#!/usr/bin/env python3
"""从投档数据库 + 种子表构建知识图谱 graph.db"""
import csv
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

DB_PATH = os.path.join(HERE, 'admission_clean.db')
GRAPH_PATH = os.path.join(HERE, 'storage', 'graph.db')
SEEDS = os.path.join(HERE, 'data', 'seeds')

PROVINCES = [
    '北京', '天津', '上海', '重庆', '河北', '山西', '辽宁', '吉林', '黑龙江',
    '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南',
    '广东', '广西', '海南', '四川', '贵州', '云南', '西藏', '陕西', '甘肃',
    '青海', '宁夏', '新疆', '内蒙古',
]

_SPECIAL_SCHOOL_PROVINCE = {
    '东北大学': '辽宁',
    '东北财经大学': '辽宁',
    '东北师范大学': '吉林',
    '东华大学': '上海',
}


def nid(ntype, name):
    return f'{ntype}:{name}'


def _normalize_school(name):
    return re.sub(r'[\(（].*?[\)）]', '', str(name)).strip()


def _infer_province(school_name, known_map):
    base = _normalize_school(school_name)
    if base in known_map:
        return known_map[base]
    if base in _SPECIAL_SCHOOL_PROVINCE:
        return _SPECIAL_SCHOOL_PROVINCE[base]
    for p in sorted(PROVINCES, key=len, reverse=True):
        if base.startswith(p) and len(base) > len(p):
            return p
    for known, prov in known_map.items():
        if base.startswith(known) or known.startswith(base):
            return prov
    return ''


class GraphBuilder:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute('DROP TABLE IF EXISTS edges')
        self.conn.execute('DROP TABLE IF EXISTS nodes')
        self.conn.execute('''
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                props TEXT
            )
        ''')
        self.conn.execute('''
            CREATE TABLE edges (
                src TEXT NOT NULL,
                dst TEXT NOT NULL,
                rel TEXT NOT NULL,
                weight REAL DEFAULT 1,
                PRIMARY KEY (src, dst, rel)
            )
        ''')
        self.conn.execute('CREATE INDEX idx_nodes_type ON nodes(type)')
        self.conn.execute('CREATE INDEX idx_nodes_name ON nodes(name)')
        self.conn.execute('CREATE INDEX idx_edges_src ON edges(src)')
        self.conn.execute('CREATE INDEX idx_edges_dst ON edges(dst)')
        self._nodes = set()

    def add_node(self, ntype, name, props=None):
        if not name:
            return
        node_id = nid(ntype, name)
        if node_id in self._nodes:
            return
        self._nodes.add(node_id)
        self.conn.execute(
            'INSERT OR IGNORE INTO nodes(id, type, name, props) VALUES (?, ?, ?, ?)',
            (node_id, ntype, name, json.dumps(props or {}, ensure_ascii=False)),
        )

    def add_edge(self, src_type, src_name, rel, dst_type, dst_name, weight=1.0):
        if not src_name or not dst_name:
            return
        self.add_node(src_type, src_name)
        self.add_node(dst_type, dst_name)
        self.conn.execute(
            'INSERT OR IGNORE INTO edges(src, dst, rel, weight) VALUES (?, ?, ?, ?)',
            (nid(src_type, src_name), nid(dst_type, dst_name), rel, weight),
        )

    def commit(self):
        self.conn.commit()
        self.conn.close()


def load_csv(name):
    path = os.path.join(SEEDS, name)
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def build():
    g = GraphBuilder(GRAPH_PATH)
    school_location = {}

    # 省份节点
    for p in PROVINCES:
        g.add_node('province', p)

    # 区域 → 省份
    for row in load_csv('region_map.csv'):
        region = row['region'].strip()
        prov = row['province'].strip()
        g.add_node('region', region)
        g.add_edge('province', prov, 'IN_REGION', 'region', region)

    # 学校 → 城市 → 省份
    for row in load_csv('school_city.csv'):
        school = row['school'].strip()
        city = row['city'].strip()
        prov = row['province'].strip()
        g.add_node('city', city, {'province': prov})
        g.add_edge('school', school, 'LOCATED_IN', 'city', city)
        g.add_edge('city', city, 'LOCATED_IN', 'province', prov)
        school_location[school] = prov

    # 学校标签
    for row in load_csv('school_tags.csv'):
        school = row['school'].strip()
        tag = row['tag'].strip()
        g.add_node('tag', tag)
        g.add_edge('school', school, 'HAS_TAG', 'tag', tag)

    # 志愿政策
    policy_path = os.path.join(SEEDS, 'province_policy.json')
    if os.path.exists(policy_path):
        with open(policy_path, 'r', encoding='utf-8') as f:
            policies = json.load(f)
        for prov, info in policies.items():
            pname = f'{prov}志愿填报规则'
            g.add_node('policy', pname, info)
            g.add_edge('province', prov, 'GOVERNED_BY', 'policy', pname)

    # 从投档库抽取学校并推断所在地
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        schools = conn.execute(
            'SELECT DISTINCT school_name FROM admission WHERE school_name IS NOT NULL'
        ).fetchall()
        majors = conn.execute(
            'SELECT DISTINCT major_name FROM admission WHERE major_name IS NOT NULL LIMIT 5000'
        ).fetchall()
        conn.close()

        inferred = 0
        for (school,) in schools:
            school = school.strip()
            if not school or len(school) < 3:
                continue
            base = _normalize_school(school)
            g.add_node('school', base)
            if base not in school_location:
                prov = _infer_province(base, school_location)
                if prov:
                    g.add_edge('school', base, 'LOCATED_IN', 'province', prov)
                    school_location[base] = prov
                    inferred += 1

        # 采样专业节点（避免图过大）
        for (major,) in majors:
            major = major.strip()
            if major and len(major) >= 2:
                g.add_node('major', major)

        print(f'[build_graph] schools from DB: {len(schools)}, inferred location: {inferred}')
        print(f'[build_graph] major nodes (sample): {len(majors)}')

    g.commit()

    # 统计
    conn = sqlite3.connect(GRAPH_PATH)
    stats = dict(conn.execute('SELECT type, COUNT(*) FROM nodes GROUP BY type').fetchall())
    edges = conn.execute('SELECT COUNT(*) FROM edges').fetchone()[0]
    conn.close()
    print(f'[build_graph] saved → {GRAPH_PATH}')
    print(f'[build_graph] nodes: {stats}, edges: {edges}')


if __name__ == '__main__':
    build()
