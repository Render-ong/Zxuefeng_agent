"""用户自定义测试脚本 - 方便填写不同用户信息进行测试
使用方法：修改下方 CONFIG 中的参数，然后运行 python user_test.py
"""
import sys
sys.path.insert(0, r'e:\APP_develop\XF_agent\GraphRAG')

import sqlite3
from engine.recommend import recommend
from engine.vector_store import expand_majors
from engine.graph_store import get_school_province, get_school_tags
from engine.graphrag import build_context, global_search, knowledge_search

DB_PATH = r'e:\APP_develop\XF_agent\GraphRAG\admission_clean.db'


# ========== 用户配置区 ==========
CONFIG = {
    'province': '河北',        # 省份
    'rank': 500,             # 位次（填0则用分数查询）
    'score': 665,                # 分数（位次为0时使用）
    'keyword': '计算机',       # 专业关键词，多个用逗号分隔
    'category': '',            # 科类：物理类/历史类/综合/空
    'region_pref': '北京',         # 地域偏好：北京,上海,广东 等，多个用逗号分隔
    'region_avoid': '',        # 地域排斥：不想去的省份/区域
    'tags_required': '',       # 标签要求：985,211,C9 等
    'query_text': '',          # 完整查询文本（用于向量重排和GraphRAG）
}
# ==================================


def check_province_data(province):
    """检查该省份是否有数据"""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute(
        "SELECT COUNT(*) FROM admission WHERE province LIKE ?",
        ('%' + province + '%',)
    ).fetchone()[0]
    valid_rank = conn.execute(
        "SELECT COUNT(*) FROM admission WHERE province LIKE ? AND rank > 0",
        ('%' + province + '%',)
    ).fetchone()[0]
    valid_score = conn.execute(
        "SELECT COUNT(*) FROM admission WHERE province LIKE ? AND score > 0",
        ('%' + province + '%',)
    ).fetchone()[0]

    rank_range = conn.execute(
        "SELECT MIN(rank), MAX(rank) FROM admission WHERE province LIKE ? AND rank > 0",
        ('%' + province + '%',)
    ).fetchone()

    score_range = conn.execute(
        "SELECT MIN(score), MAX(score) FROM admission WHERE province LIKE ? AND score > 0",
        ('%' + province + '%',)
    ).fetchone()

    categories = conn.execute(
        "SELECT category, COUNT(*) FROM admission WHERE province LIKE ? GROUP BY category ORDER BY COUNT(*) DESC",
        ('%' + province + '%',)
    ).fetchall()
    conn.close()

    return {
        'total': total,
        'valid_rank': valid_rank,
        'valid_score': valid_score,
        'rank_min': rank_range[0],
        'rank_max': rank_range[1],
        'score_min': score_range[0],
        'score_max': score_range[1],
        'categories': categories,
    }


def print_config():
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 18 + "用户信息" + " " * 28 + "║")
    print("╠" + "=" * 58 + "╣")
    for k, v in CONFIG.items():
        if v:
            print("║  %-15s: %-40s║" % (k, str(v)[:40]))
    print("╚" + "=" * 58 + "╝")


def main():
    print_config()

    province = CONFIG['province']
    rank = CONFIG['rank']
    score = CONFIG['score']
    keyword = CONFIG['keyword']
    category = CONFIG['category']
    region_pref = CONFIG['region_pref']
    region_avoid = CONFIG['region_avoid']
    tags_required = CONFIG['tags_required']
    query_text = CONFIG['query_text'] or (
        "%s考生 位次%s 分数%s 想学%s" % (province, rank or '?', score or '?', keyword or '专业')
    )

    # ========== 0. 数据检查 ==========
    print()
    print("=" * 60)
    print("【0. 数据检查】%s 省数据概况" % province)
    print("=" * 60)

    info = check_province_data(province)

    if info['total'] == 0:
        print("  !! 错误: 数据库中没有 %s 的数据 !!" % province)
        print("  可选省份: 河北、山东、浙江、重庆、黑龙江、湖北、江苏、北京、湖南、上海、广东、内蒙古、安徽、海南")
        return

    print("  总记录数: %d 条" % info['total'])
    print("  有效位次记录: %d 条" % info['valid_rank'])
    print("  有效分数记录: %d 条" % info['valid_score'])

    if info['valid_rank'] > 0:
        print("  位次范围: %d ~ %d" % (info['rank_min'], info['rank_max']))
    if info['valid_score'] > 0:
        print("  分数范围: %d ~ %d" % (info['score_min'], info['score_max']))

    print("  科类分布:")
    for cat, cnt in info['categories']:
        print("    %s: %d 条" % (cat or '(空)', cnt))

    # 检查位次/分数是否在范围内
    if rank > 0 and info['valid_rank'] > 0:
        if rank < info['rank_min'] or rank > info['rank_max']:
            print()
            print("  !! 注意: 位次 %d 不在该省数据范围内(%d~%d) !!" %
                  (rank, info['rank_min'], info['rank_max']))

    # ========== 1. 向量扩展 ==========
    print()
    print("=" * 60)
    print("【Phase 1: 向量检索】专业语义扩展")
    print("=" * 60)

    if keyword:
        majors = [m.strip() for m in keyword.split(',') if m.strip()]
        expanded = expand_majors(majors)
        print("  输入: %s" % keyword)
        print("  扩展结果 (%d个):" % len(expanded))
        for i, m in enumerate(expanded[:8]):
            print("    %2d. %s" % (i + 1, m))
        if len(expanded) > 8:
            print("    ... 还有 %d 个" % (len(expanded) - 8))
    else:
        print("  未填写专业关键词，跳过向量扩展")

    # ========== 2. 图谱查询 ==========
    print()
    print("=" * 60)
    print("【Phase 2: 知识图谱】标签/地域过滤")
    print("=" * 60)

    filter_info = []
    if region_pref:
        filter_info.append("地域偏好: %s" % region_pref)
    if region_avoid:
        filter_info.append("地域排斥: %s" % region_avoid)
    if tags_required:
        filter_info.append("标签要求: %s" % tags_required)

    if filter_info:
        print("  过滤条件: %s" % ", ".join(filter_info))
    else:
        print("  无额外过滤条件")

    # ========== 3. 完整推荐 ==========
    print()
    print("=" * 60)
    print("【Phase 3: 完整推荐结果】")
    print("=" * 60)

    result = recommend(
        DB_PATH, [], province,
        rank=rank,
        score=score,
        category=category,
        keyword=keyword,
        query_text=query_text,
        region_pref=region_pref,
        region_avoid=region_avoid,
        tags_required=tags_required,
    )

    if 'error' in result:
        print("  错误: %s" % result['error'])
        return

    chong = result.get('chong', [])
    wen = result.get('wen', [])
    bao = result.get('bao', [])

    print("  向量索引: %s" % result.get('vector_index', False))
    print("  图索引: %s" % result.get('graph_index', False))
    if result.get('keyword_expanded'):
        print("  专业扩展: 是")
    if result.get('graph_filtered'):
        print("  图过滤: 是（过滤掉 %d 条）" % result.get('graph_removed', 0))
    if result.get('reranked'):
        print("  向量重排: 是")

    print()
    print("  ┌─ 冲档 (%d 所) ─┐" % len(chong))
    if chong:
        for i, item in enumerate(chong[:8]):
            prov = get_school_province(item['school'])
            tags = get_school_tags(item['school'])
            tag_str = "[" + ",".join(tags) + "]" if tags else ""
            rank_val = item.get('rank', '?')
            rank_str = "位次%s" % (rank_val if rank_val else '?')
            print("    %2d. %-20s %s  %s  %s" %
                  (i + 1, item['school'][:20], item['major'][:25], rank_str, tag_str))
        if len(chong) > 8:
            print("    ... 还有 %d 所" % (len(chong) - 8))
    else:
        print("    (无)")

    print()
    print("  ┌─ 稳档 (%d 所) ─┐" % len(wen))
    if wen:
        for i, item in enumerate(wen[:10]):
            prov = get_school_province(item['school'])
            tags = get_school_tags(item['school'])
            tag_str = "[" + ",".join(tags) + "]" if tags else ""
            rank_val = item.get('rank', '?')
            rank_str = "位次%s" % (rank_val if rank_val else '?')
            print("    %2d. %-20s %s  %s  %s" %
                  (i + 1, item['school'][:20], item['major'][:25], rank_str, tag_str))
        if len(wen) > 10:
            print("    ... 还有 %d 所" % (len(wen) - 10))
    else:
        print("    (无)")

    print()
    print("  ┌─ 保档 (%d 所) ─┐" % len(bao))
    if bao:
        for i, item in enumerate(bao[:8]):
            prov = get_school_province(item['school'])
            tags = get_school_tags(item['school'])
            tag_str = "[" + ",".join(tags) + "]" if tags else ""
            rank_val = item.get('rank', '?')
            rank_str = "位次%s" % (rank_val if rank_val else '?')
            print("    %2d. %-20s %s  %s  %s" %
                  (i + 1, item['school'][:20], item['major'][:25], rank_str, tag_str))
        if len(bao) > 8:
            print("    ... 还有 %d 所" % (len(bao) - 8))
    else:
        print("    (无)")

    # ========== 4. GraphRAG 补充信息 ==========
    print()
    print("=" * 60)
    print("【GraphRAG 补充信息】")
    print("=" * 60)

    context = build_context(query_text, {'province': province}, intent='recommend')
    if context:
        # 分段显示
        lines = context.split('\n')
        for line in lines[:15]:
            print("  %s" % line)
        if len(lines) > 15:
            print("  ... (更多内容可直接调用 build_context 获取)")

    # ========== 汇总 ==========
    total = len(chong) + len(wen) + len(bao)
    print()
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 20 + "结果汇总" + " " * 24 + "║")
    print("╠" + "=" * 58 + "╣")
    print("║  冲档: %-3d 所                                          ║" % len(chong))
    print("║  稳档: %-3d 所                                          ║" % len(wen))
    print("║  保档: %-3d 所                                          ║" % len(bao))
    print("║  合计: %-3d 所                                          ║" % total)
    if total == 0:
        print("║  !! 无匹配结果，建议:                                   ║")
        print("║  1. 减少专业限制或使用更宽泛的关键词                    ║")
        print("║  2. 检查省份是否填写正确                                ║")
        print("║  3. 取消地域偏好/标签限制                               ║")
        print("║  4. 如果该省只有分数数据，请用score查询                  ║")
    else:
        print("║" + " " * 18 + "[OK] 查询成功" + " " * 28 + "║")
    print("╚" + "=" * 58 + "╝")


if __name__ == '__main__':
    main()
