# LangGraph Agent 模块说明

> 本目录实现基于 LangGraph 的多轮对话 Agent，负责考生画像收集、意图识别、
> 检索编排和回复生成。修改任何 node 或状态定义前必须阅读此文档。

---

## 1. 图结构

```
                    ┌─────────┐
                    │ analyze │  ← 入口节点
                    └────┬────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
         needs_ask    recommend   compare/explain/policy/chat
              │          │          │
              ▼          ▼          ▼
           ┌─────┐  ┌───────────┐  ┌─────────┐
           │ ask │  │ recommend │  │ compare │  │ explain │  │ policy │
           └──┬──┘  └─────┬─────┘  └────┬────┘
              │           │              │
              │           └──────┬───────┘
              ▼                  ▼
             END           ┌─────────┐
                           │graphrag │  ← 图谱 + 向量检索
                           └────┬────┘
                                ▼
                          ┌───────────┐
                          │web_search │  ← 联网搜索（可选）
                          └─────┬─────┘
                                ▼
                           ┌─────────┐
                           │generate │  ← LLM 生成回复
                           └────┬────┘
                                ▼
                               END
```

**路由逻辑**（`route_after_analyze`）：
- `needs_ask == True` → `ask`（追问，结束本轮）
- `intent == "recommend"` → `recommend`
- `intent == "compare"` → `compare`
- `intent == "explain"` → `explain`
- `intent == "policy"` → `policy`
- 其他（chat/collect）→ 直接 `graphrag`

---

## 2. 文件结构

```
langgraph/
├── agent.py              # 图构建 + run() 入口 + checkpoint 管理
├── state.py              # AgentState 定义 + profile 合并策略
├── database.py           # SQLite 表结构 + CRUD（对话/消息/画像）
├── user_manager.py       # 用户/对话/消息的业务封装
├── llm_config.py         # LLM API 配置加载（多源优先级）
├── requirements.txt      # LangGraph 专用依赖
├── run.py                # CLI 调试入口
├── test_multi_turn.py    # 多轮对话测试
└── nodes/
    ├── __init__.py       # 导出所有节点
    ├── analyze.py        # 画像抽取 + 意图识别
    ├── ask.py            # 追问生成
    ├── recommend.py      # 推荐编排（调用 engine/recommend.py）
    ├── compare.py        # 学校对比
    ├── explain.py        # 概念解释
    ├── policy.py         # 政策解读
    ├── graphrag.py       # 图谱 + 向量检索
    ├── web_search.py     # Tavily 联网搜索
    └── generate.py       # LLM 回复生成
```

---

## 3. AgentState 定义

```python
class AgentState(TypedDict):
    messages: Annotated[list, operator.add]   # 消息累积（自动追加）
    user_message: str                         # 当前用户消息
    mode: str                                 # "gaokao" | "fun"

    user_id: str            # 微信 openid
    conversation_id: str    # 对话 ID

    profile: Annotated[dict, merge_profile]   # 考生画像（自动合并）
    intent: str             # recommend / compare / explain / policy / chat / collect
    needs_ask: bool         # 是否需要追问
    ask_questions: list     # 追问问题列表

    recommend_result: dict  # 推荐结果
    data_context: str       # SQL 检索上下文
    graph_context: str      # 图谱检索上下文
    web_context: str        # 联网搜索上下文
    extra_context: str      # 对比/解释/政策附加上下文

    reply: str              # 最终回复
```

### 3.1 SLOT_KEYS（画像槽位）

```python
SLOT_KEYS = [
    "province", "subject", "score", "rank", "majors", "majors_reject",
    "region_pref", "region_avoid", "tags", "family_bg", "career_goal",
    "city_limit", "schools", "compare_targets",
]
```

| 槽位 | 类型 | 说明 |
|------|------|------|
| `province` | str | 省份 |
| `subject` | str | 选科（物理/历史） |
| `score` | int | 分数 |
| `rank` | int | 位次 |
| `majors` | list | 意向专业 |
| `majors_reject` | list | 排斥专业 |
| `region_pref` | list | 偏好地域 |
| `region_avoid` | list | 排斥地域 |
| `tags` | list | 标签要求（985/211/C9等） |
| `family_bg` | str | 家庭背景 |
| `career_goal` | str | 职业目标 |
| `city_limit` | str | 城市限制 |
| `schools` | list | 指定学校 |
| `compare_targets` | list | 对比目标学校 |

### 3.2 merge_profile 策略

- 标量字段（province/score/rank 等）：非空新值覆盖旧值
- 列表字段（majors/tags/schools 等）：去重追加（`dict.fromkeys` 保序）

---

## 4. 各节点职责

### analyze（入口节点）

- 调用 `GraphRAG/engine/extractor.py` 的 `extract_info()` 从消息抽取画像字段
- 正则匹配 `family_bg` 和 `career_goal`（`FAMILY_PATTERNS` / `CAREER_PATTERNS`）
- 合并到 `state.profile`
- 判断信息槽是否完整（province + score/rank），设置 `needs_ask` 和 `intent`

### ask

- 根据缺失槽位生成追问（如"你是哪个省的？""位次多少？"）
- 设置 `state.reply` 后结束本轮（等用户下一轮输入）

### recommend

- 调用 `GraphRAG/engine/recommend.py` 的 `recommend()`
- 流程：自定义 Excel → 向量扩展关键词 → SQL 冲稳保 → 图过滤 → 重排
- 结果写入 `state.recommend_result` 和 `state.data_context`

### compare

- 接收 `state.profile.compare_targets`（两所学校名）
- 查询两校录取数据，组装对比上下文到 `state.extra_context`

### explain

- 解释专业/概念（如"计算机科学与技术学什么"）
- 组装上下文到 `state.extra_context`

### policy

- 政策解读（如"浙江志愿填报规则"）
- 组装上下文到 `state.extra_context`

### graphrag

- 调用 `GraphRAG/engine/graphrag.py` 进行图谱 + 向量检索
- 结果写入 `state.graph_context`
- 所有 intent 路径最终都经过此节点

### web_search

- 调用 `GraphRAG/engine/web_search.py` 进行 Tavily 联网搜索
- 需要 `tavily_key` 配置，无 key 时跳过
- 结果写入 `state.web_context`

### generate

- 调用 `llm_config.get_api_config()` 获取 LLM 配置
- 拼装 system prompt（张雪峰风格）+ 各 context + 用户消息
- 调用 LLM API 生成回复
- 设置 `state.reply`

---

## 5. Checkpoint 机制

- 使用 `langgraph-checkpoint-sqlite` 持久化状态
- 存储位置：`data/user/checkpoints.db`
- `thread_id` = `{user_id}:{conversation_id}`（多用户多对话隔离）
- 启用 WAL + busy_timeout=5000
- 进程退出时通过 `atexit` 关闭连接

**作用**：多轮对话时，LangGraph 自动从 checkpoint 恢复 `state.profile` 和 `state.messages`，无需前端传 history。

---

## 6. database.py 表结构

详见 `GraphRAG/docs/数据字典.md` 的 `langgraph_data.db` 部分。

核心 API：
- `init_db()` — 建表 + WAL + 迁移
- `create_user()` / `get_user()` — 用户 CRUD
- `create_conversation()` / `get_conversation()` — 对话 CRUD
- `save_message()` / `get_conversation_messages()` — 消息 CRUD
- `save_profile()` / `load_profile()` — 画像 CRUD
- `delete_conversation()` — 级联删除（消息 + 画像）

---

## 7. user_manager.py 业务封装

| 函数 | 说明 |
|------|------|
| `init_user_system()` | 初始化数据库（幂等） |
| `get_or_create_user()` | 获取或创建用户 |
| `start_conversation()` | 创建新对话 |
| `get_conversation_id()` | 获取用户最新对话 ID |
| `save_user_message()` | 保存用户消息 |
| `save_assistant_message()` | 保存助手回复 |
| `get_history_messages()` | 获取历史消息 |
| `load_conversation_state()` | 加载对话状态（画像 + 消息） |
| `save_conversation_state()` | 保存对话状态（画像） |
| `list_conversations()` | 列出用户所有对话 |
| `get_conversation_detail()` | 获取对话详情（含消息 + 画像 + 摘要） |

---

## 8. llm_config.py 配置优先级

```
环境变量 (LLM_URL / LLM_KEY / LLM_MODEL)
    ↓
langgraph/api_config.json (前端设置页写入)
    ↓
GraphRAG/config.json
    ↓
内置默认值 (deepseek-v4-flash @ https://api.deepseek.com)
```

核心 API：
- `get_api_config()` → `{"url": "...", "model": "...", "key": "..."}`
- `update_api_config(provider, **kwargs)` → 运行时更新配置

---

## 9. 依赖安装

```bash
# 先装引擎依赖（GraphRAG/engine/* 需要）
pip install -r ../GraphRAG/requirements-engine.txt

# 再装 LangGraph 专用依赖
pip install -r requirements.txt
```

`requirements.txt` 包含：
- `langgraph>=1.0,<2.0`
- `langgraph-checkpoint-sqlite>=2.0,<3.0`
- `langchain-core>=0.3.0,<1.0`
