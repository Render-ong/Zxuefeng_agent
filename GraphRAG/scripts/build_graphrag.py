#!/usr/bin/env python3
"""一键构建 GraphRAG 索引：社区 + 知识向量 + 学校向量"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def main():
    parser = argparse.ArgumentParser(description='Build all GraphRAG indices')
    parser.add_argument('--ollama', action='store_true', help='Ollama summaries for communities')
    parser.add_argument('--discover', action='store_true', help='Louvain graph community discovery')
    parser.add_argument('--skip-schools', action='store_true', help='skip school vector index (slow)')
    args = parser.parse_args()

    import importlib.util

    def _load_script(name):
        path = os.path.join(HERE, 'scripts', f'{name}.py')
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    graph_db = os.path.join(HERE, 'storage', 'graph.db')
    if not os.path.exists(graph_db):
        print('[build_graphrag] graph.db missing, running build_graph.py...')
        _load_script('build_graph').build()

    n = _load_script('build_communities').build(
        use_ollama=args.ollama, discover=args.discover,
    )
    print(f'[build_graphrag] communities: {n}')

    _load_script('build_knowledge_index').build()

    if not args.skip_schools:
        _load_script('build_school_index').build()

    print('[build_graphrag] done. Restart server or call graphrag.reload()')


if __name__ == '__main__':
    main()
