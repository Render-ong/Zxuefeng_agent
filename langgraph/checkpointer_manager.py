"""Checkpointer 生命周期管理器

统一封装 SqliteSaver 的创建、配置与资源清理，取代 agent.py 中
手动 __enter__() + atexit 的临时方案。

升级路径：换 PostgresSaver 后由连接池管理生命周期，本类可简化或移除。
"""
import logging
from typing import Optional

from langgraph.checkpoint.sqlite import SqliteSaver

logger = logging.getLogger(__name__)


class CheckpointerManager:
    """SQLite Checkpointer 生命周期管理器。

    职责：
    - 创建 SqliteSaver 实例（封装 from_conn_string + __enter__）
    - 配置 SQLite 连接参数（WAL + busy_timeout）
    - 提供显式 close() 与 atexit 兜底双重清理

    用法：
        mgr = CheckpointerManager(db_path)
        checkpointer = mgr.start()
        ...使用 checkpointer...
        mgr.close()  # 或注册 atexit.register(mgr.close)
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ctx: Optional[object] = None
        self._checkpointer: Optional[SqliteSaver] = None
        self._closed = False

    def start(self) -> SqliteSaver:
        """初始化 checkpointer 并配置 SQLite 连接参数。

        幂等：重复调用返回同一实例。
        """
        if self._checkpointer is not None:
            return self._checkpointer

        # SqliteSaver.from_conn_string 返回 context manager，
        # 需要进入上下文取出实际的 saver 实例。
        self._ctx = SqliteSaver.from_conn_string(self.db_path)
        try:
            self._checkpointer = self._ctx.__enter__()
        except Exception:
            # __enter__ 失败也要尝试清理 ctx，避免句柄泄漏
            self._cleanup_ctx()
            raise

        self._configure_sqlite(self._checkpointer)
        logger.info("SQLite checkpointer started: %s", self.db_path)
        return self._checkpointer

    def _configure_sqlite(self, checkpointer: SqliteSaver) -> None:
        """配置 SQLite 连接参数，缓解多并发下 "database is locked"。

        属性名视 langgraph 版本可能为 conn/_conn，取不到则跳过
        （不影响主流程，仅退化到默认锁行为）。
        """
        for attr in ("conn", "_conn"):
            conn = getattr(checkpointer, attr, None)
            if conn is not None:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=5000")
                except Exception as e:
                    logger.warning("Failed to configure SQLite PRAGMA: %s", e)
                break

    def get(self) -> SqliteSaver:
        """获取当前 checkpointer 实例，未初始化则抛错。"""
        if self._checkpointer is None:
            raise RuntimeError("Checkpointer not started, call start() first")
        return self._checkpointer

    def _cleanup_ctx(self) -> None:
        """清理 context manager 持有的数据库连接。"""
        if self._ctx is not None:
            try:
                self._ctx.__exit__(None, None, None)
            except Exception as e:
                logger.error("Error during checkpointer cleanup: %s", e)
            finally:
                self._ctx = None
                self._checkpointer = None

    def close(self) -> None:
        """显式关闭 checkpointer，释放数据库连接，避免 WAL 文件残留。

        幂等：重复调用安全。
        """
        if self._closed:
            return
        self._cleanup_ctx()
        self._closed = True
        logger.info("Checkpointer closed")
