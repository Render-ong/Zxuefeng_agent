"""三层检索引擎测试脚本 - 覆盖多省份多场景"""
import sys
sys.path.insert(0, r'e:\APP_develop\XF_agent\GraphRAG')

import sqlite3
from engine.recommend import recommend
from engine.vector_store import expand_majors
from engine.graph_store import get_school_province, get_school_tags
from engine.graphrag import global_search, knowledge_search

DB_PATH = r'e:\APP_develop\XF_agent\GraphRAG\admission_clean.db'


def check_db():
    """检查数据库状态"""
    print("=" * 60)
    print("【检查数据库】")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    provinces = conn.execute(
        "SELECT province, COUNT(*) FROM admission GROUP BY province ORDER BY COUNT(*) DESC"
    ).fetchall()

    print(f"{'省份':<10} {'数据量':>10}")
    print("-" * 22)
    for prov, count in provinces:
        print(f"{prov:<10} {count:>10,}")

    # 检查位次范围
    print()
    print("各省份位次范围:")
    for prov, _ in provinces[:5]:  # 只检查前5个
        range_info = conn.execute(
            "SELECT MIN(rank), MAX(rank), COUNT(*) FROM admission WHERE province = ? AND rank > 0",
            (prov,)
        ).fetchone()
        if range_info[0] and range_info[1]:
            print(f"  {prov}: 位次 {range_info[0]:,} ~ {range_info[1]:,}, 有效记录 {range_info[2]:,}")

    conn.close()
    return [p for p, _ in provinces]


def test_basic(province, rank, keyword, label=""):
    """基础推荐测试"""
    print()
    print(f"{'=' * 60}")
    print(f"【测试】{province} 位次{rank} 专业:{keyword} {label}")
    print("=" * 60)

    result = recommend(DB_PATH, [], province, rank=rank, keyword=keyword)

    if 'error' in result:
        print(f"  错误: {result['error']}")
        return False

    chong = len(result.get('chong', []))
    wen = len(result.get('wen', []))
    bao = len(result.get('bao', []))

    print(f"  冲:{chong} 稳:{wen} 保:{bao}")

    if result.get('keyword_expanded'):
        print(f"  扩展: {keyword} → {result.get('keyword_expanded')[:60]}...")

    # 显示稳档示例
    if wen > 0:
        print("  稳档示例:")
        for item in result['wen'][:3]:
            prov = get_school_province(item['school'])
            print(f"    {item['school']} {item['major'][:25]} (位次{item['rank']}) [{prov}]")

    return chong > 0 or wen > 0 or bao > 0


def test_no_keyword(province, rank, label=""):
    """无专业关键词测试"""
    print()
    print(f"{'=' * 60}")
    print(f"【测试】{province} 位次{rank} 无专业限制 {label}")
    print("=" * 60)

    result = recommend(DB_PATH, [], province, rank=rank, keyword='')

    if 'error' in result:
        print(f"  错误: {result['error']}")
        return False

    chong = len(result.get('chong', []))
    wen = len(result.get('wen', []))
    bao = len(result.get('bao', []))

    print(f"  冲:{chong} 稳:{wen} 保:{bao}")

    if wen > 0:
        print("  稳档示例:")
        for item in result['wen'][:3]:
            prov = get_school_province(item['school'])
            print(f"    {item['school']} {item['major'][:25]} (位次{item['rank']}) [{prov}]")

    return chong > 0 or wen > 0 or bao > 0


def test_vector_expand(majors):
    """向量扩展测试"""
    print()
    print("=" * 60)
    print(f"【测试】向量扩展: {majors}")
    print("=" * 60)

    expanded = expand_majors(majors)
    print(f"  扩展结果 ({len(expanded)}个):")
    for i, m in enumerate(expanded[:10]):
        print(f"    {i+1}. {m}")
    if len(expanded) > 10:
        print(f"    ... 还有 {len(expanded) - 10} 个")

    return len(expanded) > 0


def test_graph_info(schools):
    """图谱查询测试"""
    print()
    print("=" * 60)
    print(f"【测试】图谱查询: {schools}")
    print("=" * 60)

    for school in schools:
        prov = get_school_province(school)
        tags = get_school_tags(school)
        print(f"  {school}:")
        print(f"    省份: {prov}")
        print(f"    标签: {tags}")


def test_graphrag(query):
    """GraphRAG 测试"""
    print()
    print("=" * 60)
    print(f"【测试】GraphRAG: {query}")
    print("=" * 60)

    global_hits = global_search(query)
    print(f"  社区摘要 ({len(global_hits)}个):")
    for hit in global_hits[:2]:
        print(f"    [{hit['name']}] {hit['summary'][:60]}...")

    knowledge_hits = knowledge_search(query)
    print(f"  知识库 ({len(knowledge_hits)}个):")
    for k in knowledge_hits[:2]:
        print(f"    {k[:80]}...")


def main():
    print()
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "三层检索引擎全面测试" + " " * 15 + "║")
    print("╚" + "=" * 58 + "╝")

    # 1. 检查数据库
    provinces = check_db()

    passed = 0
    failed = 0

    # 2. 各省份基础测试（选数据量较大的省份）
    big_provinces = [(p, c) for p, c in
                     [(p, int(c)) for p, c in
                      sqlite3.connect(DB_PATH).execute(
                          "SELECT province, COUNT(*) FROM admission GROUP BY province ORDER BY COUNT(*) DESC"
                      ).fetchall()] if c > 1000]

    print()
    print("=" * 60)
    print("【测试1】各省份基础推荐（按位次+专业）")
    print("=" * 60)

    for prov, count in big_provinces[:5]:
        # 根据省份调整测试位次
        if count > 50000:
            rank = 5000
        elif count > 10000:
            rank = 1000
        else:
            rank = 100

        if test_basic(prov, rank, "计算机", f"[数据量:{count:,}]"):
            passed += 1
        else:
            failed += 1

    # 3. 无专业限制测试
    print()
    print("=" * 60)
    print("【测试2】无专业限制测试")
    print("=" * 60)

    for prov, count in big_provinces[:3]:
        if count > 50000:
            rank = 10000
        elif count > 10000:
            rank = 2000
        else:
            rank = 200

        if test_no_keyword(prov, rank, f"[数据量:{count:,}]"):
            passed += 1
        else:
            failed += 1

    # 4. 不同分数段测试
    print()
    print("=" * 60)
    print("【测试3】不同分数段测试（浙江）")
    print("=" * 60)

    # 高分段
    if test_basic("浙江", 1000, "计算机", "[高分段]"):
        passed += 1
    else:
        failed += 1

    # 中分段
    if test_basic("浙江", 10000, "计算机", "[中分段]"):
        passed += 1
    else:
        failed += 1

    # 低分段
    if test_basic("浙江", 30000, "计算机", "[低分段]"):
        passed += 1
    else:
        failed += 1

    # 5. 不同专业测试
    print()
    print("=" * 60)
    print("【测试4】不同专业推荐（浙江 中分段）")
    print("=" * 60)

    majors = ["计算机", "电子信息", "自动化", "经济学", "医学", "法学", "建筑学", "材料"]
    for major in majors:
        if test_basic("浙江", 15000, major):
            passed += 1
        else:
            failed += 1

    # 6. 向量扩展测试
    print()
    print("=" * 60)
    print("【测试5】向量专业扩展")
    print("=" * 60)

    test_majors = [
        ["计算机"],
        ["人工智能", "机器学习"],
        ["电子信息"],
        ["经济学", "金融"],
        ["临床医学"],
    ]
    for majors in test_majors:
        if test_vector_expand(majors):
            passed += 1
        else:
            failed += 1

    # 7. 图谱查询测试
    print()
    print("=" * 60)
    print("【测试6】知识图谱查询")
    print("=" * 60)

    test_schools = ["清华大学", "北京大学", "浙江大学", "复旦大学", "上海交通大学",
                    "哈尔滨工业大学", "西安交通大学", "南京大学", "中国科学技术大学"]
    test_graph_info(test_schools)
    passed += 1

    # 8. GraphRAG 测试
    print()
    print("=" * 60)
    print("【测试7】GraphRAG 检索")
    print("=" * 60)

    queries = [
        "计算机科学与技术专业排名",
        "平行志愿填报技巧",
        "985高校推荐",
        "电子信息类专业分析",
    ]
    for query in queries:
        test_graphrag(query)
        passed += 1

    # 9. 边界测试
    print()
    print("=" * 60)
    print("【测试8】边界条件测试")
    print("=" * 60)

    # 位次为0
    print()
    print("[测试] 位次为0:")
    result = recommend(DB_PATH, [], "浙江", rank=0, keyword="计算机")
    if 'error' in result:
        print("  预期行为: rank=0 被正确拒绝")
        passed += 1
    else:
        print("  异常: rank=0 不应返回结果")
        failed += 1

    # 无效省份
    print()
    print("[测试] 无效省份:")
    result = recommend(DB_PATH, [], "火星", rank=5000, keyword="计算机")
    if 'error' in result:
        print("  预期行为: 无效省份被正确拒绝")
        passed += 1
    else:
        print("  异常: 无效省份不应返回结果")
        failed += 1

    # 10. 小数据量省份测试
    print()
    print("=" * 60)
    print("【测试9】小数据量省份")
    print("=" * 60)

    small_provinces = [(p, c) for p, c in
                        [(p, int(c)) for p, c in
                         sqlite3.connect(DB_PATH).execute(
                             "SELECT province, COUNT(*) FROM admission GROUP BY province ORDER BY COUNT(*) ASC"
                         ).fetchall()] if c < 500]

    for prov, count in small_provinces[:3]:
        if test_basic(prov, 50, "计算机", f"[小数据量:{count}]"):
            passed += 1
        else:
            failed += 1

    # 汇总
    print()
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 20 + "测试汇总" + " " * 24 + "║")
    print("╠" + "=" * 58 + "╣")
    print("║  通过: {0:>3} 项".format(passed) + " " * 40 + "║")
    print("║  失败: {0:>3} 项".format(failed) + " " * 40 + "║")
    if failed == 0:
        print("║" + " " * 18 + "[OK] 全部测试通过!" + " " * 21 + "║")
    else:
        print("║" + " " * 18 + "[!!] {0} 项测试失败".format(failed) + " " * 23 + "║")
    print("╚" + "=" * 58 + "╝")


if __name__ == '__main__':
    main()
