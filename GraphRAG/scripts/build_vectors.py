#!/usr/bin/env python3
"""离线构建专业向量索引（GPU 加速编码）"""
import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

DB_PATH = os.path.join(HERE, 'admission_clean.db')


def extract_major_names(db_path):
    if not os.path.exists(db_path):
        raise FileNotFoundError(f'database not found: {db_path}')
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT DISTINCT major_name FROM admission
        WHERE major_name IS NOT NULL AND length(trim(major_name)) >= 2
        ORDER BY major_name
    """).fetchall()
    conn.close()
    names = [r[0].strip() for r in rows if r[0] and r[0].strip()]
    # 过滤纯数字/纯代号
    cleaned = []
    for n in names:
        if n in ('本科', '专科', '(不限)', '（不限）'):
            continue
        if all(c.isdigit() or c in '.-' for c in n.replace(' ', '')):
            continue
        cleaned.append(n)
    return cleaned


def main():
    parser = argparse.ArgumentParser(description='Build major vector index')
    parser.add_argument('--gpu', action='store_true', help='prefer CUDA device')
    parser.add_argument('--model', default='', help='override embedding model')
    parser.add_argument('--db', default=DB_PATH, help='admission sqlite path')
    args = parser.parse_args()

    if args.gpu:
        import engine.vector_store as vs
        vs._CFG['embedding']['device'] = 'cuda'

    from engine import vector_store

    names = extract_major_names(args.db)
    print(f'[build_vectors] extracted {len(names)} distinct majors')
    model = args.model or None
    meta = vector_store.build_major_index(names, model_name=model)
    print(f'[build_vectors] done: {meta["count"]} majors, dim={meta["dim"]}, model={meta["model"]}')


if __name__ == '__main__':
    main()
