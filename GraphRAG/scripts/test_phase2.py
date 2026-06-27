"""Phase 2 知识图谱冒烟测试"""
import json
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8765'


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode())


def count_schools(tier):
    return len({r['school'] for r in tier if r.get('school') != '【死命令】'})


def main():
    ping = get('/ping')
    assert ping.get('graph'), f'graph not ready: {ping}'
    print('OK ping graph nodes:', ping.get('graph_nodes'))

    hhit = get('/graph/school?' + urllib.parse.urlencode({'name': '哈尔滨工业大学'}))
    assert hhit.get('province') == '黑龙江', hhit
    print('OK 哈工大所在地:', hhit['province'], 'tags:', hhit.get('tags'))

    # 无过滤基准
    base = get('/recommend?' + urllib.parse.urlencode({
        'province': '浙江', 'rank': 10500, 'keyword': '计算机',
    }))
    base_schools = set()
    for tier in ('chong', 'wen', 'bao'):
        for r in base.get(tier, []):
            if r.get('school') != '【死命令】':
                base_schools.add(r['school'])

    # 排斥东北
    filtered = get('/recommend?' + urllib.parse.urlencode({
        'province': '浙江', 'rank': 10500, 'keyword': '计算机',
        'region_avoid': '东北',
    }))
    assert filtered.get('graph_filtered'), filtered
    filt_schools = set()
    for tier in ('chong', 'wen', 'bao'):
        for r in filtered.get(tier, []):
            if r.get('school') != '【死命令】':
                filt_schools.add(r['school'])
    northeast = [s for s in filt_schools if get('/graph/school?' + urllib.parse.urlencode({'name': s})).get('province') in ('黑龙江', '吉林', '辽宁')]
    assert not northeast, f'northeast schools still present: {northeast}'
    print('OK region_avoid=东北 removed', filtered.get('graph_removed', 0), 'rows')

    # C9 标签过滤
    c9 = get('/recommend?' + urllib.parse.urlencode({
        'province': '浙江', 'rank': 3000, 'keyword': '计算机',
        'tags': 'C9',
    }))
    assert c9.get('graph_filtered'), c9
    for tier in ('chong', 'wen', 'bao'):
        for r in c9.get(tier, []):
            if r.get('school') == '【死命令】':
                continue
            tags = get('/graph/school?' + urllib.parse.urlencode({'name': r['school']})).get('tags', [])
            assert 'C9' in tags, f"{r['school']} not C9: {tags}"
    print('OK tags=C9 all results are C9 schools')

    print('\n全部通过')


if __name__ == '__main__':
    main()
