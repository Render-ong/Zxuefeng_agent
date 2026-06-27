"""向量检索 — Phase 1：专业语义扩展 + 候选重排"""
import json
import os
import threading

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(HERE, 'storage', 'vector')
CONFIG_PATH = os.path.join(HERE, 'config', 'retrieval.yaml')
META_PATH = os.path.join(STORAGE_DIR, 'meta.json')
MAJORS_NPZ = os.path.join(STORAGE_DIR, 'majors.npz')

_DEFAULT_CFG = {
    'embedding': {'model': 'BAAI/bge-small-zh-v1.5', 'batch_size': 64, 'device': 'auto'},
    'vector': {'major_expand_top_k': 12, 'major_expand_threshold': 0.52, 'rerank_enabled': True},
}

_model = None
_model_lock = threading.Lock()
_index_cache = None


def _load_cfg():
    cfg = {'embedding': dict(_DEFAULT_CFG['embedding']), 'vector': dict(_DEFAULT_CFG['vector'])}
    if os.path.exists(CONFIG_PATH):
        try:
            import yaml
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                raw = yaml.safe_load(f) or {}
            if raw.get('embedding'):
                cfg['embedding'].update(raw['embedding'])
            if raw.get('vector'):
                cfg['vector'].update(raw['vector'])
        except Exception as e:
            print(f'[vector_store] config load failed: {e}')
    return cfg


_CFG = _load_cfg()


def is_ready():
    return os.path.exists(META_PATH) and os.path.exists(MAJORS_NPZ)


def index_info():
    if not is_ready():
        return {'ready': False, 'majors': 0}
    with open(META_PATH, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    return {
        'ready': True,
        'majors': len(meta.get('majors', [])),
        'model': meta.get('model', ''),
        'built_at': meta.get('built_at', ''),
    }


def _resolve_device(pref):
    if pref and pref != 'auto':
        return pref
    try:
        import torch
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    except Exception:
        return 'cpu'


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from sentence_transformers import SentenceTransformer
        model_name = _CFG['embedding']['model']
        device = _resolve_device(_CFG['embedding'].get('device', 'auto'))
        print(f'[vector_store] loading {model_name} on {device}')
        _model = SentenceTransformer(model_name, device=device)
        return _model


def encode_texts(texts):
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    model = _get_model()
    batch = int(_CFG['embedding'].get('batch_size', 64))
    embs = model.encode(
        texts,
        batch_size=batch,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 200,
    )
    return np.asarray(embs, dtype=np.float32)


def _load_index():
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    if not is_ready():
        return None
    data = np.load(MAJORS_NPZ)
    with open(META_PATH, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    _index_cache = {
        'embeddings': data['embeddings'],
        'majors': meta['majors'],
    }
    return _index_cache


def build_major_index(major_names, model_name=None):
    """离线建库：编码专业名并写入 storage/vector/"""
    os.makedirs(STORAGE_DIR, exist_ok=True)
    names = sorted({m.strip() for m in major_names if m and len(m.strip()) >= 2})
    if not names:
        raise ValueError('no major names to index')

    if model_name:
        _CFG['embedding']['model'] = model_name

    print(f'[vector_store] encoding {len(names)} majors...')
    embs = encode_texts(names)

    from datetime import datetime, timezone
    meta = {
        'majors': names,
        'model': _CFG['embedding']['model'],
        'built_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count': len(names),
        'dim': int(embs.shape[1]) if len(embs) else 0,
    }
    np.savez_compressed(MAJORS_NPZ, embeddings=embs)
    with open(META_PATH, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    global _index_cache, _model
    _index_cache = {'embeddings': embs, 'majors': names}
    # 释放模型显存
    with _model_lock:
        _model = None
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

    print(f'[vector_store] index saved → {STORAGE_DIR} ({len(names)} majors)')
    return meta


def _search_majors(query_emb, top_k, threshold):
    idx = _load_index()
    if idx is None or query_emb is None:
        return []
    sims = idx['embeddings'] @ query_emb
    order = np.argsort(-sims)
    hits = []
    for i in order[:top_k]:
        if sims[i] < threshold:
            break
        hits.append({'name': idx['majors'][i], 'score': float(sims[i])})
    return hits


def expand_majors(majors, top_k=None, threshold=None):
    """
    将用户专业意图扩展为更多相关专业名（供 SQL keyword 使用）。
    索引未建好时原样返回。
    """
    if not majors:
        return []
    majors = [m.strip() for m in majors if m and m.strip()]
    if not majors:
        return []

    if not is_ready():
        return list(dict.fromkeys(majors))

    top_k = top_k or int(_CFG['vector'].get('major_expand_top_k', 12))
    threshold = threshold if threshold is not None else float(_CFG['vector'].get('major_expand_threshold', 0.52))

    query = '想学专业：' + '、'.join(majors)
    q_emb = encode_texts([query])[0]
    hits = _search_majors(q_emb, top_k=top_k, threshold=threshold)

    expanded = list(dict.fromkeys(majors))
    for h in hits:
        name = h['name']
        if name not in expanded:
            expanded.append(name)
    return expanded


def expand_keyword(keyword):
    """keyword 逗号分隔 → 向量扩展 → 逗号字符串"""
    if not keyword or not keyword.strip():
        return keyword
    parts = [p.strip() for p in keyword.split(',') if p.strip()]
    if not parts:
        return keyword
    expanded = expand_majors(parts)
    return ','.join(expanded)


def rerank_tier(rows, query_text, top_n=None):
    """按与用户完整意图的语义相似度重排一档候选"""
    if not rows or not query_text or not _CFG['vector'].get('rerank_enabled', True):
        return rows
    if not is_ready():
        return rows

    top_n = top_n or len(rows)
    texts = [f"{r.get('school', '')} {r.get('major', '')}" for r in rows]
    q_emb = encode_texts([query_text])[0]
    c_embs = encode_texts(texts)
    if c_embs.size == 0:
        return rows
    sims = c_embs @ q_emb
    order = np.argsort(-sims)[:top_n]
    return [rows[i] for i in order]


def rerank_result(result, query_text):
    """对冲/稳/保三档分别重排（保留系统提示行）"""
    if not query_text or not is_ready():
        return result
    out = dict(result)
    for tier in ('chong', 'wen', 'bao'):
        rows = result.get(tier) or []
        if not rows:
            continue
        system_rows = [r for r in rows if r.get('school') == '【死命令】']
        data_rows = [r for r in rows if r.get('school') != '【死命令】']
        reranked = rerank_tier(data_rows, query_text)
        out[tier] = system_rows + reranked
    out['reranked'] = True
    return out
