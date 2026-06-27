FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存
COPY GraphRAG/requirements-engine.txt /tmp/requirements-engine.txt
COPY langgraph/requirements.txt /tmp/requirements-langgraph.txt

# 安装依赖（torch 用 CPU 版本减小镜像体积）
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r /tmp/requirements-engine.txt && \
    pip install --no-cache-dir -r /tmp/requirements-langgraph.txt && \
    pip install --no-cache-dir gunicorn

# 复制项目文件
COPY log_setup.py /app/log_setup.py
COPY GraphRAG /app/GraphRAG
COPY langgraph /app/langgraph

# 确保必要目录存在
RUN mkdir -p /app/data/user /app/logs

# 知识库解压（admission_clean.db 是只读的，预置到镜像）
RUN cd /app/GraphRAG && \
    gunzip -k admission_clean.db.gz 2>/dev/null || true

# ponytail: 预烘焙 bge-small-zh 嵌入模型，避免运行时从 HF 下载（国内云环境 HF 不稳定）。
# 用 hf-mirror 国内镜像；HF_HOME 固定路径，运行时 preload 线程从本地缓存加载（~5s，零网络）。
ENV HF_ENDPOINT=https://hf-mirror.com
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"

WORKDIR /app/GraphRAG

# 微信云托管注入 PORT 环境变量
ENV PORT=5000
EXPOSE 5000

# gunicorn 单 worker（SQLite 不支持多进程写）
# ponytail: callContainer 最大超时 60s，同步对齐。
CMD ["sh", "-c", "gunicorn -w 1 -b 0.0.0.0:${PORT:-5000} --timeout 60 api_server:app"]
