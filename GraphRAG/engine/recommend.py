"""推荐编排 — SQL + 向量扩展 + 图过滤 + 重排"""
from engine.sql_recommend import recommend_from_db, recommend_from_user_data
from engine import vector_store
from engine import graph_store


def recommend(db_path, user_data, province, rank=0, score=0, category='',
              major='', keyword='', school='', query_text='',
              region_avoid=None, region_pref=None, tags_required=None):
    """
    统一推荐入口：
    1. 自定义 Excel 优先
    2. 向量扩展 keyword
    3. SQL 冲稳保
    4. 图过滤（地域/标签）
    5. 向量重排（有 query_text 时）
    """
    custom = recommend_from_user_data(user_data, province, rank, score, category)
    if custom:
        return custom

    original_kw = keyword
    expanded_kw = vector_store.expand_keyword(keyword) if keyword else keyword

    result = recommend_from_db(
        db_path, province, rank, score, category, major, expanded_kw, school,
    )
    if 'error' in result:
        return result

    if expanded_kw != original_kw:
        result['keyword_original'] = original_kw
        result['keyword_expanded'] = expanded_kw

    result = graph_store.filter_result(
        result,
        region_avoid=region_avoid,
        region_pref=region_pref,
        tags_required=tags_required,
    )

    if query_text:
        result = vector_store.rerank_result(result, query_text)

    result['vector_index'] = vector_store.is_ready()
    result['graph_index'] = graph_store.is_ready()
    return result
