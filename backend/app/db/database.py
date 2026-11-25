"""SQLite 连接管理（全 SQLite 方案 D3：SQLite + FTS5 + sqlite-vec + 进程内缓存）。

- WAL 模式 + foreign_keys 开启；
- sqlite-vec 扩展可选加载（V1 spike；失败不阻断启动，语义检索降级）；
- schema 由 migrations 模块按 schema_migrations 顺序执行。
"""

import logging
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.core import paths
from app.core.config import runtime

logger = logging.getLogger(__name__)


def _load_extensions(conn: sqlite3.Connection) -> None:
    # FTS5 随 CPython sqlite3 编译通常内置；校验可用性
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
    except sqlite3.OperationalError:
        logger.warning("FTS5 不可用：请更换带 FTS5 的 SQLite 构建（混合检索关键词侧依赖）")
    # sqlite-vec：可选（V1 spike 结论决定是否启用）
    if runtime.enable_vec_ext:
        try:
            import sqlite_vec  # type: ignore[import-not-found]

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            logger.info("sqlite-vec 扩展加载成功")
        except Exception as e:  # noqa: BLE001 - 任何加载失败均降级
            logger.warning("sqlite-vec 加载失败（语义检索暂不可用，R1）：%s", e)


def connect() -> sqlite3.Connection:
    paths.ensure_runtime_dirs()
    conn = sqlite3.connect(paths.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    _load_extensions(conn)
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """请求级连接（FastAPI 依赖注入使用）。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
