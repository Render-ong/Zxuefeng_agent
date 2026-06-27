"""参数抽取与意图识别 — 从用户消息提取结构化信息"""
import re

PROVINCES = [
    '北京', '天津', '上海', '重庆', '河北', '山西', '辽宁', '吉林', '黑龙江',
    '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南',
    '广东', '广西', '海南', '四川', '贵州', '云南', '西藏', '陕西', '甘肃',
    '青海', '宁夏', '新疆', '内蒙古',
]

MAJOR_KEYWORDS = [
    '计算机', '软件', '电气', '机械', '自动化', '土木', '临床', '口腔', '法学',
    '会计', '金融', '物联网', '人工智能', '大数据', '电子', '通信', '材料',
    '化工', '生物', '医学', '护理', '师范', '英语', '日语', '新闻', '设计',
    '美术', '音乐', '体育', '汉语言', '思政', '马克思', '数学', '化学',
    '地理', '航空航天', '能源', '交通', '环境',
]

REGION_AVOID_KEYS = [
    '东北', '西北', '华北', '华东', '华南', '华中', '西南', '江浙沪', '京津冀',
    '珠三角', '云贵', '新疆', '西藏', '黑龙江', '吉林', '辽宁', '云南', '贵州',
]

REGION_PREF_KEYS = [
    '江浙沪', '京津冀', '珠三角', '北京', '上海', '广东', '浙江', '江苏',
]

COMPARE_PAT = re.compile(r'怎么选|有什么区别|对比|哪个好|vs|VS|还是')
POLICY_PAT = re.compile(r'政策|志愿|填几个|什么模式|专业\+院校|专业组')
MAJOR_INFO_PAT = re.compile(r'专业.*怎么样|就业|学什么|前景|薪资')
TAG_PAT = re.compile(r'(C9|985|211|双一流|两电一邮)', re.I)


def _parse_rank(text):
    m = re.search(r'(\d{4,7})\s*[位名]', text)
    if m:
        return int(m.group(1))
    m = re.search(r'[位名]次?\s*(\d{4,7})', text)
    if m:
        return int(m.group(1))
    m = re.search(r'排[名行]\s*(\d{4,7})', text)
    if m:
        return int(m.group(1))
    # 口语：一万三、1万3
    m = re.search(r'([一二三四五六七八九]?万[一二三四五六七八九]?|1?\d万\d?)', text)
    if m:
        s = m.group(1).replace('万', '0000').replace('一', '1').replace('二', '2')
        s = s.replace('三', '3').replace('四', '4').replace('五', '5')
        s = s.replace('六', '6').replace('七', '7').replace('八', '8').replace('九', '9')
        try:
            if '0000' in s:
                parts = s.split('0000')
                return int(parts[0]) * 10000 + int(parts[1] or 0)
        except Exception:
            pass
    return 0


def _parse_score(text):
    m = re.search(r'(\d{3})\s*分', text)
    if m:
        return int(m.group(1))
    m = re.search(r'分数\s*(\d{3})', text)
    if m:
        return int(m.group(1))
    return 0


def _extract_majors(text):
    neg_parts = re.findall(
        r'(?:不学|不接受|不读|不选|别推荐|别学|拒绝|排斥|不想学|不考虑).*?(?:[。，,;\n]|$)',
        text,
    )
    neg_str = ''.join(neg_parts)
    found = []
    for kw in MAJOR_KEYWORDS:
        if kw in text and kw not in neg_str:
            found.append(kw)
    return found


def _extract_regions(text, keys, pattern):
    m = re.search(pattern, text)
    if not m:
        return []
    chunk = m.group(1)
    return [k for k in keys if k in chunk]


def extract_info(text):
    """从用户消息提取结构化参数（正则主路径）"""
    info = {
        'province': '', 'rank': 0, 'score': 0, 'subject': '',
        'majors': [], 'schools': [], 'region_avoid': [], 'region_pref': [], 'tags': [],
    }
    best_idx, best_prov = len(text), ''
    for p in PROVINCES:
        idx = text.find(p)
        if 0 <= idx < best_idx:
            best_idx, best_prov = idx, p
    info['province'] = best_prov
    info['rank'] = _parse_rank(text)
    info['score'] = _parse_score(text)
    info['majors'] = _extract_majors(text)
    if re.search(r'物化生|物理类?|理科|理工', text):
        info['subject'] = '物理类'
    elif re.search(r'史政地|历史类?|文科|文史', text):
        info['subject'] = '历史类'
    info['region_avoid'] = _extract_regions(
        text, REGION_AVOID_KEYS, r'(?:不想去|不要|排除|别去|远离)([^。，,；;\n]+)',
    )
    info['region_pref'] = _extract_regions(
        text, REGION_PREF_KEYS, r'(?:想去|留在|只要|偏好)([^。，,；;\n]+)',
    )
    sch = re.search(r'[\u4e00-\u9fff]{2,8}(?:大学|学院)', text)
    if sch:
        info['schools'] = [sch.group(0)]
    tm = TAG_PAT.search(text)
    if tm:
        info['tags'] = [tm.group(1) or tm.group(0)]
    if '两电一邮' in text:
        info['tags'].append('两电一邮')
    if 'C9' in text.upper() or 'c9' in text:
        if 'C9' not in info['tags']:
            info['tags'].append('C9')
    return info


def extract_schools_for_compare(text):
    """对比类问题：提取提到的学校名"""
    school_pat = re.compile(r'[\u4e00-\u9fff]{2,10}(?:大学|学院)')
    seen, out = set(), []

    for part in re.split(r'(?:和|与|跟|、|以及|还是|哪个好|怎么选)', text):
        m = school_pat.search(part)
        if not m:
            continue
        s = m.group(0)
        if s not in seen:
            seen.add(s)
            out.append(s)
    if len(out) >= 2:
        return out

    for raw in school_pat.findall(text):
        s = re.sub(r'^[和与跟及、\s]+', '', raw)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def classify_intent(text, params):
    """意图路由"""
    if COMPARE_PAT.search(text):
        return 'compare'
    if POLICY_PAT.search(text) and not (params.get('rank') or params.get('score')):
        return 'policy'
    if params.get('province') and (params.get('rank') or params.get('score')):
        return 'recommend'
    if MAJOR_INFO_PAT.search(text):
        return 'major_info'
    if params.get('majors') or params.get('province'):
        return 'recommend'
    return 'chat'
