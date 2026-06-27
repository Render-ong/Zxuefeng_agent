"""通用向量索引 — 知识库 / 学校等多集合"""
import json
import os
from datetime import datetime, timezone

import numpy as np

from engine.vector_store import STORAGE_DIR, encode_texts, _get_model, _model_lock

_indices = {}


def _paths(name):
    return (
        os.path.join(STORAGE_DIR, f'{name}.npz'),
        os.path.join(STORAGE_DIR, f'{name}_meta.json'),
    )


def is_index_ready(name):
    npz, meta = _paths(name)
    return os.path.exists(npz) and os.path.exists(meta)


def build_index(name, records, model_name=None):
    """
    records: [{id, text, ...extra}]
  返回 meta
    """
    os.makedirs(STORAGE_DIR, exist_ok=True)
    texts = [r['text'] for r in records]
    print(f'[vector_index] encoding {len(texts)} items for {name}...')
    embs = encode_texts(texts)
    npz, meta_path = _paths(name)
    np.savez_compressed(npz, embeddings=embs)
    meta = {
        'name': name,
        'records': records,
        'model': model_name or 'see vector_store',
        'built_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count': len(records),
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    _indices.pop(name, None)

    with _model_lock:
        import engine.vector_store as vs
        vs._model = None
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    print(f'[vector_index] saved {name} ({len(records)} items)')
    return meta


def _load_index(name):
    if name in _indices:
        return _indices[name]
    npz_path, meta_path = _paths(name)
    if not os.path.exists(npz_path):
        return None
    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    embs = np.load(npz_path)['embeddings']
    _indices[name] = {'embeddings': embs, 'records': meta['records']}
    return _indices[name]


def search(name, query, top_k=5, threshold=0.40):
    idx = _load_index(name)
    if idx is None or not query:
        return []
    q_emb = encode_texts([query])[0]
    sims = idx['embeddings'] @ q_emb
    order = np.argsort(-sims)[:top_k]
    hits = []
    for i in order:
        if sims[i] < threshold:
            break
        rec = dict(idx['records'][i])
        rec['score'] = float(sims[i])
        hits.append(rec)
    return hits
