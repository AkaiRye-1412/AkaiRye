"""顺序迁移：按 schema_migrations 记录执行增量脚本。

v1 = 初始 12 表（03 文档）；
chunk_vec（vec0 虚表）在扩展加载成功时于 v1 一起创建，失败时标记待补
（后续迁移或重启时重试，避免阻断启动）。
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.db import database

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"
VEC_DIM_DEFAULT = 1024  # 占位维度，实际以 embedding.dim 配置为准（R4）


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _try_create_vec_table(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(embedding float[{VEC_DIM_DEFAULT}])")
        return True
    except sqlite3.OperationalError as e:
        logger.warning("chunk_vec 建表失败（sqlite-vec 未就绪，重启自动重试）：%s", e)
        return False


def apply_migrations(conn: sqlite3.Connection) -> None:
    # 迁移记录表先于首个迁移自举创建
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    if 1 not in applied:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        vec_ok = _try_create_vec_table(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (1, 'init_12_tables', ?)",
            (_utcnow(),),
        )
        conn.commit()
        logger.info("迁移 v1 完成（12 表；chunk_vec 就绪=%s）", vec_ok)
    elif not _vec_table_exists(conn):
        # 已迁移但 vec 表缺失（上次扩展不可用）：静默重试
        if _try_create_vec_table(conn):
            conn.commit()


def _vec_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_vec'"
    ).fetchone()
    return row is not None


def init_db() -> None:
    conn = database.connect()
    try:
        apply_migrations(conn)
    finally:
        conn.close()
