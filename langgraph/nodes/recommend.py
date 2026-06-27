"""recommend 节点 — 冲稳保推荐，复用 xuefeng-agent-zjy 的 RAG 引擎"""
import sys
import os
import json
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "GraphRAG"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llm_config import get_api_config
from llm_client import call_llm as _call_llm

log = logging.getLogger("lg_agent.recommend")

from engine.recommend import recommend as recommend_full
from engine.prompt_builder import format_recommend_result
from engine.web_search import web_search as _web_search


DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "GraphRAG", "admission_clean.db"
)


# ponytail: admission_clean.db 在 .dockerignore 里被 *.db 排除，云托管镜像只有 .gz。
# 模块加载时检测：DB 缺失或被清空（0 字节）→ 从 .gz 原子解压恢复。
# 已存在则跳过，不影响本地测试速度。升级路径：迁 PostgreSQL 后此逻辑废弃。
def _ensure_db():
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        return
    gz_path = DB_PATH + ".gz"
    if not os.path.exists(gz_path) or os.path.getsize(gz_path) == 0:
        log.error(f"admission_clean.db 缺失且 .gz 也不可用: {DB_PATH}")
        return  # 让下游 SQL 报错，触发 web/llm fallback
    try:
        import gzip, shutil, tempfile
        log.info(f"extracting {gz_path} -> {DB_PATH}")
        # 原子写：先解压到临时文件，再 rename，避免并发解压产生半文件
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(DB_PATH), suffix=".tmp"
        )
        os.close(fd)
        try:
            with gzip.open(gz_path, "rb") as f_in, open(tmp_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.replace(tmp_path, DB_PATH)
            log.info(f"admission_clean.db restored: {os.path.getsize(DB_PATH)} bytes")
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        log.exception("extract admission_clean.db failed")


_ensure_db()


def _db_search(profile: dict, user_message: str) -> dict | None:
    """数据库检索"""
    province = profile.get("province", "")
    rank = profile.get("rank", 0)
    score = profile.get("score", 0)
    subject = profile.get("subject", "")

    if not province or not (rank or score):
        return None

    majors = profile.get("majors", [])
    keyword = ",".join(majors) if majors else ""
    schools = profile.get("schools", [])
    school = schools[0] if schools else ""
    region_avoid = ",".join(profile.get("region_avoid", []))
    region_pref = ",".join(profile.get("region_pref", []))
    tags_required = ",".join(profile.get("tags", []))

    try:
        result = recommend_full(
            DB_PATH,
            [],  # user_data 暂不使用自定义 Excel
            province,
            rank,
            score,
            subject,
            keyword=keyword,
            school=school,
            query_text=user_message,
            region_avoid=region_avoid,
            region_pref=region_pref,
            tags_required=tags_required,
        )
    except Exception:
        # ponytail: 异常详情仅写日志，不塞进 result 透传到下游 generate prompt（可能含 SQL/路径）。
        # 返回 None 触发联网/大模型兜底链路。
        log.exception("recommend_full failed province=%s rank=%s", province, rank)
        return None

    return result


def _web_fallback(profile: dict, api_config: dict) -> str:
    """数据库检索失败时，联网搜索兜底"""
    province = profile.get("province", "")
    rank = profile.get("rank", 0)
    score = profile.get("score", 0)
    majors = profile.get("majors", [])
    major = majors[0] if majors else ""
    subject = profile.get("subject", "")

    query = f"{province} {subject} {major} 2025 录取分数线 冲稳保推荐"
    if rank:
        query = f"{province} {subject}位次{rank} {major} 2025 冲稳保"

    tavily_key = api_config.get("tavily", "")
    try:
        results = _web_search(query, tavily_key=tavily_key, n=3)
        if results:
            return "【联网检索兜底】\n" + "\n".join(f"- {r}" for r in results if r)
    except Exception:
        pass
    return ""


def _llm_fallback(profile: dict, api_config: dict) -> str:
    """数据库和联网都失败时，大模型生成兜底推荐"""
    province = profile.get("province", "")
    rank = profile.get("rank", 0)
    score = profile.get("score", 0)
    subject = profile.get("subject", "")
    majors = profile.get("majors", [])
    major = ",".join(majors) if majors else "不限"
    region_pref = ",".join(profile.get("region_pref", [])) or "不限"
    region_avoid = ",".join(profile.get("region_avoid", [])) or "无"
    career = profile.get("career_goal", "不限")
    family = profile.get("family_bg", "不限")

    prompt = f"""你是高考志愿规划专家。请根据以下信息，直接给出冲稳保推荐，不需要任何开场白。

【考生信息】
- 省份：{province}
- 选科：{subject}
- 位次：{rank}
- 分数：{score}
- 意向专业：{major}
- 偏好地域：{region_pref}
- 排斥地域：{region_avoid}
- 就业目标：{career}
- 家庭背景：{family}

【输出格式】
冲（2-3所）：学校名 + 专业 + 为什么能冲
稳（2-3所）：学校名 + 专业 + 为什么稳
保（2-3所）：学校名 + 专业 + 为什么保底

注意：基于你的知识推荐，分数位次请尽量准确。如果不确定具体数据，请标注"仅供参考"。"""

    if not api_config.get("key"):
        return ""

    try:
        messages = [
            {"role": "system", "content": "你是高考志愿规划专家，基于你的知识给出冲稳保推荐。"},
            {"role": "user", "content": prompt},
        ]
        reply = _call_llm(api_config, messages, temperature=0.7, max_tokens=1000)
        return "【大模型兜底推荐】\n" + reply
    except Exception:
        return ""


def recommend_node(state: dict) -> dict:
    """冲稳保推荐，数据库优先，失败时联网兜底，再失败大模型兜底"""
    profile = state.get("profile", {})
    user_message = state.get("user_message", "")
    _global = get_api_config()
    _req = state.get("api_config") or {}
    api_config = {**_global, **{k: v for k, v in _req.items() if v}}

    # 第一步：数据库检索
    result = _db_search(profile, user_message)
    if result and "error" not in result:
        chong = len(result.get("chong", []))
        wen = len(result.get("wen", []))
        bao = len(result.get("bao", []))
        log.info(f'db ok 冲{chong} 稳{wen} 保{bao}')
        ctx = format_recommend_result(result)
        return {
            "recommend_result": result,
            "data_context": ctx,
        }

    log.warning(f'db miss/fail, trying web fallback')

    # 第二步：联网检索兜底
    web_ctx = _web_fallback(profile, api_config)
    if web_ctx:
        log.info('web fallback ok')
        return {
            "recommend_result": result or {},
            "data_context": web_ctx,
        }

    # 第三步：大模型生成兜底
    llm_ctx = _llm_fallback(profile, api_config)
    if llm_ctx:
        log.info('llm fallback ok')
        return {
            "recommend_result": result or {},
            "data_context": llm_ctx,
        }

    # 全部失败
    return {
        "recommend_result": result or {},
        "data_context": "",
    }
