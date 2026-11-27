"""顺序迁移：按 schema_migrations 记录执行增量脚本。

v1 = 初始 12 表（03 文档）；
chunk_vec（vec0 虚表）在扩展加载成功时于 v1 一起创建，失败时标记待补
（后续迁移或重启时重试，避免阻断启动）。
v2 = note_file 增加列表展示派生字段（note_title / summary / tags / note_mode），
     由整表重扫重建，不新增真相源。
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

    if 2 not in applied:
        _migrate_v2_note_file_columns(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (2, 'note_file_display_fields', ?)",
            (_utcnow(),),
        )
        conn.commit()
        logger.info("迁移 v2 完成（note_file 增加 note_title / summary / tags / note_mode）")

    if 3 not in applied:
        conn.executescript(_CLEANUP_V3_SQL)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (3, 'cleanup_batch_item', ?)",
            (_utcnow(),),
        )
        conn.commit()
        logger.info("迁移 v3 完成（M7 存量整理：cleanup_batch / cleanup_item）")

    if 4 not in applied:
        conn.executescript(_TERMINAL_V4_SQL)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (4, 'terminal_tab', ?)",
            (_utcnow(),),
        )
        conn.commit()
        logger.info("迁移 v4 完成（M5 终端：terminal_tab）")

    if 5 not in applied:
        conn.executescript(_GIT_REPO_V5_SQL)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (5, 'git_repo', ?)",
            (_utcnow(),),
        )
        conn.commit()
        logger.info("迁移 v5 完成（M6 代码仓库：git_repo）")

    if 6 not in applied:
        _migrate_v6_git_repo_columns(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (6, 'git_repo_remote_commit', ?)",
            (_utcnow(),),
        )
        conn.commit()
        logger.info("迁移 v6 完成（git_repo 增加 remote_url / commit_count）")


# v3：M7 存量整理（03 文档 6 章三期表预留的落位）。
# - cleanup_item.before_content 保存执行前的完整原文，回滚按字节还原（只写头部 ≠ 只回滚头部：
#   存原文最稳，且几十篇量级不会撑爆库）；
# - backup_run 里另记一条 kind='cleanup_rollback' 的 zip，供工作台外手工恢复。
# 禁止级联删除（database-rules）：批次与条目由服务层显式事务清理。
_CLEANUP_V3_SQL = """
CREATE TABLE IF NOT EXISTS cleanup_batch (
    id             INTEGER PRIMARY KEY,
    status         TEXT NOT NULL CHECK (status IN ('draft','done','rolled_back','failed')),
    scope          TEXT NOT NULL DEFAULT 'missing' CHECK (scope IN ('missing','all')),
    total          INTEGER NOT NULL DEFAULT 0,
    applied        INTEGER NOT NULL DEFAULT 0,
    skipped        INTEGER NOT NULL DEFAULT 0,
    failed         INTEGER NOT NULL DEFAULT 0,
    backup_path    TEXT,
    note           TEXT,
    created_at     TEXT NOT NULL,
    executed_at    TEXT,
    rolled_back_at TEXT
);

CREATE TABLE IF NOT EXISTS cleanup_item (
    id             INTEGER PRIMARY KEY,
    batch_id       INTEGER NOT NULL REFERENCES cleanup_batch(id),
    path           TEXT NOT NULL,                -- note_file.path 同口径（notes_dir 相对路径）
    customer       TEXT,
    project_name   TEXT,
    status         TEXT,                         -- 目标状态：active | archived
    start_date     TEXT,                         -- YYYY-MM-DD
    missing        TEXT,                         -- JSON 数组：缺失的原始键
    sources        TEXT,                         -- JSON 对象：各字段识别来源
    changes        TEXT,                         -- JSON 数组：本次将写入的键
    skip           INTEGER NOT NULL DEFAULT 0,
    applied        INTEGER NOT NULL DEFAULT 0,
    rolled_back    INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    before_content TEXT,                         -- 执行前原文（回滚真相源）
    before_hash    TEXT,
    after_hash     TEXT,
    UNIQUE (batch_id, path)
);
CREATE INDEX IF NOT EXISTS idx_cleanup_item_batch ON cleanup_item (batch_id);
"""


# v4：M5 终端（03 文档 6 章三期表预留的落位）。
# - 标签元数据（tab_id / cwd / 标题 / 排序）持久化，PTY 进程随标签常驻但仅在内存维护，
#   重启后标签仍可恢复（重新附着时按 cwd 重启 shell，输出历史按 NFR-DATA-04 允许丢失）；
# - 关闭运行中标签由前端二次确认后调 DELETE，服务层终止 PTY 再删记录（禁止级联，显式清理）。
_TERMINAL_V4_SQL = """
CREATE TABLE IF NOT EXISTS terminal_tab (
    tab_id     TEXT PRIMARY KEY,
    cwd        TEXT NOT NULL,
    title      TEXT NOT NULL,
    sort       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_terminal_tab_sort ON terminal_tab (sort);
"""


# v5：M6 代码仓库（03 文档 7.3「代码仓库记录（派生数据）」落位）。
# - path 为仓库本地路径（真相锚点，唯一）；display 为置顶/普通/隐藏三态（FR-GIT-02）；
# - 状态摘要（分支/未提交数/领先落后/最近提交）是派生数据，每次扫描重建，不新增真相源；
# - 只读（FR-GIT-05）：本表只存展示状态与刷新时间，没有任何写操作语义。
_GIT_REPO_V5_SQL = """
CREATE TABLE IF NOT EXISTS git_repo (
    path          TEXT PRIMARY KEY,
    display       TEXT NOT NULL DEFAULT 'normal' CHECK (display IN ('pin','normal','hidden')),
    refreshed_at  TEXT,
    branch        TEXT,
    ahead         INTEGER,
    behind        INTEGER,
    dirty         INTEGER,
    last_commit   TEXT,
    last_commit_at TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_git_repo_display ON git_repo (display);
"""


# v2：note_file 列表展示派生字段（列已存在则跳过，兼容 v1 新库与旧库）
_NOTE_FILE_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("note_title", "TEXT"),
    ("summary", "TEXT"),
    ("tags", "TEXT"),
    ("note_mode", "TEXT"),
)


# v6：git_repo 增加远端地址与提交总数（列已存在则跳过，兼容 v5 旧库）
_GIT_REPO_V6_COLUMNS: tuple[tuple[str, str], ...] = (
    ("remote_url", "TEXT"),
    ("commit_count", "INTEGER"),
)


def _migrate_v6_git_repo_columns(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(git_repo)").fetchall()}
    for name, decl in _GIT_REPO_V6_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE git_repo ADD COLUMN {name} {decl}")
    # 旧行为 NULL：下次刷新（GET /api/repos 或后台线程）由 read_status 填充


def _migrate_v2_note_file_columns(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(note_file)").fetchall()}
    for name, decl in _NOTE_FILE_V2_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE note_file ADD COLUMN {name} {decl}")
    # 旧行为 NULL：下次 GET /api/notes（或启动重扫）时由 scan_file 填充


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
