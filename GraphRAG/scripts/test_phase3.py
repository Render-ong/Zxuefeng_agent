"""Phase 3 GraphRAG + /api/chat 冒烟测试"""
import json
import urllib.parse
import urllib.request
import urllib.error

BASE = 'http://127.0.0.1:8765'


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())


def post(path, body):
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def main():
    ping = get('/ping')
    assert ping.get('graphrag'), f'graphrag not ready: {ping}'
    print('OK ping graphrag communities:', ping.get('communities'))

    search = get('/api/graph/search?' + urllib.parse.urlencode({'q': 'C9计算机怎么选'}))
    assert search.get('global') or search.get('knowledge'), search
    print('OK graph search global hits:', len(search.get('global', [])))

    # /api/chat 结构测试（无真实 key 时应返回明确错误）
    status, resp = post('/api/chat', {'message': '你好', 'mode': 'gaokao', 'api': {}})
    assert status == 400 and 'key' in resp.get('error', '').lower(), resp
    print('OK /api/chat validates API key')

    status2, resp2 = post('/api/chat', {
        'message': '测试',
        'mode': 'fun',
        'api': {'url': 'https://api.deepseek.com', 'key': 'invalid-key-test', 'model': 'deepseek-chat'},
    })
    # invalid key → LLM 失败但有 reply 或 error
    assert resp2.get('reply') or resp2.get('error'), resp2
    print('OK /api/chat endpoint reachable, response keys:', list(resp2.keys()))

    print('\n全部通过')


if __name__ == '__main__':
    main()
