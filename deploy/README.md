# 部署运维规范

> 本文档描述 XF_agent 后端的部署流程、环境配置和运维操作。
> 修改部署配置前必须阅读此文档。

---

## 1. 部署架构

```
                    ┌─────────────┐
                    │   Nginx     │ :443 (HTTPS) / :80 (HTTP→HTTPS)
                    │   反向代理   │
                    └──────┬──────┘
                           │ proxy_pass
                           ▼
                    ┌─────────────┐
                    │  Gunicorn   │ :5000 (4 workers)
                    │  Flask App  │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         admission.db  user_data.db  langgraph_data.db
         (只读)        (读写)        (读写)
```

---

## 2. 环境要求

- **OS**: Ubuntu 20.04+ / Debian 11+
- **Python**: 3.9+
- **内存**: ≥ 2GB（向量模型加载约 1GB）
- **磁盘**: ≥ 5GB（数据库 + 向量索引 + 日志）
- **GPU**: 可选（RTX 3070 Ti 8GB，用于离线建库）

---

## 3. 一键部署

```bash
# 在服务器上克隆项目后执行
cd deploy
bash install.sh
```

脚本自动完成：
1. 安装 Python3 + pip + nginx + certbot
2. 复制应用文件到 `/opt/xuefeng-agent`
3. 解压 `admission_clean.db.gz`
4. 创建 systemd 服务（开机自启）
5. 配置 nginx 反向代理

---

## 4. 手动部署

### 4.1 安装依赖

```bash
cd /opt/xuefeng-agent
pip install -r GraphRAG/requirements-engine.txt
pip install -r langgraph/requirements.txt
```

### 4.2 配置

编辑 `GraphRAG/config.json`：

```json
{
  "llm_api_url": "https://api.deepseek.com",
  "llm_api_key": "sk-xxx",
  "llm_model": "deepseek-v4-flash",
  "tavily_key": "tvly-xxx（可选）",
  "wx_appid": "YOUR_WX_APPID",
  "wx_secret": "xxx"
}
```

### 4.3 解压数据库

```bash
cd /opt/xuefeng-agent/GraphRAG
python3 -c "
import gzip, shutil, os
if os.path.exists('admission_clean.db.gz') and not os.path.exists('admission_clean.db'):
    with gzip.open('admission_clean.db.gz', 'rb') as f_in, open('admission_clean.db', 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
    print('解压完成')
"
```

### 4.4 启动

```bash
# 开发环境
XF_ALLOW_DEV=1 python api_server.py --host 0.0.0.0 --port 5000

# 生产环境（Gunicorn）
cd /opt/xuefeng-agent/GraphRAG
gunicorn -w 4 -b 0.0.0.0:5000 --timeout 120 api_server:app
```

---

## 5. systemd 服务

文件：`/etc/systemd/system/xuefeng-agent.service`

```ini
[Unit]
Description=雪峰Agent Server
After=network.target

[Service]
Type=simple
User=xuefeng
Group=xuefeng
WorkingDirectory=/opt/xuefeng-agent/GraphRAG
ExecStart=/opt/xuefeng-agent/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 --timeout 120 api_server:app
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/xuefeng-agent/.env

[Install]
WantedBy=multi-user.target
```

**常用命令**：

```bash
systemctl start xuefeng-agent    # 启动
systemctl stop xuefeng-agent     # 停止
systemctl restart xuefeng-agent  # 重启
systemctl status xuefeng-agent   # 状态
journalctl -u xuefeng-agent -f   # 实时日志
```

---

## 6. Nginx 配置

文件：`deploy/nginx.conf`

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN.COM;

    location / {
        proxy_pass http://127.0.0.1:5000;  # ← 注意：不是 8765
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }
}
```

**HTTPS 配置**：

```bash
certbot --nginx -d YOUR_DOMAIN.COM
```

---

## 7. 环境变量

| 变量 | 用途 | 默认 | 必填 |
|------|------|------|------|
| `XF_ALLOW_DEV` | 允许无微信配置启动 | 未设置 | 本地开发设为 `1` |
| `XF_LOG_LEVEL` | 日志级别 | `INFO` | 否 |
| `XF_LOG_CONSOLE` | 控制台输出 | `1` | 生产设 `0` |
| `LLM_URL` | LLM API 地址 | 空 | 否 |
| `LLM_KEY` | LLM API Key | 空 | 否 |
| `LLM_MODEL` | LLM 模型名 | 空 | 否 |
| `DEEPSEEK_KEY` | DeepSeek Key（别名） | 空 | 否 |
| `TAVILY_KEY` | Tavily Key | 空 | 否 |

---

## 8. 日志

- **文件**：`logs/app.log`（项目根目录）
- **轮转**：10MB × 5 份
- **级别**：`XF_LOG_LEVEL` 控制（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- **格式**：`2026-06-26 12:47:40 [INFO] api_server | 消息内容`

查看日志：
```bash
tail -f /opt/xuefeng-agent/logs/app.log
journalctl -u xuefeng-agent -f
```

---

## 9. 数据库备份

### 9.1 手动备份

```bash
# 备份用户数据（最关键）
cp data/user/user_data.db data/user/user_data.db.bak
cp data/user/langgraph_data.db data/user/langgraph_data.db.bak
cp data/user/checkpoints.db data/user/checkpoints.db.bak
```

### 9.2 自动备份（crontab）

```bash
# 每天凌晨 3 点备份
0 3 * * * cd /opt/xuefeng-agent && tar czf /backup/xuefeng-user-$(date +\%Y\%m\%d).tar.gz data/user/
```

### 9.3 恢复

```bash
# 停止服务
systemctl stop xuefeng-agent

# 恢复备份
cp /backup/xuefeng-user-20260626/user_data.db data/user/
cp /backup/xuefeng-user-20260626/langgraph_data.db data/user/
cp /backup/xuefeng-user-20260626/checkpoints.db data/user/

# 启动服务
systemctl start xuefeng-agent
```

---

## 10. 健康检查

```bash
curl http://localhost:5000/api/health
# 期望: {"status": "ok", "service": "xf-graphrag"}
```

---

## 11. 常见问题

### 11.1 "database is locked"

SQLite WAL 模式 + busy_timeout 已缓解此问题。如仍出现：
- 检查是否有多个进程同时写入
- 升级路径：迁 PostgreSQL

### 11.2 "ModuleNotFoundError: log_setup"

确保从项目根目录启动，或 `PYTHONPATH` 包含项目根目录。

### 11.3 "langgraph 包名冲突"

`api_server.py` 已用 `importlib` 绕过。如果手动 import，确保先 `sys.path.insert` langgraph 目录。

### 11.4 向量模型加载慢

首次加载 `BAAI/bge-small-zh-v1.5` 需要下载模型（~100MB）。后续从缓存加载。

### 11.5 微信登录失败

检查 `config.json` 中的 `wx_appid` 和 `wx_secret` 是否正确。
本地开发可设 `XF_ALLOW_DEV=1` 跳过。

---

## 12. 生产环境检查清单

- [ ] `config.json` 填写 `wx_appid` + `wx_secret`
- [ ] `config.json` 填写 `llm_api_key`
- [ ] 未设置 `XF_ALLOW_DEV`（或设为 `0`）
- [ ] `XF_LOG_CONSOLE=0`（减少 systemd 日志量）
- [ ] Nginx 配置 HTTPS
- [ ] 数据库已解压（`admission_clean.db` 存在）
- [ ] crontab 配置数据库备份
- [ ] 健康检查通过
