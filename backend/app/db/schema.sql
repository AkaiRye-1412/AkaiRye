-- AkaiRye 售后工作台 · 数据库 Schema v1（初始版本）
-- 依据：docs/design/03.数据库设计.md v1.1（12 表）+ docs/rules/database-rules.md v1.0
-- 规则要点：vault 唯一真相源；三件套同事务；禁用级联删除（服务层显式事务）。

-- ============================================================
-- 3.1 配置分组
-- ============================================================
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL                       -- ISO 8601 UTC
);

CREATE TABLE IF NOT EXISTS brand (
    name    TEXT PRIMARY KEY,
    sort    INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1
);

-- ============================================================
-- 3.2 vault 派生分组（整表可由 vault 重扫重建）
-- ============================================================
CREATE TABLE IF NOT EXISTS note_file (
    path           TEXT PRIMARY KEY,               -- vault 内相对路径（唯一真相锚点）
    customer       TEXT,
    project_name   TEXT,
    status         TEXT CHECK (status IN ('active','archived')),
    start_date     TEXT,                           -- YYYY-MM-DD
    updated_date   TEXT,
    file_mtime     INTEGER,
    content_hash   TEXT,
    head_ok        INTEGER NOT NULL DEFAULT 0,
    indexed_status TEXT CHECK (indexed_status IN ('ready','pending','failed','excluded')),
    last_error     TEXT,
    scanned_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_note_file_status    ON note_file (status);
CREATE INDEX IF NOT EXISTS idx_note_file_customer  ON note_file (customer);
CREATE INDEX IF NOT EXISTS idx_note_file_idxstatus ON note_file (indexed_status);

-- ============================================================
-- 3.3 手册与队列分组
-- ============================================================
CREATE TABLE IF NOT EXISTS manual (
    id             INTEGER PRIMARY KEY,
    path           TEXT UNIQUE NOT NULL,           -- 引用原路径不复制（FR-RAG-09）
    name           TEXT NOT NULL,
    brand          TEXT,                           -- 自动识别（FR-RAG-12），空=未知
    pages          INTEGER,
    size           INTEGER,
    status         TEXT NOT NULL CHECK (status IN (
                       'discovered','queue','parsing','rebuilding',
                       'ready','failed','update','invalid'
                   )),                            -- 8 态（Q-15）
    progress       INTEGER,
    file_mtime     INTEGER,
    content_hash   TEXT,
    model_id       TEXT,                           -- R4：向量模型标识
    dim            INTEGER,
    last_parsed_at TEXT,
    error          TEXT,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_manual_status ON manual (status);
CREATE INDEX IF NOT EXISTS idx_manual_brand  ON manual (brand);

CREATE TABLE IF NOT EXISTS ingest_task (
    id          INTEGER PRIMARY KEY,
    manual_id   INTEGER NOT NULL REFERENCES manual (id),
    action      TEXT NOT NULL CHECK (action IN ('ingest','rebuild','relocate','remove')),
    status      TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','canceled')),
    progress    INTEGER,
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_task_status ON ingest_task (status, created_at);
CREATE INDEX IF NOT EXISTS idx_ingest_task_manual ON ingest_task (manual_id);

-- ============================================================
-- 3.4 切块与检索分组（三件套必须同事务：chunk / chunk_fts / chunk_vec）
-- ============================================================
CREATE TABLE IF NOT EXISTS chunk (
    id           INTEGER PRIMARY KEY,
    source_type  TEXT NOT NULL CHECK (source_type IN ('note','manual')),
    note_path    TEXT REFERENCES note_file (path),
    manual_id    INTEGER REFERENCES manual (id),
    seq          INTEGER NOT NULL,
    title_path   TEXT,
    page         INTEGER,                          -- 手册页码（笔记为空）
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model_id     TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','rebuilding')),
    indexed_at   TEXT NOT NULL,
    -- 来源一致性约束（03 文档 3.4）
    CHECK (
        (source_type = 'note'   AND note_path IS NOT NULL AND manual_id IS NULL AND page IS NULL)
     OR (source_type = 'manual' AND manual_id  IS NOT NULL AND note_path IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_chunk_note    ON chunk (note_path);
CREATE INDEX IF NOT EXISTS idx_chunk_manual  ON chunk (manual_id);
CREATE INDEX IF NOT EXISTS idx_chunk_status  ON chunk (status);
CREATE INDEX IF NOT EXISTS idx_chunk_srctype ON chunk (source_type);

-- FTS5（非 external content：tokens 为应用层 bigram 分词，规则第 10 节；V2 spike 后可整表重建）
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5 (
    tokens,
    chunk_id UNINDEXED
);

-- sqlite-vec（维度 1024 为占位，以实际 embedding.dim 为准；rowid = chunk.id）
-- 注意：vec0 建表依赖 sqlite-vec 扩展加载成功；未加载时跳过（V1 spike / R1）。
-- 由 migrations 在扩展可用时创建，避免纯 DDL 文件触发失败。

-- ============================================================
-- 3.5 RAG 会话分组（OQ-08：永久保留至手动删除）
-- ============================================================
CREATE TABLE IF NOT EXISTS rag_session (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_message (
    id           INTEGER PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES rag_session (id),
    role         TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content      TEXT NOT NULL,
    scope_json   TEXT,
    context_json TEXT,
    model_id     TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rag_message_session ON rag_message (session_id, created_at);

CREATE TABLE IF NOT EXISTS rag_citation (
    id          INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES rag_message (id),
    seq         INTEGER NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('note','manual')),
    source_name TEXT NOT NULL,
    chapter     TEXT,
    page        INTEGER,
    snippet     TEXT NOT NULL,
    note_path   TEXT,
    manual_id   INTEGER,
    chunk_id    INTEGER REFERENCES chunk (id) ON DELETE SET NULL  -- 溯源用；块被重建时自动脱钩
);
CREATE INDEX IF NOT EXISTS idx_rag_citation_message ON rag_citation (message_id, seq);
CREATE INDEX IF NOT EXISTS idx_rag_citation_chunk   ON rag_citation (chunk_id);

-- ============================================================
-- 3.6 备份分组
-- ============================================================
CREATE TABLE IF NOT EXISTS backup_run (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('daily','cleanup_rollback')),
    file_path  TEXT NOT NULL,
    size       INTEGER,
    status     TEXT NOT NULL CHECK (status IN ('ok','failed')),
    created_at TEXT NOT NULL
);

-- ============================================================
-- 3.7 迁移
-- ============================================================
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
