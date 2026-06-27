"""HTTP API 服务 — 小程序前端调用入口"""
import json
import logging
import os
import sqlite3
import sys
import time
import uuid
import urllib.request
import urllib.parse
from datetime import datetime
from functools import wraps

# 确保项目根目录和当前目录在 path 中
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, HERE)

# 日志配置：import 即生效，必须在所有业务 import 之前
import log_setup  # noqa: F401 — 项目根 logs/app.log
log = logging.getLogger("api_server")

from flask import Flask, request, jsonify, g
from werkzeug.exceptions import HTTPException
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import importlib.util

# ponytail: langgraph/ 目录与 pip 安装的 langgraph 包同名，直接 import 会加载 pip 包。
# 用 importlib 从文件路径加载，绕过包名冲突。
_LG_DIR = os.path.join(HERE, '..', 'langgraph')
sys.path.insert(0, _LG_DIR)
_lg_spec = importlib.util.spec_from_file_location('lg_agent', os.path.join(_LG_DIR, 'agent.py'))
lg_agent = importlib.util.module_from_spec(_lg_spec)
sys.modules['lg_agent'] = lg_agent
_lg_spec.loader.exec_module(lg_agent)

# langgraph 子模块：sys.path 已加 _LG_DIR，可直接 import
# ponytail: 之前代码使用了 lg_user_manager / lg_db 但未导入，所有 /api/conversations* 会 NameError。
import user_manager as lg_user_manager
import database as lg_db
import llm_config as lg_llm_config

app = Flask(__name__)
# ponytail: 小程序原生请求不依赖 CORS（非浏览器环境），无需配置。
# 若未来需要支持 H5 网页调用，再按需安装 flask-cors 并限定 origins。

# ponytail: 请求限流，防止恶意刷接口打爆 LLM 额度。
# storage_uri 用内存即可（单进程），多 worker 需迁 Redis。
limiter = Limiter(key_func=get_remote_address, app=app, default_limits=["60/minute"], storage_uri="memory://")


# ── 请求日志中间件 ──
@app.before_request
def _log_request():
    g.req_start = time.time()


@app.after_request
def _log_response(response):
    latency_ms = int((time.time() - getattr(g, 'req_start', time.time())) * 1000)
    # 跳过健康检查的日志（高频且无业务价值）
    path = request.path
    if path == '/api/health':
        return response
    uid = getattr(g, 'user_id', '')[:8]
    log.info(f'{request.method} {path} → {response.status_code} {latency_ms}ms user={uid}…' if uid
             else f'{request.method} {path} → {response.status_code} {latency_ms}ms')
    return response


# 全局异常处理器：未捕获异常统一返回 JSON，避免 Flask 默认 HTML 500 破坏前端 JSON.parse
# ponytail: 生产环境隐藏 detail（str(e) 可能含路径/SQL/栈帧片段），仅写日志。
# 升级路径：接入 structlog + 接入 Sentry 上报。
@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    # HTTPException（404/405 等）交给 Flask 默认处理器，避免 404 被吞成 500。
    if isinstance(e, HTTPException):
        return jsonify({'error': e.description}), e.code
    log.error(f'unhandled exception: {e}', exc_info=True)
    return jsonify({'error': '服务内部错误，请稍后重试'}), 500

DB_PATH = os.path.join(HERE, 'admission_clean.db')
CONFIG_PATH = os.path.join(HERE, 'config.json')


def load_config():
    """从 config.json 加载配置，环境变量优先"""
    default = {
        'llm_api_url': os.environ.get('LLM_API_URL', 'https://api.deepseek.com'),
        'llm_api_key': os.environ.get('LLM_API_KEY', ''),
        'llm_model': os.environ.get('LLM_MODEL', 'deepseek-chat'),
        'tavily_key': os.environ.get('TAVILY_KEY', ''),
        'wx_appid': os.environ.get('WX_APPID', ''),
        'wx_secret': os.environ.get('WX_SECRET', ''),
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            # 环境变量优先，只用 config.json 的值做兜底
            for k, v in cfg.items():
                if not default.get(k):
                    default[k] = v
        except Exception:
            pass
    return default


_cfg = load_config()

# ponytail: 启动期硬校验 wx_appid/wx_secret，缺失则拒绝启动。
# 缺失时回退 dev_user 会让所有用户共享同一画像，且任意客户端可伪造 X-User-Id 读改 dev_user 画像。
# 本地开发用环境变量 XF_ALLOW_DEV=1 绕过，生产环境禁止绕过。
if not _cfg.get('wx_appid') or not _cfg.get('wx_secret'):
    if os.environ.get('XF_ALLOW_DEV') != '1':
        log.critical('config.json 缺少 wx_appid/wx_secret，生产环境不可启动。')
        log.critical('本地开发请设置环境变量 XF_ALLOW_DEV=1 后再启动。')
        sys.exit(1)
    log.warning('wx_appid 缺失，XF_ALLOW_DEV=1 已启用 dev_user 回退（仅本地开发，不可上线）。')

# 用户私有数据单独建库，避免与公共知识库 admission_clean.db 混在一起。
# ponytail: 用户数据统一存放项目根 data/user/，便于备份/迁移。
# 升级路径：迁 PostgreSQL 后此目录废弃，改用 DB 连接串。
USER_DB_PATH = os.path.join(HERE, '..', 'data', 'user', 'user_data.db')


def init_user_db():
    """初始化用户数据表（画像按 user_id 隔离，杜绝按 name 读他人画像）"""
    os.makedirs(os.path.dirname(USER_DB_PATH), exist_ok=True)
    with sqlite3.connect(USER_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                province TEXT,
                score TEXT,
                rank TEXT,
                customProfile TEXT,
                api_key TEXT,
                tavily_key TEXT,
                updated_at TEXT
            )
        """)
        # ponytail: 迁移旧表——添加 api_key / tavily_key 列（已存在则忽略）。
        for col in ('api_key', 'tavily_key'):
            try:
                conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在
        # 会话表：登录签发的 token 必须落库校验，杜绝 X-User-Id 头可任意伪造
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        # 清理过期 session，ponytail: 每次启动时扫一次，避免 DB 膨胀。
        conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")


# ponytail: session TTL 7 天，过期需重新登录。
# 升级路径：迁 Postgres 后用 redis 做 session store，支持主动撤销。
SESSION_TTL_SECONDS = 7 * 24 * 3600


def create_session(user_id: str) -> str:
    """登录成功后签发 token 并落库"""
    token = uuid.uuid4().hex
    now = datetime.now()
    expires_at = datetime.fromtimestamp(now.timestamp() + SESSION_TTL_SECONDS)
    with sqlite3.connect(USER_DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (token, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires_at.isoformat())
        )
    return token


def verify_session(token: str) -> str | None:
    """校验 Bearer token，返回 user_id 或 None"""
    if not token:
        return None
    with sqlite3.connect(USER_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row['expires_at']) < datetime.now():
            return None
    except Exception:
        return None
    return row['user_id']


def get_profile_row(user_id: str) -> dict | None:
    """按 user_id 读取画像（不再按 name）"""
    with sqlite3.connect(USER_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_profile(user_id: str, data: dict) -> None:
    """按 user_id 写入/更新画像"""
    now = datetime.now().isoformat()
    with sqlite3.connect(USER_DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_profiles "
            "(user_id, name, province, score, rank, customProfile, api_key, tavily_key, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, data.get('name', ''), data.get('province', ''),
             data.get('score', ''), data.get('rank', ''),
             data.get('customProfile', ''), data.get('api_key', ''),
             data.get('tavily_key', ''), now)
        )


# ponytail: 鉴权中间件严格校验 Bearer token（登录签发的 token 落 sessions 表）。
# 废弃 X-User-Id 头直读 —— 该头可被任意客户端伪造，会导致 IDOR + PII 泄漏。
# 升级路径：迁 Postgres + Redis 后用 redis 做 session store，支持主动撤销与刷新。
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        token = auth[7:].strip() if auth.startswith('Bearer ') else ''
        user_id = verify_session(token)
        if not user_id:
            return jsonify({'error': '未登录或会话已过期，请重新登录'}), 401
        g.user_id = user_id
        return f(*args, **kwargs)
    return decorated


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("5/minute")
def auth_login():
    """微信静默登录。

    支持两种模式：
    1. callContainer 内网调用：微信自动在 header 注入 x-wx-openid，直接读取。
    2. 公网 wx.request：前端传 wx.login 的 code，后端调 code2session 换 openid。

    响应: {"user_id": "openid", "token": "session_token"}

    ponytail: 开发期未配置 wx_appid/wx_secret 且没有 x-wx-openid 时，回退为 dev_user。
    """
    # 1) 优先 callContainer 内网身份
    openid = request.headers.get('x-wx-openid', '').strip()
    if openid:
        log.info(f'login via callContainer openid={openid[:8]}…')
        token = create_session(openid)
        return jsonify({'user_id': openid, 'token': token})

    # 2) 公网 code2session 模式
    data = request.get_json(silent=True) or {}
    code = data.get('code', '').strip()
    if not code:
        return jsonify({'error': 'missing code'}), 400

    appid = _cfg.get('wx_appid', '')
    secret = _cfg.get('wx_secret', '')

    if appid and secret:
        url = (
            'https://api.weixin.qq.com/sns/jscode2session'
            f'?appid={appid}&secret={secret}&js_code={code}'
            '&grant_type=authorization_code'
        )
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                result = json.loads(resp.read())
            if result.get('errcode', 0) != 0:
                log.error(f'code2session error: {result.get("errmsg", "unknown")}')
                return jsonify({'error': '登录服务繁忙，请稍后重试'}), 401
            openid = result.get('openid', '')
            if not openid:
                return jsonify({'error': '登录服务繁忙，请稍后重试'}), 401
        except Exception as e:
            log.error(f'code2session exception: {e}')
            return jsonify({'error': '登录服务超时，请稍后重试'}), 500
    else:
        # ponytail: 开发模式用固定 ID
        openid = 'dev_user'

    token = create_session(openid)
    log.info(f'login ok user={openid[:8]}…')
    return jsonify({'user_id': openid, 'token': token})


@app.route('/api/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({'status': 'ok', 'service': 'xf-graphrag'})


@app.route('/api/chat', methods=['POST'])
@require_auth
@limiter.limit("10/minute")
def chat():
    """
    对话接口（接通 langgraph agent，多轮上下文走 checkpoint，不再靠前端传 history）

    请求体：
    {
        "message": "用户消息",
        "conversation_id": "可选，为空则后端自动创建",
        "api_config": {  // 可选，仅 model 生效
            "model": "deepseek-chat"
        }
    }

    响应：
    {
        "reply": "AI回复内容",
        "intent": "recommend",
        "profile": {...},
        "needs_ask": false,
        "ask_questions": [],
        "conversation_id": "..."
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'invalid request body'}), 400

    message = data.get('message', '').strip()
    if not message:
        return jsonify({'error': 'message is required'}), 400

    # ponytail: 透传模式 — 前端传入的 api_config 直接透传给 agent.run()，
    # 不写入全局配置文件，避免用户 A 的 key 被用户 B 使用。
    # 后端 config.json 有 key 时作为 fallback（前端没传 key 时生效）。
    # 升级路径：正式运营后改为 DB 存储模式（方案 B），key 落库 + 加密。
    frontend_cfg = data.get('api_config') or {}
    _lg_cfg = lg_llm_config.get_api_config()
    if not _lg_cfg.get('key') and not frontend_cfg.get('key'):
        log.error('API Key 未配置，请在 config.json 中填写 llm_api_key 或在前端设置页填写')
        return jsonify({'error': 'API Key 未配置，请在设置页填写或在 config.json 中配置'}), 400

    # 画像 hint 注入（langgraph agent 的 analyze 节点会从消息抽取画像）
    # ponytail: customProfile 是自由文本（可能含家庭背景等上下文信息），
    # 只在前端本次请求显式传入时注入，不从 DB 自动读取——
    # 避免旧对话的 customProfile 泄漏到新对话（DB 按 user_id 存，跨对话共享）。
    # 结构化字段（province/score/rank）是事实数据，跨对话有效，继续从 DB 注入。
    row = get_profile_row(g.user_id)
    user_profile = {}
    if row:
        user_profile = {k: row[k] for k in ('name', 'province', 'score', 'rank') if row.get(k)}
    fe_profile = data.get('user_profile') or {}
    profile_changed = False
    for k in ('name', 'province', 'score', 'rank', 'customProfile'):
        if fe_profile.get(k) and fe_profile[k] != user_profile.get(k):
            user_profile[k] = fe_profile[k]
            profile_changed = True
    # 前端传入的画像字段立即持久化到 user_data.db（避免每次重登丢失）
    if profile_changed:
        upsert_profile(g.user_id, user_profile)
    # 本次请求显式传入的 customProfile 才注入 hint（不从 DB 读旧值）
    custom_hint = fe_profile.get('customProfile', '')
    profile_hint = ''
    if user_profile or custom_hint:
        parts = []
        if user_profile.get('province'):
            parts.append(f"我是{user_profile['province']}考生")
        if user_profile.get('rank'):
            parts.append(f"位次{user_profile['rank']}")
        if user_profile.get('score'):
            parts.append(f"分数{user_profile['score']}")
        if custom_hint:
            parts.append(custom_hint)
        if parts:
            profile_hint = '（' + '，'.join(parts) + '）'
    enhanced_message = message
    if profile_hint and not any(kw in message for kw in ['位次', '分数', '我是']):
        enhanced_message = message + profile_hint

    conversation_id = (data.get('conversation_id') or '').strip() or None

    log.info(f'CHAT user={g.user_id[:8]}… conv={conversation_id or "new"} msg="{message[:40]}"')

    # 调 langgraph agent（多轮上下文走 checkpoint，归属校验在 agent.run 内做）
    try:
        result = lg_agent.run(
            message=enhanced_message,
            user_id=g.user_id,
            conversation_id=conversation_id,
            mode='gaokao',
            api_config=frontend_cfg or None,
        )
    except Exception as e:
        log.error(f'agent.run failed: {e}', exc_info=True)
        return jsonify({'error': '服务繁忙，请稍后重试', 'reply': '服务繁忙，请稍后重试'}), 500

    log.info(f'CHAT done intent={result.get("intent")} needs_ask={result.get("needs_ask")} reply={len(result.get("reply",""))} chars')

    # agent 可能更新了画像（analyze 节点从消息抽取了 province/score/rank 等），同步回 user_data.db
    agent_profile = result.get('profile') or {}
    if agent_profile:
        sync_fields = {}
        for k in ('province', 'score', 'rank'):
            v = agent_profile.get(k)
            if v and str(v) != str(user_profile.get(k, '')):
                sync_fields[k] = str(v) if not isinstance(v, str) else v
        if sync_fields:
            merged = dict(user_profile)
            merged.update(sync_fields)
            upsert_profile(g.user_id, merged)
            result['profile'] = {**agent_profile, **{k: merged[k] for k in sync_fields}}

    # ponytail: 返回给前端的 profile 清理掉 0/空字符串，避免前端误把无效值覆盖本地。
    if result.get('profile'):
        result['profile'] = {k: v for k, v in result['profile'].items() if v not in (None, '', 0, '0')}

    return jsonify(result)


# ─────────────────────────────────────────────
#  对话管理接口（接通 langgraph 的对话持久化）
# ─────────────────────────────────────────────

@app.route('/api/conversations', methods=['GET'])
@require_auth
def list_conversations():
    """获取当前用户的所有对话列表"""
    lg_user_manager.init_user_system()
    convs = lg_agent.get_user_conversations(g.user_id)
    return jsonify({'conversations': convs})


@app.route('/api/conversations', methods=['POST'])
@require_auth
def create_conversation():
    """创建新对话，返回 conversation_id"""
    lg_user_manager.init_user_system()
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    conv_id = lg_user_manager.start_conversation(g.user_id, title)
    return jsonify({'conversation_id': conv_id})


@app.route('/api/conversations/<conv_id>', methods=['GET'])
@require_auth
def get_conversation_detail(conv_id):
    """获取对话详情（含消息历史，校验归属）"""
    lg_user_manager.init_user_system()
    detail = lg_agent.get_conversation_detail(g.user_id, conv_id)
    if not detail:
        return jsonify({'error': '对话不存在或无权访问'}), 404
    return jsonify(detail)


@app.route('/api/conversations/<conv_id>/messages', methods=['GET'])
@require_auth
def get_conversation_messages(conv_id):
    """获取对话的消息历史（校验归属）"""
    lg_user_manager.init_user_system()
    conv = lg_db.get_conversation(conv_id)
    if not conv or conv['user_id'] != g.user_id:
        return jsonify({'error': '对话不存在或无权访问'}), 404
    limit = request.args.get('limit', 50, type=int)
    messages = lg_user_manager.get_history_messages(g.user_id, conv_id, limit)
    return jsonify({'messages': messages, 'conversation_id': conv_id})


@app.route('/api/conversations/<conv_id>', methods=['DELETE'])
@require_auth
def delete_conversation(conv_id):
    """删除对话（校验归属，防跨用户删他人对话）"""
    lg_user_manager.init_user_system()
    ok = lg_db.delete_conversation(conv_id, g.user_id)
    if not ok:
        return jsonify({'error': '对话不存在或无权访问'}), 404
    return jsonify({'success': True})


@app.route('/api/user/profile', methods=['POST'])
@require_auth
def save_profile():
    """
    用户画像同步接口（按当前登录用户 user_id 存储，防跨用户覆盖）

    请求体：
    {
        "name": "张三",
        "province": "浙江",
        "score": "650",
        "rank": "5000",
        "customProfile": "想学计算机"
    }

    响应：
    {"success": true}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'invalid request body'}), 400

    log.info(f'PROFILE SAVE user={g.user_id[:8]}… api_key={"有" if data.get("api_key") else "空"} tavily_key={"有" if data.get("tavily_key") else "空"}')
    upsert_profile(g.user_id, data)
    return jsonify({'success': True})


@app.route('/api/user/profile', methods=['GET'])
@require_auth
def get_profile():
    """获取当前登录用户的画像（按 user_id 隔离，不再按 name 查）"""
    row = get_profile_row(g.user_id)
    if row:
        log.info(f'PROFILE GET user={g.user_id[:8]}… api_key={"有" if row.get("api_key") else "空"} tavily_key={"有" if row.get("tavily_key") else "空"}')
        return jsonify(row)
    log.info(f'PROFILE GET user={g.user_id[:8]}… not found')
    return jsonify({'error': 'not found'}), 404


# ponytail: gunicorn 导入本模块时不会执行 __main__，因此初始化必须在模块加载时完成。
init_user_db()
lg_user_manager.init_user_system()
log.info(f'数据库路径: {DB_PATH}')
log.info('接口: auth/login, chat, conversations*, user/profile, health')


# ponytail: 后台预热 bge-small-zh 嵌入模型。
# 根因：vector_store._get_model() 懒加载，首次 RAG 请求才从 HuggingFace 拉模型（网络+磁盘+内存 ~90s），
# 远超前端 60s 超时。启动时后台线程预热，把冷启动成本从首个请求转移到启动阶段。
# 升级路径：模型常驻后可移除；若改用 API 化 embedding 服务则整段删除。
def _preload_embedding_model():
    import threading
    def _warm():
        try:
            from engine.vector_store import _get_model
            _get_model()
            log.info('嵌入模型预热完成')
        except Exception as e:
            log.warning(f'嵌入模型预热失败（首次请求将回退到懒加载）: {e}')
    threading.Thread(target=_warm, name='embedding-preload', daemon=True).start()


_preload_embedding_model()


if __name__ == '__main__':
    # ponytail: 云托管注入 PORT 环境变量，本地默认 0.0.0.0:5000
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))

    log.info(f'API 服务启动: http://{host}:{port}')
    app.run(host=host, port=port, debug=False)
