#!/usr/bin/env python3
"""知识库 chunk 向量化"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

CHUNKS_PATH = os.path.join(HERE, 'data', 'knowledge', 'chunks.json')


def build():
    if not os.path.exists(CHUNKS_PATH):
        raise FileNotFoundError(CHUNKS_PATH)
    with open(CHUNKS_PATH, 'r', encoding='utf-8') as f:
        chunks = json.load(f)
    records = []
    for ch in chunks:
        tags = ' '.join(ch.get('tags') or [])
        text = f"{tags} {ch['text']}"
        records.append({'id': ch['id'], 'text': text, 'snippet': ch['text']})
    from engine import vector_index
    return vector_index.build_index('knowledge', records)


if __name__ == '__main__':
    build()
