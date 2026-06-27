"""Phase 0 检索冒烟测试"""
import json
import urllib.request
import urllib.parse
import urllib.error

BASE = 'http://127.0.0.1:8765'


def get(path):
    url = BASE + path if path.startswith('/') else BASE + '/' + path
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    ping = get('/ping')
    assert ping['ok'] and ping['db'], ping

    # 浙江综合类 + 计算机
    r1 = get('/recommend?' + urllib.parse.urlencode({
        'province': '浙江', 'rank': 10500, 'keyword': '计算机',
    }))
    assert r1.get('province') == '浙江', r1
    assert r1.get('chong') or r1.get('wen') or r1.get('bao'), r1
    schools = {x['school'] for x in r1['wen']}
    assert len(schools) <= 12, f'too many schools: {len(schools)}'
    print('OK 浙江+计算机:', len(r1['chong']), len(r1['wen']), len(r1['bao']), '校')

    # 科类过滤：安徽物理类（库中该科类有位次数据）
    r2 = get('/recommend?' + urllib.parse.urlencode({
        'province': '安徽', 'rank': 160000, 'category': '物理类',
    }))
    assert r2.get('wen') or r2.get('chong') or r2.get('bao'), '安徽物理类应有结果'
    assert r2.get('category') == '物理类'
    print('OK 安徽+物理类:', len(r2.get('wen', [])), '稳档条数')

    # 无效省份 → HTTP 400
    try:
        get('/recommend?' + urllib.parse.urlencode({'province': '火星', 'rank': 10000}))
        raise AssertionError('expected HTTP 400')
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
        assert 'error' in body, body
    print('OK 无效省份拒绝')

    print('\n全部通过')


if __name__ == '__main__':
    main()
