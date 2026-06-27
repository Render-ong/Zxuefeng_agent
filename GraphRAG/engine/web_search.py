"""联网搜索 — Tavily 优先，百度兜底"""
import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("graphrag.web_search")


def web_search_baidu(query, n=5):
    results = []
    try:
        url = 'https://www.baidu.com/s?wd=' + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        for pat in [
            r'<span class="content-right_[^"]*">(.*?)</span>',
            r'class="c-abstract"[^>]*>(.*?)</span>',
        ]:
            for s in re.findall(pat, html):
                clean = re.sub(r'<[^>]+>', '', s).strip()
                if len(clean) > 20 and clean not in results:
                    results.append(clean[:300])
                if len(results) >= n:
                    break
            if results:
                break
    except Exception as e:
        results.append(f'搜索暂不可用（{e}）')
    return results[:n] if results else ['百度搜索未返回可用结果']


def web_search_tavily(query, api_key, n=3):
    if not api_key:
        return []
    results = []
    try:
        payload = json.dumps({
            'query': query,
            'search_depth': 'basic',
            'include_answer': True,
            'max_results': n,
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.tavily.com/search',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        if data.get('answer'):
            results.append('[Tavily总结] ' + data['answer'])
        for item in (data.get('results') or [])[:n]:
            results.append(f"{item.get('title', '')}: {(item.get('content') or '')[:300]}")
    except Exception as e:
        log.warning(f'tavily failed: {e}')
    return results


def web_search(query, tavily_key=None, n=3):
    # ponytail: Tavily 优先；有 key 但失败时不再回退百度（高频请求易被封 IP，正则脆弱易失效）。
    # 仅在未配置 tavily_key 时启用百度兜底（开发期/零成本场景）。
    # 升级路径：接入 Bing/SerpAPI 等付费搜索 API 替代百度抓取。
    results = web_search_tavily(query, tavily_key, n=n)
    if not results and not tavily_key:
        results = ['[百度搜索] ' + r for r in web_search_baidu(query, n=n)]
    return results
