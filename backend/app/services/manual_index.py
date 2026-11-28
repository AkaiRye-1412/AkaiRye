"""PDF 入库服务（M3/M9 · FR-RAG-04~10 / Q-11）：队列 + 解析 + 切块 + 向量化。

状态机（8 态，Q-15）：
    discovered → queue → parsing → ready / failed
    ready + 文件变化 → update → rebuilding（旧块继续可检索，OQ-12）→ ready
    文件失效 → invalid（重新定位后回 ready，FR-RAG-09）

实现要点：
- **队列持久化在 ingest_task 表**：重启后未完成任务可恢复（NFR-DATA-04）；
- **单进程单工作线程**串行消费（个人单机场景，避免并发写 SQLite 与 embedding 限频叠加）；
- **解析与写库分离**：先在事务外解析完，再单事务「删旧块三件套 → 插新块」，
  这样重建失败时旧索引原样保留，不会出现「点到一半搜不到」；
- 向量化复用笔记索引的 `_embed_texts`（同一份 embedding 配置与限频退避），
  未配置时降级为 FTS-only，手册仍可关键词检索；
- 进度经 WS `ingest.progress`（节流）+ `manual.updated` 推送，前端也可轮询 /api/manuals。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.services import config_service, indexer, pdf_parse, textutil
from app.ws.events import broker

logger = logging.getLogger(__name__)

# 入库任务串行：同一时刻只跑一本，避免 embedding 并发触发 429
_task_lock = threading.Lock()
_running = False

_state_lock = threading.Lock()
_state: dict = {
    "running": False,
    "manual_id": None,
    "name": "",
    "stage": "",          # parsing / chunking / embedding / writing
    "progress": 0,
    "started_at": None,
    "finished_at": None,
    "error": "",
}

# 进度推送节流（api-rules：500ms）
_last_push = 0.0


def task_state() -> dict:
    with _state_lock:
        return dict(_state)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _publish(force: bool = False) -> None:
    """节流推送进度；force=True 用于阶段跃迁（不能等节流窗口）。"""
    global _last_push
    now = time.monotonic()
    if not force and now - _last_push < 0.5:
        return
    _last_push = now
    broker.publish("ingest.progress", task_state())


def _set_state(force: bool = False, **kw) -> None:
    """更新任务状态并推送。

    force=True 用于阶段跃迁 / 任务开始结束（必须实时）；
    纯进度更新（向量化批次回调）走 0.5s 节流——否则每个 batch 都推一次 WS，
    前端会被高频事件牵着反复刷新列表，看起来就是页面在闪。
    """
    with _state_lock:
        _state.update(kw)
    _publish(force=force)


def _set_manual(conn, manual_id: int, **fields) -> None:
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE manual SET {cols} WHERE id=?", (*fields.values(), manual_id))
    conn.commit()


def _file_hash(path: Path, *, limit_bytes: int = 512 * 1024 * 1024) -> str:
    """整文件 sha256（>512MB 只哈希首尾各 8MB，避免大手册卡住入库）。"""
    h = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as f:
        if size <= limit_bytes:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        else:
            h.update(f.read(8 * 1024 * 1024))
            f.seek(-8 * 1024 * 1024, 2)
            h.update(f.read())
            h.update(str(size).encode())
    return h.hexdigest()


# ---- 块三件套 ---------------------------------------------------------------

def _delete_chunks(conn, manual_id: int) -> int:
    """删除某手册全部块三件套（事务由调用方控制），返回删除块数。"""
    rows = conn.execute("SELECT id FROM chunk WHERE manual_id=?", (manual_id,)).fetchall()
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    conn.execute(f"UPDATE rag_citation SET chunk_id=NULL WHERE chunk_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM chunk_fts WHERE chunk_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM chunk_vec WHERE rowid IN ({marks})", ids)
    conn.execute(f"DELETE FROM chunk WHERE id IN ({marks})", ids)
    return len(ids)


def delete_manual(conn, manual_id: int) -> dict:
    """单事务删除手册：块三件套 + 队列记录 + manual 行（**不删磁盘 PDF**）。"""
    row = conn.execute("SELECT name FROM manual WHERE id=?", (manual_id,)).fetchone()
    if row is None:
        raise LookupError("手册不存在")
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN")
    removed = _delete_chunks(conn, manual_id)
    conn.execute("DELETE FROM ingest_task WHERE manual_id=?", (manual_id,))
    conn.execute("DELETE FROM manual WHERE id=?", (manual_id,))
    conn.commit()
    broker.publish("manual.updated", {"manual_id": manual_id, "status": "deleted",
                                      "chunks": 0, "name": row["name"]})
    return {"manual_id": manual_id, "deleted_chunks": removed}


# ---- 单本入库 ---------------------------------------------------------------

def index_manual(conn, manual_id: int) -> dict:
    """解析并重建一本手册的索引（同步）。返回 {status, chunks, reason}。

    状态流转由本函数负责；失败时抛异常，由调用方（工作线程/API）决定落库策略。
    """
    row = conn.execute("SELECT * FROM manual WHERE id=?", (manual_id,)).fetchone()
    if row is None:
        raise LookupError("手册不存在")
    path = Path(row["path"])
    if not path.is_file():
        _set_manual(conn, manual_id, status="invalid", error="PDF 文件不存在（可能已移动或删除）")
        raise FileNotFoundError("PDF 文件不存在（可能已移动或删除）")

    # 重建期间旧块保持 ready 可检索（OQ-12），解析完再一次性替换
    is_rebuild = row["status"] in ("ready", "update", "rebuilding")
    _set_manual(conn, manual_id, status="rebuilding" if is_rebuild else "parsing",
                progress=5, error=None)
    _set_state(force=True, running=True, manual_id=manual_id, name=row["name"], stage="parsing",
               progress=5, started_at=_now(), finished_at=None, error="")

    params = config_service.retrieval_params(conn)
    parsed = pdf_parse.parse_pdf(
        path, chunk_chars=params["pdf_chunk_size"], chunk_overlap=params["chunk_overlap"]
    )
    _set_manual(conn, manual_id, progress=20)
    _set_state(force=True, stage="chunking", progress=20)

    pieces = parsed.chunks
    if not pieces:
        raise ValueError("解析完成但没有可入库的文本块，请确认 PDF 内容是否为空")

    _set_manual(conn, manual_id, progress=25)
    _set_state(force=True, stage="embedding", progress=25)

    cfg = indexer._embedding_cfg(conn)
    model_id = indexer._model_id(cfg)
    vectors: list[list[float]] | None = None
    embed_error = ""

    def on_batch(done: int, total: int) -> None:
        pct = 25 + int(done / max(total, 1) * 65)
        _set_manual(conn, manual_id, progress=pct)
        _set_state(progress=pct)

    try:
        inputs = [
            f"{p['title_path'] or ''}\n{p['content'][: params['embed_max_chars']]}".strip()
            for p in pieces
        ]
        vectors = indexer._embed_texts(conn, inputs, on_batch=on_batch)
    except Exception as e:  # noqa: BLE001 —— 限频/接口异常降级 FTS-only，文本不丢
        logger.warning("手册向量化失败，降级 FTS-only（%s）：%s", row["name"], str(e)[:160])
        vectors = None
        embed_error = str(e)[:300]

    if vectors is None:
        model_id = indexer.FTS_ONLY_MODEL
        dim = 0
    else:
        dim = len(vectors[0]) if vectors else 0
        indexer._ensure_vec_dim(conn, dim)

    _set_state(force=True, stage="writing", progress=95)
    now = _now()
    # 大文件哈希放到事务外：写库事务期间不做磁盘 I/O，避免长事务锁库
    st = path.stat()
    file_hash = _file_hash(path)
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN")
    removed = _delete_chunks(conn, manual_id)
    for p in pieces:
        cur = conn.execute(
            "INSERT INTO chunk (source_type, note_path, manual_id, seq, title_path, page, content,"
            " content_hash, model_id, dim, status, indexed_at)"
            " VALUES ('manual', NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)",
            (manual_id, p["seq"], p["title_path"], p["page"], p["content"],
             textutil.content_hash_text(p["content"]), model_id, dim, now),
        )
        chunk_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunk_fts (tokens, chunk_id) VALUES (?, ?)",
            (textutil.bigram_tokens(f"{p['title_path'] or ''}\n{p['content']}"), chunk_id),
        )
        if vectors:
            conn.execute(
                "INSERT INTO chunk_vec (rowid, embedding) VALUES (?, ?)",
                (chunk_id, json.dumps(vectors[p["seq"]])),
            )

    conn.execute(
        "UPDATE manual SET status='ready', progress=100, pages=?, size=?, content_hash=?,"
        " file_mtime=?, model_id=?, dim=?, last_parsed_at=?, error=?, updated_at=? WHERE id=?",
        (parsed.pages, st.st_size, file_hash, int(st.st_mtime),
         model_id, dim, now, embed_error or None, now, manual_id),
    )
    conn.commit()

    reason = (
        f"已入库 {len(pieces)} 块（关键词模式，向量化降级：{embed_error[:80]}）"
        if vectors is None
        else f"已入库 {len(pieces)} 块"
    )
    broker.publish("manual.updated", {
        "manual_id": manual_id, "name": row["name"], "status": "ready",
        "chunks": len(pieces), "pages": parsed.pages, "replaced": removed,
    })
    logger.info("手册入库完成：%s（%d 块，替换旧块 %d，引擎 %s）",
                row["name"], len(pieces), removed, parsed.engine)
    return {
        "manual_id": manual_id,
        "status": "ready",
        "chunks": len(pieces),
        "pages": parsed.pages,
        "title_paths": len(parsed.toc),
        "scanned": parsed.scanned,
        "engine": parsed.engine,
        "reason": reason,
    }


# ---- 队列 ------------------------------------------------------------------

def enqueue(conn, manual_ids: list[int], action: str = "ingest") -> dict:
    """把手册排进入库队列（幂等：已在队列中的跳过）。返回 {queued, skipped}。"""
    if action not in ("ingest", "rebuild"):
        raise ValueError("action 仅支持 ingest / rebuild")
    queued: list[int] = []
    skipped: list[int] = []
    now = _now()
    for mid in manual_ids:
        row = conn.execute("SELECT id, status FROM manual WHERE id=?", (mid,)).fetchone()
        if row is None:
            skipped.append(mid)
            continue
        busy = conn.execute(
            "SELECT 1 FROM ingest_task WHERE manual_id=? AND status IN ('queued','running') LIMIT 1",
            (mid,),
        ).fetchone()
        if busy:
            skipped.append(mid)
            continue
        conn.execute(
            "INSERT INTO ingest_task (manual_id, action, status, progress, created_at)"
            " VALUES (?, ?, 'queued', 0, ?)",
            (mid, action, now),
        )
        # 排队即改状态，列表上能立刻看到「排队中」
        conn.execute(
            "UPDATE manual SET status=?, progress=0, error=NULL, updated_at=? WHERE id=?",
            ("queue" if action == "ingest" else "rebuilding", now, mid),
        )
        queued.append(mid)
    conn.commit()
    for mid in queued:
        broker.publish("manual.updated", {"manual_id": mid, "status": "queue"})
    return {"queued": queued, "skipped": skipped}


def _next_task(conn):
    return conn.execute(
        "SELECT * FROM ingest_task WHERE status='queued' ORDER BY created_at, id LIMIT 1"
    ).fetchone()


def _worker(conn_factory) -> None:
    global _running
    try:
        conn = conn_factory()
        try:
            while True:
                task = _next_task(conn)
                if task is None:
                    break
                mid = task["manual_id"]
                conn.execute(
                    "UPDATE ingest_task SET status='running', started_at=? WHERE id=?",
                    (_now(), task["id"]),
                )
                conn.commit()
                try:
                    index_manual(conn, mid)
                    conn.execute(
                        "UPDATE ingest_task SET status='done', progress=100, finished_at=? WHERE id=?",
                        (_now(), task["id"]),
                    )
                    conn.commit()
                except Exception as e:  # noqa: BLE001 —— 单本失败不中断队列
                    logger.exception("手册入库失败：%s", task["manual_id"])
                    msg = str(e)[:500]
                    conn.rollback()
                    conn.execute(
                        "UPDATE ingest_task SET status='failed', error=?, finished_at=? WHERE id=?",
                        (msg, _now(), task["id"]),
                    )
                    conn.execute(
                        "UPDATE manual SET status='failed', error=?, updated_at=? WHERE id=?",
                        (msg, _now(), mid),
                    )
                    conn.commit()
                    broker.publish("manual.updated", {
                        "manual_id": mid, "status": "failed", "error": msg,
                    })
                    _set_state(force=True, error=msg)
        finally:
            conn.close()
    finally:
        _set_state(force=True, running=False, stage="", finished_at=_now())
        broker.publish("ingest.progress", task_state())
        with _task_lock:
            _running = False


def start(conn_factory) -> dict:
    """启动后台工作线程（已有线程在跑则直接返回其状态）。立即返回。"""
    global _running
    with _task_lock:
        if _running:
            return {"started": False, "reason": "已有入库任务进行中", "state": task_state()}
        _running = True
    _set_state(force=True, running=True, stage="queued", progress=0, error="")
    threading.Thread(target=_worker, args=(conn_factory,), daemon=True, name="manual-ingest").start()
    return {"started": True, "state": task_state()}


def recover_on_startup(conn) -> int:
    """重启恢复（NFR-DATA-04）：running 视为中断 → 退回 queued 重跑。"""
    rows = conn.execute("SELECT id FROM ingest_task WHERE status='running'").fetchall()
    for r in rows:
        conn.execute(
            "UPDATE ingest_task SET status='queued', started_at=NULL, progress=0 WHERE id=?", (r["id"],)
        )
    conn.commit()
    return len(rows)


def relocate(conn, manual_id: int, new_path: str) -> dict:
    """重新定位失效手册（FR-RAG-09）：路径必须在任一扫描目录内，哈希可校验时要求一致。"""
    from app.services import pdf_scan

    roots = pdf_scan.scan_roots(conn)
    if not roots:
        pdf_scan.pdf_root(conn)  # 统一抛「未配置」错误
    p = Path(new_path).expanduser()
    if not p.is_absolute():
        p = roots[0] / new_path
    if not pdf_scan.in_scan_roots(conn, p):
        raise ValueError("新路径必须位于「PDF 手册索引位置」或「数据库索引位置」内")
    if not p.is_file():
        raise FileNotFoundError("新路径下没有找到 PDF 文件")

    row = conn.execute("SELECT content_hash FROM manual WHERE id=?", (manual_id,)).fetchone()
    if row is None:
        raise LookupError("手册不存在")
    expected = row["content_hash"]
    if expected and _file_hash(p) != expected:
        raise ValueError("文件内容与入库时不一致，请确认是否选错了文件")

    st = p.stat()
    conn.execute(
        "UPDATE manual SET path=?, name=?, size=?, file_mtime=?, status='ready', error=NULL,"
        " updated_at=? WHERE id=?",
        (p.resolve().as_posix(), p.stem, st.st_size, int(st.st_mtime), _now(), manual_id),
    )
    conn.commit()
    broker.publish("manual.updated", {"manual_id": manual_id, "status": "ready", "relocated": True})
    return {"manual_id": manual_id, "path": p.resolve().as_posix(), "status": "ready"}


def ignore_update(conn, manual_id: int) -> dict:
    """忽略「有更新」（FR-RAG-10）：同步 mtime，状态回 ready，不重建。"""
    row = conn.execute("SELECT path FROM manual WHERE id=?", (manual_id,)).fetchone()
    if row is None:
        raise LookupError("手册不存在")
    p = Path(row["path"])
    mtime = int(p.stat().st_mtime) if p.is_file() else None
    size = p.stat().st_size if p.is_file() else None
    conn.execute(
        "UPDATE manual SET status='ready', file_mtime=COALESCE(?, file_mtime),"
        " size=COALESCE(?, size), updated_at=? WHERE id=?",
        (mtime, size, _now(), manual_id),
    )
    conn.commit()
    broker.publish("manual.updated", {"manual_id": manual_id, "status": "ready", "ignored": True})
    return {"manual_id": manual_id, "status": "ready"}
