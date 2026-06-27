"""知识图谱存储与过滤 — Phase 2"""
import json
import os
import re
import sqlite3
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_PATH = os.path.join(HERE, 'storage', 'graph.db')

PROVINCES = [
    '北京', '天津', '上海', '重庆', '河北', '山西', '辽宁', '吉林', '黑龙江',
    '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南',
    '广东', '广西', '海南', '四川', '贵州', '云南', '西藏', '陕西', '甘肃',
    '青海', '宁夏', '新疆', '内蒙古',
]

_SPECIAL_SCHOOL_PROVINCE = {
    '东北大学': '辽宁',
    '东北财经大学': '辽宁',
    '东北师范大学': '吉林',
    '东华大学': '上海',
    '中国矿业大学': '江苏',
    '中国石油大学': '山东',
    '中国地质大学': '湖北',
    '华北电力大学': '北京',
    '西藏民族大学': '陕西',
}

_cache_lock = threading.Lock()
_cache = None


def is_ready():
    return os.path.exists(GRAPH_PATH)


def _normalize_school(name):
    if not name:
        return ''
    s = re.sub(r'[\(（].*?[\)）]', '', str(name)).strip()
    return s


def _parse_list(val):
    if not val:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    if s.startswith('['):
        try:
            return [str(x).strip() for x in json.loads(s) if str(x).strip()]
        except Exception:
            pass
    return [x.strip() for x in re.split(r'[,，、]', s) if x.strip()]


def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        if not is_ready():
            _cache = {
                'school_province': {},
                'school_tags': {},
                'region_provinces': {},
                'stats': {},
            }
            return _cache

        conn = sqlite3.connect(GRAPH_PATH)
        school_province = {}
        for row in conn.execute(
            "SELECT n.name, p.name FROM nodes n "
            "JOIN edges e ON e.src = n.id AND e.rel = 'LOCATED_IN' "
            "JOIN nodes p ON p.id = e.dst AND p.type = 'province' "
            "WHERE n.type = 'school'"
        ):
            school_province[row[0]] = row[1]

        school_tags = {}
        for row in conn.execute(
            "SELECT n.name, t.name FROM nodes n "
            "JOIN edges e ON e.src = n.id AND e.rel = 'HAS_TAG' "
            "JOIN nodes t ON t.id = e.dst AND t.type = 'tag' "
            "WHERE n.type = 'school'"
        ):
            school_tags.setdefault(row[0], set()).add(row[1])

        region_provinces = {}
        # edges: province -IN_REGION-> region (src=province, dst=region)
        for row in conn.execute(
            "SELECT r.name, p.name FROM nodes r "
            "JOIN edges e ON e.dst = r.id AND e.rel = 'IN_REGION' "
            "JOIN nodes p ON p.id = e.src AND p.type = 'province' "
            "WHERE r.type = 'region'"
        ):
            region_provinces.setdefault(row[0], set()).add(row[1])

        stats = {}
        for row in conn.execute(
            "SELECT type, COUNT(*) FROM nodes GROUP BY type"
        ):
            stats[row[0]] = row[1]

        conn.close()
        _cache = {
            'school_province': school_province,
            'school_tags': school_tags,
            'region_provinces': region_provinces,
            'stats': stats,
        }
        return _cache


def reload():
    global _cache
    with _cache_lock:
        _cache = None
    return _load_cache()


def index_info():
    if not is_ready():
        return {'ready': False}
    c = _load_cache()
    return {
        'ready': True,
        'nodes': c.get('stats', {}),
        'schools_with_location': len(c.get('school_province', {})),
        'schools_with_tags': len(c.get('school_tags', {})),
        'regions': len(c.get('region_provinces', {})),
    }


def _infer_province(school_name, school_province_map):
    base = _normalize_school(school_name)
    if base in school_province_map:
        return school_province_map[base]
    if base in _SPECIAL_SCHOOL_PROVINCE:
        return _SPECIAL_SCHOOL_PROVINCE[base]
    for p in sorted(PROVINCES, key=len, reverse=True):
        if base.startswith(p) and len(base) > len(p):
            return p
    # 模糊匹配种子校名
    for known, prov in school_province_map.items():
        if base.startswith(known) or known.startswith(base):
            return prov
    return ''


def get_school_province(school_name):
    c = _load_cache()
    return _infer_province(school_name, c['school_province'])


def get_school_tags(school_name):
    c = _load_cache()
    base = _normalize_school(school_name)
    tags = set()
    for known, tgs in c['school_tags'].items():
        if base == known or base.startswith(known) or known.startswith(base):
            tags.update(tgs)
    return tags


def _provinces_for_regions(regions):
    c = _load_cache()
    out = set()
    for r in regions:
        if r in c['region_provinces']:
            out.update(c['region_provinces'][r])
        elif r in PROVINCES:
            out.add(r)
    return out


def _should_exclude_school(school_name, region_avoid, region_pref, tags_required):
    if school_name == '【死命令】':
        return False

    loc = get_school_province(school_name)
    avoid_provs = _provinces_for_regions(region_avoid)
    pref_provs = _provinces_for_regions(region_pref)

    if avoid_provs and loc and loc in avoid_provs:
        return True

    if pref_provs and loc and loc not in pref_provs:
        return True

    if tags_required:
        tags = get_school_tags(school_name)
        if not tags:
            return True
        if not any(t in tags for t in tags_required):
            return True

    return False


def filter_result(result, region_avoid=None, region_pref=None, tags_required=None):
    """图过滤：地域排斥/偏好 + 标签要求"""
    region_avoid = _parse_list(region_avoid)
    region_pref = _parse_list(region_pref)
    tags_required = _parse_list(tags_required)

    if not is_ready() or not (region_avoid or region_pref or tags_required):
        result['graph_index'] = is_ready()
        return result

    removed = 0
    out = dict(result)
    for tier in ('chong', 'wen', 'bao'):
        rows = result.get(tier) or []
        kept = []
        for row in rows:
            school = row.get('school', '')
            if _should_exclude_school(school, region_avoid, region_pref, tags_required):
                removed += 1
                continue
            kept.append(row)
        out[tier] = kept

    out['graph_filtered'] = True
    out['graph_index'] = True
    out['graph_removed'] = removed
    if region_avoid:
        out['region_avoid'] = region_avoid
    if region_pref:
        out['region_pref'] = region_pref
    if tags_required:
        out['tags_required'] = tags_required
    return out


def lookup_school(school_name):
    """调试：查询学校所在地与标签"""
    return {
        'school': school_name,
        'normalized': _normalize_school(school_name),
        'province': get_school_province(school_name),
        'tags': sorted(get_school_tags(school_name)),
    }
