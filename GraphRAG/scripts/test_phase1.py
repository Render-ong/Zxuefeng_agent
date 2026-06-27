"""Phase 1 向量检索冒烟测试"""
import json
import urllib.parse
import urllib.request
import urllib.error

BASE = 'http://127.0.0.1:8765'


def get(path):
    url = BASE + path if path.startswith('/') else BASE + '/' + path
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def main():
    ping = get('/ping')
    assert ping['ok'] and ping['db'], ping
    assert ping.get('vector'), f'vector index not ready: {ping}'
    print('OK ping vector:', ping.get('vector_majors'), 'majors')

    # 专业扩展
    exp = get('/vector/majors?' + urllib.parse.urlencode({'q': '人工智能'}))
    assert len(exp['expanded']) > 1, exp
    assert '人工智能' in exp['expanded']
    print('OK expand 人工智能 →', len(exp['expanded']), 'terms')
    print('   sample:', exp['expanded'][:5])

    # 推荐链路含扩展字段
    r = get('/recommend?' + urllib.parse.urlencode({
        'province': '浙江', 'rank': 10500, 'keyword': '人工智能',
        'q': '我是浙江考生位次10500想学人工智能',
    }))
    assert r.get('keyword_expanded'), 'should have expanded keywords'
    assert r.get('vector_index') is True
    orig = set(r['keyword_original'].split(','))
    expanded = set(r['keyword_expanded'].split(','))
    assert len(expanded) >= len(orig), r
    print('OK recommend expanded:', r['keyword_original'], '→', r['keyword_expanded'][:80], '...')

    print('\n全部通过')


if __name__ == '__main__':
    main()
