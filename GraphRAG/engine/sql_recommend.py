"""SQL 冲稳保推荐 — Phase 0：精确省份、科类过滤、动态带宽、学校去重"""
import logging
import os
import sqlite3

log = logging.getLogger("graphrag.sql_recommend")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(HERE, 'config', 'rank_bands.yaml')

PROVINCES = [
    '北京', '天津', '上海', '重庆', '河北', '山西', '辽宁', '吉林', '黑龙江',
    '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南',
    '广东', '广西', '海南', '四川', '贵州', '云南', '西藏', '陕西', '甘肃',
    '青海', '宁夏', '新疆', '内蒙古',
]

_CATEGORY_ALIASES = {
    '物理': '物理类', '物理类': '物理类', '理科': '物理类', '理工': '物理类',
    '历史': '历史类', '历史类': '历史类', '文科': '历史类', '文史': '历史类',
    '综合': '综合',
}

_DEFAULT_BANDS = [
    {'rank_max': 5000, 'chong': [0.92, 1.00], 'wen': [1.00, 1.15], 'bao': [1.15, 1.30]},
    {'rank_max': 50000, 'chong': [0.88, 1.00], 'wen': [1.00, 1.25], 'bao': [1.25, 1.55]},
    {'rank_max': 999999, 'chong': [0.85, 1.00], 'wen': [1.00, 1.30], 'bao': [1.30, 1.70]},
]

_DEFAULT_LIMITS = {
    'max_schools_per_tier': 12,
    'max_majors_per_school': 2,
    'sql_fetch_limit': 80,
}


def _load_config():
    bands, limits = _DEFAULT_BANDS, dict(_DEFAULT_LIMITS)
    if os.path.exists(CONFIG_PATH):
        try:
            import yaml
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
            if cfg.get('bands'):
                bands = cfg['bands']
            if cfg.get('limits'):
                limits = {**limits, **cfg['limits']}
        except Exception as e:
            log.warning(f'config load failed: {e}')
    return bands, limits


_RANK_BANDS, _LIMITS = _load_config()


def normalize_province(prov):
    """省份名归一化 + 白名单校验，返回标准省名或空串"""
    if not prov:
        return ''
    s = str(prov).strip().replace('省', '').replace('市', '').replace('自治区', '').strip()
    if s in PROVINCES:
        return s
    for p in PROVINCES:
        if p in s or s in p:
            return p
    return ''


def normalize_category(subject):
    """科类归一化：物化生→物理类，史政地→历史类"""
    if not subject:
        return ''
    s = str(subject).strip()
    if s in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[s]
    if '物理' in s or '理科' in s or '理工' in s:
        return '物理类'
    if '历史' in s or '文科' in s or '文史' in s:
        return '历史类'
    if '综合' in s:
        return '综合'
    return s


def get_rank_bands(rank):
    """按用户位次返回冲/稳/保比例区间 (low_ratio, high_ratio)"""
    for band in _RANK_BANDS:
        if rank <= band['rank_max']:
            return band['chong'], band['wen'], band['bao']
    c, w, b = _DEFAULT_BANDS[-1]['chong'], _DEFAULT_BANDS[-1]['wen'], _DEFAULT_BANDS[-1]['bao']
    return c, w, b


def _row_to_dict(r):
    return {'school': r[0], 'major': r[1], 'score': r[2], 'rank': r[3], 'year': r[4]}


def _build_filters(province, category='', major='', keyword='', school=''):
    """构造 WHERE 子句与参数（省份精确匹配 + 科类 + 关键词）"""
    clauses = ['province = ?', '(score > 0 OR rank > 0)']
    params = [province]

    cat = normalize_category(category)
    if cat:
        clauses.append("(category = ? OR category = '综合' OR category = '' OR category IS NULL)")
        params.append(cat)

    if major:
        clauses.append('major_name LIKE ?')
        params.append(f'%{major}%')

    if school:
        clauses.append('school_name LIKE ?')
        params.append(f'%{school}%')

    if keyword:
        kws = [k.strip() for k in keyword.split(',') if k.strip()]
        if kws:
            kw_conds, kw_params = [], []
            for kw in kws:
                kw_conds.append('(major_name LIKE ? OR school_name LIKE ?)')
                kw_params.extend([f'%{kw}%', f'%{kw}%'])
            clauses.append('(' + ' OR '.join(kw_conds) + ')')
            params.extend(kw_params)

    return ' AND '.join(clauses), params


def _aggregate_by_school(rows, user_rank, max_schools=None, max_per_school=None):
    """按学校去重：每校保留位次最接近用户的专业，限制学校数量"""
    max_schools = max_schools or _LIMITS['max_schools_per_tier']
    max_per_school = max_per_school or _LIMITS['max_majors_per_school']

    by_school = {}
    for row in rows:
        if row.get('school') == '【死命令】':
            continue
        sk = row['school']
        rk = row.get('rank') or 0
        if sk not in by_school:
            by_school[sk] = []
        by_school[sk].append(row)

    result = []
    school_best = []
    for sk, majors in by_school.items():
        if user_rank and user_rank > 0:
            majors.sort(key=lambda x: abs((x.get('rank') or 999999) - user_rank))
        else:
            majors.sort(key=lambda x: x.get('score') or 0, reverse=True)
        best_rank = abs((majors[0].get('rank') or 999999) - (user_rank or 0))
        school_best.append((best_rank, sk, majors[:max_per_school]))

    school_best.sort(key=lambda x: x[0])
    for _, sk, majors in school_best[:max_schools]:
        result.extend(majors)
    return result


def _query_rank_range(conn, base_where, base_params, lo, hi, fetch_limit):
    """按位次闭区间查询"""
    if lo > hi or hi < 1:
        return []
    sql = (
        f'SELECT school_name, major_name, score, rank, year FROM admission '
        f'WHERE {base_where} AND rank > 0 AND rank >= ? AND rank <= ? '
        f'ORDER BY year DESC, rank ASC LIMIT ?'
    )
    rows = conn.execute(sql, base_params + [lo, hi, fetch_limit]).fetchall()
    return [_row_to_dict(r) for r in rows]


def _tier_bounds(rank, chong_band, wen_band, bao_band):
    """计算冲/稳/保位次区间（互不重叠）"""
    chong_lo = max(1, int(rank * chong_band[0]))
    chong_hi = max(chong_lo, rank - 1)
    wen_lo = rank
    wen_hi = int(rank * wen_band[1])
    bao_lo = wen_hi + 1
    bao_hi = int(rank * bao_band[1])
    return {
        'chong': (chong_lo, chong_hi),
        'wen': (wen_lo, wen_hi),
        'bao': (bao_lo, bao_hi),
    }


def _query_score_tiers(conn, base_where, base_params, score, fetch_limit):
    """按分数区间查询冲/稳/保（位次不可用时的降级）"""
    chong = [_row_to_dict(r) for r in conn.execute(
        f'SELECT school_name, major_name, score, rank, year FROM admission '
        f'WHERE {base_where} AND score > ? AND score <= ? '
        f'ORDER BY year DESC, score DESC LIMIT ?',
        base_params + [score, score + 25, fetch_limit],
    ).fetchall()]
    wen = [_row_to_dict(r) for r in conn.execute(
        f'SELECT school_name, major_name, score, rank, year FROM admission '
        f'WHERE {base_where} AND score >= ? AND score <= ? '
        f'ORDER BY year DESC, score ASC LIMIT ?',
        base_params + [score - 25, score + 25, fetch_limit],
    ).fetchall()]
    bao = [_row_to_dict(r) for r in conn.execute(
        f'SELECT school_name, major_name, score, rank, year FROM admission '
        f'WHERE {base_where} AND score >= ? AND score < ? '
        f'ORDER BY year DESC, score ASC LIMIT ?',
        base_params + [score - 50, score - 25, fetch_limit],
    ).fetchall()]
    return chong, wen, bao


def query_db(conn_path, province=None, school=None, major=None, limit=50):
    """简单查询 /query 接口"""
    prov = normalize_province(province)
    if not prov or not os.path.exists(conn_path):
        return None
    conn = sqlite3.connect(conn_path)
    clauses, params = ['province = ?'], [prov]
    if school:
        clauses.append('school_name LIKE ?')
        params.append(f'%{school}%')
    if major:
        clauses.append('major_name LIKE ?')
        params.append(f'%{major}%')
    sql = (
        f'SELECT province, year, school_name, major_name, score, rank FROM admission '
        f"WHERE {' AND '.join(clauses)} AND rank > 100 "
        f'ORDER BY year DESC, rank ASC LIMIT ?'
    )
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {'province': r[0], 'year': r[1], 'school_name': r[2], 'major_name': r[3], 'score': r[4], 'rank': r[5]}
        for r in rows
    ]


def recommend_from_db(db_path, province, rank=0, score=0, category='', major='', keyword='', school=''):
    """
    主推荐入口。返回 dict: rank, score, province, category, chong, wen, bao, bands
    """
    prov = normalize_province(province)
    if not prov:
        return {'error': 'invalid province', 'province': province}

    if rank <= 0 and score <= 0:
        return {'error': 'need rank or score', 'province': prov}

    cat = normalize_category(category)
    fetch_limit = _LIMITS['sql_fetch_limit']
    max_schools = _LIMITS['max_schools_per_tier']

    conn = sqlite3.connect(db_path)
    base_where, base_params = _build_filters(prov, cat, major, keyword, school)

    chong, wen, bao = [], [], []
    bands_used = None

    if rank > 0:
        chong_band, wen_band, bao_band = get_rank_bands(rank)
        bands_used = {'chong': chong_band, 'wen': wen_band, 'bao': bao_band}
        bounds = _tier_bounds(rank, chong_band, wen_band, bao_band)
        chong = _query_rank_range(conn, base_where, base_params, *bounds['chong'], fetch_limit)
        wen = _query_rank_range(conn, base_where, base_params, *bounds['wen'], fetch_limit)
        bao = _query_rank_range(conn, base_where, base_params, *bounds['bao'], fetch_limit)

    # 关键词过严导致空结果 → 去掉关键词重试
    if not (chong or wen or bao) and keyword and rank > 0:
        base_where, base_params = _build_filters(prov, cat, major, '', school)
        chong_band, wen_band, bao_band = get_rank_bands(rank)
        bounds = _tier_bounds(rank, chong_band, wen_band, bao_band)
        chong = _query_rank_range(conn, base_where, base_params, *bounds['chong'], fetch_limit)
        wen = _query_rank_range(conn, base_where, base_params, *bounds['wen'], fetch_limit)
        bao = _query_rank_range(conn, base_where, base_params, *bounds['bao'], fetch_limit)

    # 位次无结果 → 按分数降级
    if not (chong or wen or bao) and score > 0:
        chong, wen, bao = _query_score_tiers(conn, base_where, base_params, score, fetch_limit)
        if not (chong or wen or bao) and keyword:
            base_where, base_params = _build_filters(prov, cat, major, '', school)
            chong, wen, bao = _query_score_tiers(conn, base_where, base_params, score, fetch_limit)

    conn.close()

    chong = _aggregate_by_school(chong, rank, max_schools)
    wen = _aggregate_by_school(wen, rank, max_schools)
    bao = _aggregate_by_school(bao, rank, max_schools)

    return {
        'province': prov,
        'category': cat,
        'rank': rank,
        'score': score,
        'bands': bands_used,
        'chong': chong,
        'wen': wen,
        'bao': bao,
        'source': 'db',
    }


def recommend_from_user_data(user_data, province, rank=0, score=0, category=''):
    """自定义 Excel 数据推荐（保留原有逻辑，修复 years 未写入 bug）

    ponytail: 当前所有 caller（api_server.py / langgraph/nodes/recommend.py）
    都传 user_data=[]，函数始终走 has_ud=False 提前返回 None，是死路径。
    保留函数不删，避免改 recommend()/handle_chat() 的 signature 引发连带改动。
    启用自定义 Excel 时必须补 user_id 参数并按 user_id 过滤 user_data，
    否则用户 A 的 Excel 数据会被 B 的请求读到（跨用户泄漏）。
    升级路径：user_data 改为从 user_data.db 按 user_id 查表得到。
    """
    prov = normalize_province(province)
    if not prov:
        return None

    cat = normalize_category(category)
    has_ud = any(
        normalize_province(u.get('province', '')) == prov and u.get('score')
        for u in user_data
    )
    if not has_ud:
        return None

    um_raw = {}
    for u in user_data:
        if normalize_province(u.get('province', '')) != prov:
            continue
        if cat and u.get('category') and u['category'] not in (cat, '综合', ''):
            continue
        if not u.get('score') or not u.get('rank'):
            continue
        k = u['school'] + '|' + u.get('major', '')
        if k not in um_raw:
            um_raw[k] = {
                'school': u['school'], 'major': u.get('major', ''),
                'scores': [], 'ranks': [], 'years': [],
            }
        um_raw[k]['scores'].append(u['score'])
        um_raw[k]['ranks'].append(u['rank'])
        um_raw[k]['years'].append(u.get('year', 0))

    um_all = []
    for v in um_raw.values():
        r, s = v['ranks'], v['scores']
        avg_sc = int(sum(s) / len(s))
        avg_rk = int(sum(r) / len(r))
        yr = v['major']
        if len(r) >= 2:
            y0, y1 = v['years'][0], v['years'][1]
            if y0 == 2024:
                yr += f' [24:{s[0]}分/{r[0]}位 25:{s[1]}分/{r[1]}位]'
            else:
                yr += f' [24:{s[1]}分/{r[1]}位 25:{s[0]}分/{r[0]}位]'
        elif len(r) == 1:
            yr += f' [{v["years"][0]}:{s[0]}分/{r[0]}位]'
        um_all.append({
            'school': v['school'], 'major': yr, 'score': avg_sc, 'rank': avg_rk,
            'year': '综合', 'source': 'user',
        })

    um_all.sort(key=lambda x: x['rank'])
    n = len(um_all)
    ch = um_all[: max(1, n // 3)] if n >= 3 else um_all
    wn = um_all[n // 3: 2 * n // 3] if n >= 3 else []
    ba = um_all[2 * n // 3:] if n >= 3 else []
    ch.insert(0, {
        'school': '【死命令】',
        'major': '只准推荐下面学校·不准推荐表外任何学校·补充建议提其他校须标注网络搜索仅供参考',
        'score': 0, 'rank': 0, 'year': '', 'source': 'system',
    })
    return {
        'rank': rank, 'score': score, 'province': prov, 'category': cat,
        'chong': ch, 'wen': wn, 'bao': ba, 'user_data': um_all, 'source': 'custom_only',
    }
