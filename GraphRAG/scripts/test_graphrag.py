"""GraphRAG 深化功能测试（无需启动 server）"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def main():
    from engine import graphrag, graph_subgraph

    print('=== index_info ===')
    print(graphrag.index_info())

    q_compare = '浙江大学和复旦大学计算机专业怎么选'
    print('\n=== search compare ===')
    r = graphrag.search(q_compare, intent='compare', params={})
    print('global:', len(r.get('global', [])), 'hits')
    print('compare block:', bool(r.get('compare')))
    if r.get('compare'):
        print(r['compare'][:400])

    print('\n=== knowledge ===')
    k = graphrag.knowledge_search('计算机就业城市')
    for line in k:
        print(' -', line[:80])

    print('\n=== school_search ===')
    s = graphrag.school_search('上海 985 计算机')
    for h in s[:3]:
        print(h)

    print('\n=== subgraph profile ===')
    p = graph_subgraph.school_profile('浙江大学')
    print(graph_subgraph.format_profile(p))

    print('\n=== build_context ===')
    ctx = graphrag.build_context(q_compare, {}, 'compare')
    print(ctx[:600] if ctx else '(empty)')

    print('\n[test_graphrag] OK')


if __name__ == '__main__':
    main()
