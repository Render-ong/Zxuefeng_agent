"""统一对话编排 — POST /api/chat 入口

⚠️ DEPRECATED（2026-06-26）：api_server.py 已改用 langgraph agent.run() 路线，
此模块的 handle_chat() 不再被任何调用方使用。
engine/recommend.py、engine/web_search.py 等子模块仍被 langgraph nodes 引用，不受影响。
MVP 上线后可安全删除此文件。保留是为了回退兼容。
"""
import json
import logging
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# ponytail: 导入公共 LLM 客户端，统一超时/错误处理
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'langgraph'))
from llm_client import call_llm

log = logging.getLogger("graphrag.chat")

from engine.extractor import extract_info, classify_intent
from engine.recommend import recommend as recommend_full
from engine import graphrag
from engine.web_search import web_search
from engine.prompt_builder import build_messages, format_recommend_result


def _build_web_context(message, params, recommend_result, tavily_key):
    if not message:
        return ''
    queries = [message]
    if params.get('province') and params.get('majors'):
        queries.append(
            f"{params['province']} {params['majors'][0]} 专业 录取分数线 2025"
        )
    if recommend_result:
        schools = []
        for tier in ('chong', 'wen', 'bao'):
            for r in (recommend_result.get(tier) or [])[:3]:
                if r.get('school') and r.get('school') != '【死命令】':
                    schools.append(r['school'])
        for s in schools[:4]:
            queries.append(f"{s} {params.get('province', '')} 王牌专业 就业")

    seen, all_results = set(), []
    unique_queries = []
    for q in queries[:5]:
        if q in seen:
            continue
        seen.add(q)
        unique_queries.append(q)

    # ponytail: 多 query 并行搜索（原串行最坏 5×20s=100s 超 Nginx 60s 网关）。
    # 升级路径：Tavily 支持单请求多 query，可省一次往返。
    if not unique_queries:
        return ''
    with ThreadPoolExecutor(max_workers=min(5, len(unique_queries))) as ex:
        futs = [ex.submit(web_search, q, tavily_key, 2) for q in unique_queries]
        for fut in as_completed(futs):
            try:
                for line in fut.result():
                    if line not in all_results:
                        all_results.append(line)
            except Exception as e:
                log.warning(f'web_search parallel failed: {e}')
    if not all_results:
        return ''
    return '【联网搜索·仅供参考】\n' + '\n'.join(f'· {x[:300]}' for x in all_results[:12])


def handle_chat(db_path, user_data, message, mode='gaokao', history=None, api_config=None):
    """
    服务端统一管线：抽参 → 检索 → GraphRAG → 联网 → LLM
    返回 {reply, debug}
    """
    api_config = api_config or {}
    if not api_config.get('key'):
        return {'error': 'missing API key', 'reply': '请先配置 API Key'}

    pipeline = ['extract']
    params = extract_info(message)
    intent = classify_intent(message, params)
    pipeline.append(f'intent:{intent}')

    data_context = ''
    recommend_result = None
    web_context = ''

    if mode == 'gaokao' and intent in ('recommend', 'compare', 'major_info'):
        keyword = ','.join(params.get('majors') or [])
        tags = ','.join(params.get('tags') or [])
        has_rank_data = params.get('province') and (params.get('rank') or params.get('score'))
        if has_rank_data:
            # ponytail: DB/向量异常时降级为空 data_context，保证 LLM 仍能回复
            try:
                recommend_result = recommend_full(
                    db_path, user_data,
                    params['province'], params.get('rank', 0), params.get('score', 0),
                    params.get('subject', ''),
                    keyword=keyword,
                    school=(params.get('schools') or [''])[0],
                    query_text=message,
                    region_avoid=','.join(params.get('region_avoid') or []),
                    region_pref=','.join(params.get('region_pref') or []),
                    tags_required=tags,
                )
                pipeline.extend(['vector_expand', 'sql', 'graph_filter', 'rerank'])
                if 'error' not in recommend_result:
                    data_context = format_recommend_result(recommend_result)
                else:
                    data_context = f"【检索提示】{recommend_result.get('error')}"
            except Exception as e:
                log.warning(f'recommend_full failed: {e}')
                pipeline.append('sql_skip:error')
                data_context = f"【检索异常】{e}"
        elif intent == 'compare':
            pipeline.append('sql_skip:compare_no_rank')

    graph_context = ''
    graphrag_detail = None
    if mode == 'gaokao' and graphrag.is_ready():
        # ponytail: 图谱加载/查询异常时降级为空，避免 /api/chat 整体崩
        try:
            graph_context = graphrag.build_context(message, params, intent)
            if graph_context:
                pipeline.append('graphrag')
            if intent in ('compare', 'policy', 'major_info'):
                graphrag_detail = graphrag.search(message, intent=intent, params=params)
        except Exception as e:
            log.warning(f'graphrag failed: {e}')
            pipeline.append('graphrag_skip:error')

    if mode == 'gaokao' and intent in ('recommend', 'compare', 'major_info', 'policy'):
        web_context = _build_web_context(
            message, params, recommend_result, api_config.get('tavily'),
        )
        if web_context:
            pipeline.append('web_search')

    messages = build_messages(
        mode, message, history,
        data_context=data_context,
        graph_context=graph_context,
        web_context=web_context,
        params=params,
    )
    pipeline.append('llm')

    try:
        reply = call_llm(api_config, messages)
    except Exception as e:
        return {'error': str(e), 'reply': f'LLM 调用失败：{e}\n请检查 API 设置'}

    debug = {
        'intent': intent,
        'params': params,
        'pipeline': pipeline,
        'sql_stats': {
            'chong': len((recommend_result or {}).get('chong') or []),
            'wen': len((recommend_result or {}).get('wen') or []),
            'bao': len((recommend_result or {}).get('bao') or []),
        } if recommend_result else {},
        'graphrag': graphrag_detail,
        'sources': [],
    }
    if data_context:
        debug['sources'].append('db')
    if graph_context:
        debug['sources'].append('graph')
    if web_context:
        debug['sources'].append('web')

    return {'reply': reply, 'debug': debug}
