"""后端统一日志配置 — GraphRAG + langgraph 共用

用法：在 api_server.py 启动时 import 本模块即可自动配置。
各业务模块只需 `import logging; log = logging.getLogger(__name__)`。

日志文件：项目根 logs/app.log（10MB 轮转，保留 5 份）
"""
import logging
import logging.handlers
import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(_HERE, 'logs')

# ponytail: 加锁防多线程/多 worker 并发重复添加 handler。
# 升级路径：迁 gunicorn 后此模块由 master 进程 import 一次，worker fork 后继承，锁可移除。
_configured = False
_lock = threading.Lock()


def setup():
    global _configured
    with _lock:
        if _configured:
            return
        _configured = True

    os.makedirs(LOG_DIR, exist_ok=True)

    level = getattr(logging, os.environ.get('XF_LOG_LEVEL', 'INFO').upper(), logging.INFO)
    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    root = logging.getLogger()
    root.setLevel(level)

    # 文件轮转：10MB × 5 份
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, 'app.log'),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    fh.setFormatter(fmt)
    fh.setLevel(level)
    root.addHandler(fh)

    # 控制台输出（生产环境设 XF_LOG_CONSOLE=0 关闭）
    if os.environ.get('XF_LOG_CONSOLE', '1') != '0':
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        ch.setLevel(level)
        root.addHandler(ch)


# import 即配置，api_server.py 只需 `import log_setup`
setup()
