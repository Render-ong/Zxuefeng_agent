# GraphRAG 后端 API 接口规范

## 概述

本服务提供高考志愿填报 AI 对话后端，集成 langgraph 多节点工作流（analyze → generate）、向量检索、SQL 查询录取数据等功能。

## 服务信息

- **服务名称**: xf-graphrag
- **默认端口**: 5000
- **启动命令**: `python api_server.py --host 0.0.0.0 --port 5000`

## 鉴权

除 `/api/health` 和 `/api/auth/login` 外，所有接口均需鉴权：

```
Authorization: Bearer <token>
```

token 由 `POST /api/auth/login` 签发，有效期 7 天。过期返回 401。

---

## API 接口

### 1. 健康检查

`GET /api/health` ｜ 无需鉴权

**响应** (200):
```json
{
  "status": "ok",
  "service": "xf-graphrag"
}
```

### 2. 登录

`POST /api/auth/login` ｜ 无需鉴权

**请求体**:
```json
{
  "code": "wx.login 拿到的 code"
}
```

**响应** (200):
```json
{
  "user_id": "openid 或 dev_user",
  "token": "session_token"
}
```

**错误**:
| 状态码 | 响应 | 说明 |
|--------|------|------|
| 400 | `{"error": "missing code"}` | 缺少 code |
| 401 | `{"error": "登录服务繁忙，请稍后重试"}` | code2session 失败（errcode 非 0 或无 openid） |
| 500 | `{"error": "登录服务超时，请稍后重试"}` | 微信接口超时 |

### 3. 对话

`POST /api/chat` ｜ 需鉴权

**请求体**:
```json
{
  "message": "浙江考生 位次5000 想学计算机",
  "conversation_id": "可选，为空则后端自动创建",
  "user_profile": {
    "name": "张三",
    "province": "浙江",
    "score": "650",
    "rank": "5000",
    "customProfile": "想学计算机"
  },
  "api_config": {
    "key": "sk-xxx（可选，后端 config.json 有 key 时忽略）",
    "model": "deepseek-v4-flash（可选，覆盖后端默认模型）",
    "tavily": "tvly-xxx（可选，后端 config.json 有 key 时忽略）"
  }
}
```

**字段说明**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| message | string | 是 | 用户消息 |
| conversation_id | string | 否 | 对话 ID，为空则后端自动创建；非空时校验归属 |
| user_profile | object | 否 | 用户画像 hint，后端会持久化并拼入消息 |
| api_config | object | 否 | API 配置覆盖（可选，后端 config.json 已有默认值） |
| api_config.key | string | 否 | LLM API Key，仅当后端未配置时生效 |
| api_config.model | string | 否 | 模型名称，始终生效（覆盖后端默认值 `deepseek-v4-flash`） |
| api_config.tavily | string | 否 | Tavily Key，仅当后端未配置时生效 |

**画像注入逻辑**：前端传入的 `user_profile` 字段会合并到后端全局画像（`user_data.db`）。agent 的 analyze 节点从消息中抽取的 province/score/rank 也会回写全局画像。

**响应** (200):
```json
{
  "reply": "根据你的情况...",
  "debug": {
    "intent": "recommend",
    "params": {
      "province": "浙江",
      "rank": 5000,
      "score": 0,
      "subject": "",
      "majors": ["计算机"],
      "schools": [],
      "region_avoid": [],
      "region_pref": [],
      "tags": []
    },
    "pipeline": [],
    "sources": []
  },
  "profile": {
    "province": "浙江",
    "score": 0,
    "rank": 5000,
    "subject": "",
    "majors": ["计算机"],
    "schools": [],
    "region_avoid": [],
    "region_pref": [],
    "tags": [],
    "ask_rounds": 1
  },
  "needs_ask": false,
  "ask_questions": [],
  "conversation_id": "uuid"
}
```

**错误**:
| 状态码 | 响应 | 说明 |
|--------|------|------|
| 400 | `{"error": "API Key 未配置，请在设置页填写或在 config.json 中配置"}` | key 未配置 |
| 400 | `{"error": "message is required"}` | 空消息 |
| 401 | `{"error": "未登录或会话已过期，请重新登录"}` | token 过期 |
| 500 | `{"error": "服务繁忙，请稍后重试", "reply": "服务繁忙，请稍后重试"}` | agent 内部异常 |

### 4. 用户画像

#### 4a. 获取画像

`GET /api/user/profile` ｜ 需鉴权

按当前登录用户的 Bearer token 反查 user_id，返回该用户的全局画像。

**响应** (200):
```json
{
  "user_id": "dev_user",
  "name": "张雪儿",
  "province": "安徽",
  "score": "699",
  "rank": "2",
  "customProfile": "无所谓",
  "updated_at": "2026-06-26T03:52:08.864463"
}
```

**错误**: 404 `{"error": "not found"}` — 用户画像不存在。

#### 4b. 同步画像

`POST /api/user/profile` ｜ 需鉴权

**请求体**:
```json
{
  "name": "张三",
  "province": "浙江",
  "score": "650",
  "rank": "5000",
  "customProfile": "想学计算机"
}
```

**响应** (200): `{"success": true}`

### 5. 对话管理

#### 5a. 列出对话

`GET /api/conversations` ｜ 需鉴权

**响应** (200):
```json
{
  "conversations": [
    {
      "conversation_id": "uuid",
      "user_id": "dev_user",
      "title": "对话 2026-06-26",
      "created_at": "2026-06-26T04:17:03",
      "updated_at": "2026-06-26T04:17:03",
      "message_count": 20,
      "last_message": "最近一条消息摘要"
    }
  ]
}
```

按 `updated_at` 倒序返回。

#### 5b. 创建对话

`POST /api/conversations` ｜ 需鉴权

**请求体** (均可选):
```json
{
  "title": "新对话"
}
```

**响应** (200):
```json
{
  "conversation_id": "uuid"
}
```

#### 5c. 对话详情

`GET /api/conversations/{conv_id}` ｜ 需鉴权

校验归属（当前 user_id 必须是对话创建者）。

**响应** (200):
```json
{
  "conversation": {
    "conversation_id": "uuid",
    "user_id": "dev_user",
    "title": "对话 2026-06-26",
    "created_at": "...",
    "updated_at": "..."
  },
  "messages": [
    {"role": "user", "content": "...", "created_at": "..."},
    {"role": "assistant", "content": "...", "created_at": "..."}
  ],
  "profile": {},
  "summary": {"message_count": 10, "last_message": "..."}
}
```

**错误**: 404 `{"error": "对话不存在或无权访问"}`

#### 5d. 消息历史

`GET /api/conversations/{conv_id}/messages` ｜ 需鉴权

校验归属。返回消息列表，按时间正序。

**查询参数**:
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| limit | int | 50 | 最大返回条数 |

**响应** (200):
```json
{
  "messages": [
    {"role": "user", "content": "...", "created_at": "..."},
    {"role": "assistant", "content": "...", "created_at": "..."}
  ],
  "conversation_id": "uuid"
}
```

**错误**: 404 `{"error": "对话不存在或无权访问"}`

#### 5e. 删除对话

`DELETE /api/conversations/{conv_id}` ｜ 需鉴权

校验归属，级联删除消息和画像。

**响应** (200): `{"success": true}`

**错误**: 404 `{"error": "对话不存在或无权访问"}`

---

## 错误格式

所有错误统一返回 JSON（不返回 HTML）:

```json
{"error": "错误描述"}
```

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 未登录或会话已过期 |
| 404 | 资源不存在或无权访问 |
| 500 | 服务内部错误 |

---

## 后端架构

### 处理流程

```
用户消息 → 画像 hint 拼接 → langgraph agent (analyze → generate) → 回复
```

1. **analyze**: 从消息中抽取省份、位次、分数、专业等考生画像字段
2. **generate**: 基于画像和 RAG 检索结果，调用 LLM 生成回复

### 数据存储

- **admission_clean.db**: 各省份录取数据（只读）
- **data/user/user_data.db**: 用户 session + 全局画像
- **data/user/langgraph_data.db**: 对话、消息、per-conversation 画像

### 配置优先级

API Key / Model / URL 的配置优先级：前端传入 > config.json > 环境变量 > 内置默认值

---

## 部署说明

### 环境要求

- Python 3.9+
- 依赖：见 `requirements-engine.txt`

### 启动服务

```bash
cd GraphRAG
pip install -r requirements-engine.txt
python api_server.py --host 0.0.0.0 --port 5000
```

### 本地开发

未配置 `wx_appid`/`wx_secret` 时需设环境变量 `XF_ALLOW_DEV=1`，此时所有用户统一使用 `dev_user`。

### 生产环境建议

1. 使用 Gunicorn 替代 Flask 内置服务器：`gunicorn -w 4 -b 0.0.0.0:5000 api_server:app`
2. 配置 Nginx 反向代理 + HTTPS
3. 在 config.json 填写 `wx_appid` + `wx_secret`（禁用 XF_ALLOW_DEV）
