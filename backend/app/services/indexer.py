"""笔记索引服务（M3 · FR-RAG-01~03）：切块 → bigram → FTS + 向量三件套同事务。

依据：
- 03 文档场景 6：保存 8s 后增量 = 重扫 → hash 变化 → 事务删旧块三件套 → 插新块 → 更新 note_file；
- 03 文档场景 2：移出白名单 → 事务删块三件套 + indexed_status='excluded'；纳入 → 增量索引；
- R4：chunk.model_id/dim 记录生成模型，配置切换后不一致即重索引（维度漂移防护）；
- 降级：embedding 未配置时仅建 FTS（model_id 记为 fts-only），语义检索待配置后重建。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.services import config_service, embedding, textutil, vault
from app.ws.events import broker

logger = logging.getLogger(__name__)

FTS_ONLY_MODEL = "(fts-only)"
# 嵌入输入截断长度：省 embedding 配额（长笔记块数多时触发 429）
EMBED_MAX_CHARS = 700

# 后台任务串行化（个人单机场景：同时只跑一个索引任务）
_task_lock = threading.Lock()
_running = False

# 任务进度（进程内状态，供 /api/kb/stats 展示；不需要持久化，重启即归零）
_progress_lock = threading.Lock()
_progress: dict = {
    "running": False,
    "mode": "",          # autofill（补齐缺失）/ full（全量重建）
    "total": 0,
    "done": 0,
    "failed": 0,
    "current": "",
    "started_at": None,
    "finished_at": None,
}


def task_progress() -> dict:
    """当前/上次索引任务进度快照（M3 进度条数据源）。"""
    with _progress_lock:
        return dict(_progress)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _progress_start(mode: str, total: int) -> None:
    with _progress_lock:
        _progress.update(
            running=True, mode=mode, total=total, done=0, failed=0,
            current="", started_at=_now_iso(), finished_at=None,
        )


def _progress_step(path: str, ok: bool) -> None:
    with _progress_lock:
        _progress["done"] += 1
        if not ok:
            _progress["failed"] += 1
        _progress["current"] = path


def _progress_end() -> None:
    with _progress_lock:
        _progress.update(running=False, current="", finished_at=_now_iso())


# ---- 配置与白名单 ----------------------------------------------------------

def _embedding_cfg(conn) -> dict:
    return config_service.get_raw(conn, "embedding")


def _model_id(cfg: dict) -> str:
    return cfg.get("model") or FTS_ONLY_MODEL


def _in_whitelist(conn, rel: str) -> bool:
    """范围判定：
    - 售后主位置根下的直接子文件恒在（子目录仍需显式白名单，避免 vault 杂乱目录全被索引）；
    - 售后第 2/3 个位置、随手笔记各位置、知识库资料目录（「数据库索引位置」）：整根递归都在范围内；
    - 其余按归一化白名单目录前缀匹配（子文件夹名 / vault 相对路径 / 绝对路径均可）。
    - PDF 手册目录不在知识库范围内（仅用于手册界面浏览/预览）。
    """
    p = Path(rel)
    if p.is_absolute():
        abs_p = p.resolve()
    else:
        base = vault.notes_dir(conn)
        abs_p = (base / rel).resolve()
        if abs_p.parent == base:
            return True

    for root in [
        *vault.after_sales_roots(conn)[1:],
        *vault.memo_roots(conn),
        *vault.db_roots(conn),
    ]:
        try:
            if abs_p.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    for _key, abs_dir in vault.whitelist_abs_dirs(conn):
        if abs_p == abs_dir or abs_p.is_relative_to(abs_dir):
            return True
    return False


def _norm_path(rel: str) -> str:
    """路径比较口径：统一 posix（note_file.path 对外即 posix，绝对路径同为 posix）。"""
    return Path(rel).as_posix()


def excluded_paths(conn) -> set[str]:
    """单篇移出知识库的名单（config `index.whitelist.excluded`，2026-11-26）。

    与「文件夹级白名单」互补：目录仍纳入索引，但其中个别笔记被显式移出，
    移出后在列表里保持可见（状态 excluded），可一键恢复，不重新参与切块/检索。
    """
    raw = config_service.get_raw(conn, "index.whitelist").get("excluded") or []
    return {_norm_path(x) for x in raw if isinstance(x, str) and x.strip()}


def is_excluded(conn, rel: str) -> bool:
    return _norm_path(rel) in excluded_paths(conn)


def _embed_texts(conn, texts: list[str], on_batch=None) -> list[list[float]] | None:
    """调用 embedding 接口；未配置返回 None（FTS-only 降级），失败抛异常。

    `on_batch(done, total)` 可选，供手册入库按批推进进度（笔记侧不传，行为不变）。
    提供方差异（Ollama 的 /v1 前缀、免密钥、免节流）由 services.embedding 统一处理。
    """
    cfg = _embedding_cfg(conn)
    base_url = embedding.base_url(cfg)
    if not base_url or not cfg.get("model"):
        return None
    from openai import OpenAI

    local = embedding.is_local(base_url)
    client = OpenAI(base_url=base_url, api_key=embedding.api_key(cfg), timeout=120)
    # 分批调用：云端（火山）单次 input 上限 10 条；本机 Ollama 可以放大，免得 1400 块跑成上百批
    batch = 32 if local else 10
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        part = texts[i : i + batch]
        resp = None
        # 本机没有 429 限频，失败即真失败，不重试；云端保留指数退避
        attempts = 1 if local else 5
        for attempt in range(attempts):
            try:
                resp = client.embeddings.create(model=cfg["model"], input=part)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == attempts - 1:
                    raise
                wait = 3.0 * (attempt + 1)
                logger.warning(
                    "embedding 批次限频/失败，%.0fs 后重试（%s/%s）：%s",
                    wait, attempt + 1, attempts - 1, str(e)[:120],
                )
                time.sleep(wait)
        out.extend(d.embedding for d in resp.data)
        if on_batch is not None:
            on_batch(min(i + batch, len(texts)), len(texts))
        if not local and i + batch < len(texts):
            time.sleep(3.0)  # 云端批次间节流，规避 429 限频
    return out


def _vec_declared_dim(conn) -> int | None:
    """读取 chunk_vec 虚表声明的向量维度；表不存在返回 None。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunk_vec'"
    ).fetchone()
    if row is None:
        return None
    import re as _re

    m = _re.search(r"float\[(\d+)\]", row["sql"] or "")
    return int(m.group(1)) if m else None


def _ensure_vec_dim(conn, dim: int) -> bool:
    """向量维度对齐：chunk_vec 声明维度与实际不符时重建（自愈，2026-11-26）。

    背景：建表用 VEC_DIM_DEFAULT=1024 占位，而真实模型（如 doubao-embedding-vision）
    是 2048 维，插入直接报错。重建会清空全部旧向量——维度变了旧向量本就不可比，
    由随后的全量重索引补齐；KNN 查询在表空时自动走 FTS-only，不影响可用性。
    返回是否发生了重建。
    """
    declared = _vec_declared_dim(conn)
    if declared is None or declared == dim:
        return False
    conn.execute("DROP TABLE chunk_vec")
    conn.execute(
        f"CREATE VIRTUAL TABLE chunk_vec USING vec0(embedding float[{dim}])"
    )
    # 旧向量已随表清空，使旧块的 dim 标记失效（避免混维误判）
    conn.execute("UPDATE chunk SET dim=0 WHERE dim != 0 AND dim != ?", (dim,))
    logger.info("chunk_vec 维度 %s → %s，已重建（旧向量清空，待重索引）", declared, dim)
    return True


# ---- 三件套事务操作（03 文档 3.4） ------------------------------------------

def _delete_chunks(conn, note_path: str) -> int:
    """事务内删除某笔记全部块三件套，返回删除块数。"""
    rows = conn.execute("SELECT id FROM chunk WHERE note_path=?", (note_path,)).fetchall()
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    # 溯源引用先脱钩（rag_citation 自带 snippet/source_name，展示不依赖 chunk 行）
    conn.execute(f"UPDATE rag_citation SET chunk_id=NULL WHERE chunk_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM chunk_fts WHERE chunk_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM chunk_vec WHERE rowid IN ({marks})", ids)
    conn.execute(f"DELETE FROM chunk WHERE id IN ({marks})", ids)
    return len(ids)


def _set_status(conn, rel: str, status: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE note_file SET indexed_status=?, last_error=? WHERE path=?",
        (status, error, rel),
    )


# ---- 单篇索引 ---------------------------------------------------------------

def index_note(conn, rel: str, *, force: bool = False) -> dict:
    """增量索引单篇笔记（调用方负责 commit）。返回 {status, chunks, reason}。

    - hash 未变且模型一致 → skipped（force 跳过该检查）；
    - 三件套同事务：删旧 → 插新（chunk + chunk_fts + chunk_vec）；
    - embedding 未配置/调用失败 → FTS-only 降级（无 vec 行，内容仍可关键词检索；
      模型标记为 FTS-only，下次重索引会自动重试补向量，不再整篇失败丢失）。
    """
    row = conn.execute("SELECT * FROM note_file WHERE path=?", (rel,)).fetchone()
    if row is None:
        return {"status": "missing", "chunks": 0, "reason": "note_file 无此行"}

    if is_excluded(conn, rel):
        deleted = _delete_chunks(conn, rel)
        _set_status(conn, rel, "excluded")
        return {"status": "excluded", "chunks": 0, "reason": f"已移出知识库（删 {deleted} 块）"}

    if not _in_whitelist(conn, rel):
        deleted = _delete_chunks(conn, rel)
        _set_status(conn, rel, "excluded")
        return {"status": "excluded", "chunks": 0, "reason": f"移出白名单（删 {deleted} 块）"}

    abs_path = vault.resolve_note_abs(conn, rel)
    if not abs_path.is_file():
        _set_status(conn, rel, "failed", "文件不存在（可能已被移动或删除）")
        return {"status": "missing", "chunks": 0, "reason": "文件不存在"}
    text = abs_path.read_bytes().decode("utf-8", errors="replace")
    params = config_service.retrieval_params(conn)
    pieces = textutil.chunk_note_markdown(
        text, max_chars=params["chunk_size"], overlap=params["chunk_overlap"]
    )
    embed_max = params["embed_max_chars"]
    cfg = _embedding_cfg(conn)
    model_id = _model_id(cfg)

    if not force:
        old = conn.execute(
            "SELECT seq, content_hash, model_id FROM chunk WHERE note_path=? ORDER BY seq", (rel,)
        ).fetchall()
        if (
            [r["content_hash"] for r in old] == [textutil.content_hash_text(p["content"]) for p in pieces]
            and all(r["model_id"] == model_id for r in old)
        ):
            _set_status(conn, rel, "ready")
            return {"status": "skipped", "chunks": len(old), "reason": "内容与模型未变化"}

    # 嵌入输入：标题 + 内容前段（截断长度可配；默认 700 是为了省云端配额，本机可放大到分块上限）
    embed_inputs = [f"{p['title_path'] or ''}\n{p['content'][:embed_max]}".strip() for p in pieces]
    embed_error = ""
    dim = 0
    try:
        vectors = _embed_texts(conn, embed_inputs) if pieces else []
    except Exception as e:  # noqa: BLE001 —— 限频/接口异常时降级 FTS-only，内容不丢
        logger.warning("embedding 失败，降级 FTS-only（%s）：%s", rel, str(e)[:160])
        vectors = None
        embed_error = str(e)[:300]
    if vectors is None:
        # 降级：块照常入库（关键词可检索），模型标记 FTS-only 以便下次重索引补向量
        model_id = FTS_ONLY_MODEL

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # 维度以实际返回为准（配置可不填 dim；模型换档时自愈重建向量表）
    if vectors:
        dim = len(vectors[0])
        _ensure_vec_dim(conn, dim)
    if conn.in_transaction:
        conn.commit()  # 调用链可能遗留隐式事务（rescan 等），三件套须独立事务
    conn.execute("BEGIN")
    _delete_chunks(conn, rel)
    for p in pieces:
        cur = conn.execute(
            "INSERT INTO chunk (source_type, note_path, manual_id, seq, title_path, page, content,"
            " content_hash, model_id, dim, status, indexed_at)"
            " VALUES ('note', ?, NULL, ?, ?, NULL, ?, ?, ?, ?, 'ready', ?)",
            (rel, p["seq"], p["title_path"], p["content"],
             textutil.content_hash_text(p["content"]), model_id, dim, now),
        )
        chunk_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunk_fts (tokens, chunk_id) VALUES (?, ?)",
            # 标题一并入索引：标题命中词频更高，bm25 排序更准（"集合set"标题优先于正文零散出现）
            (textutil.bigram_tokens(f"{p['title_path'] or ''}\n{p['content']}"), chunk_id),
        )
        if vectors:
            # sqlite-vec 接受 JSON 字符串绑定（V1 spike 口径；裸 list 不受支持）
            conn.execute(
                "INSERT INTO chunk_vec (rowid, embedding) VALUES (?, ?)",
                (chunk_id, json.dumps(vectors[p["seq"]])),
            )
    _set_status(conn, rel, "ready", embed_error or None)
    conn.commit()
    return {
        "status": "ready",
        "chunks": len(pieces),
        "reason": "已索引" if vectors else f"已索引（关键词模式，embedding 降级：{embed_error[:80]}）",
    }


def delete_note_index(conn, rel: str) -> int:
    """笔记删除时清理块三件套（watchdog deleted 分支）。"""
    deleted = _delete_chunks(conn, rel)
    conn.commit()
    if deleted:
        broker.publish("index.updated", {"note_path": rel, "status": "deleted", "chunks": 0})
    return deleted


def reindex_all_notes(conn, *, force: bool = True) -> dict:
    """全量重建（POST /api/kb/reindex）。同步执行——个人规模（数百篇）秒级完成。

    注意：embedding 已配置时每批 10 条 + 3s 节流，1425 块约需数分钟，
    前端请用 background 模式（大库同步调用会 HTTP 超时）。
    """
    vault.rescan_all(conn)
    rows = conn.execute("SELECT path FROM note_file").fetchall()
    _progress_start("full", len(rows))
    done = failed = excluded = 0
    try:
        for r in rows:
            rel = r["path"]
            try:
                result = index_note(conn, rel, force=force)
            except Exception:  # noqa: BLE001 —— 单篇异常不中断整体任务
                logger.exception("全量重建失败：%s", rel)
                result = {"status": "failed", "chunks": 0}
            if result["status"] in ("ready", "skipped"):
                done += 1
            elif result["status"] == "excluded":
                excluded += 1
            else:
                failed += 1
            _progress_step(rel, result["status"] in ("ready", "skipped", "excluded"))
            broker.publish("index.updated", {"note_path": rel, "status": result["status"],
                                             "chunks": result["chunks"]})
        conn.commit()
    finally:
        _progress_end()
    return {"done": done, "failed": failed, "excluded": excluded, "total": len(rows)}


def autofill_targets(conn) -> list[str]:
    """待补齐索引的笔记（FR-RAG-01「自动索引，无需手动触发」）。

    判定：在白名单内，且 状态非 ready 或虽然 ready 但一块都没有（历史遗留/向量表重建后）。
    注意 excluded 也要纳入：它表示"上次被移出白名单"，重新纳入后必须补索引
    （白名单外的笔记已由 _in_whitelist 排除，不会误扫）。
    """
    out: list[str] = []
    excluded = excluded_paths(conn)
    for r in conn.execute("SELECT path, indexed_status FROM note_file").fetchall():
        rel = r["path"]
        if _norm_path(rel) in excluded:  # 单篇移出：即使目录在白名单内也不补
            continue
        if not _in_whitelist(conn, rel):
            continue
        if r["indexed_status"] == "ready":
            has = conn.execute("SELECT 1 FROM chunk WHERE note_path=? LIMIT 1", (rel,)).fetchone()
            if has:
                continue
        out.append(rel)
    return out


def _run_task(conn_factory, mode: str, paths: list[str], *, force: bool) -> None:
    """后台索引任务主体：逐篇索引并推进进度（进程内单任务串行）。"""
    global _running
    conn = None
    try:
        conn = conn_factory()
        for rel in paths:
            try:
                result = index_note(conn, rel, force=force)
                ok = result["status"] in ("ready", "skipped")
                detail = {"note_path": rel, "status": result["status"], "chunks": result["chunks"]}
            except Exception:  # noqa: BLE001 —— 单篇失败记录后继续
                logger.exception("索引失败：%s", rel)
                ok, detail = False, {"note_path": rel, "status": "failed", "chunks": 0}
            _progress_step(rel, ok)
            broker.publish("index.updated", {**detail, "progress": task_progress()})
        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()
        _progress_end()
        broker.publish("index.updated", {"status": "task_done", "mode": mode,
                                         "progress": task_progress()})
        with _task_lock:
            _running = False


def start_task(conn_factory, *, mode: str, paths: list[str] | None = None,
               force: bool = True, delay: float = 0.0) -> dict:
    """启动后台索引任务（M3 队列语义）：立即返回，进度经 task_progress()/WS 获取。

    - mode="autofill"：只在 paths 为空时按 autofill_targets 计算；
    - mode="full"：paths 传 None → 后台线程内自行 rescan + 算全量。
    已有任务在跑时返回 {started: False}（前端提示"已有任务进行中"）。
    """
    global _running
    with _task_lock:
        if _running:
            return {"started": False, "reason": "已有索引任务进行中", "progress": task_progress()}
        _running = True

    if paths is not None:
        _progress_start(mode, len(paths))
        total_hint = len(paths)
    else:
        total_hint = 0

    def _worker() -> None:
        # delay 用于启动钩子：先让服务对外可用，再做重活
        if delay:
            time.sleep(delay)
        try:
            if paths is None:
                conn = conn_factory()
                try:
                    vault.rescan_all(conn)
                    targets = autofill_targets(conn) if mode == "autofill" else [
                        r["path"] for r in conn.execute("SELECT path FROM note_file").fetchall()
                    ]
                finally:
                    conn.close()
                _progress_start(mode, len(targets))
                broker.publish("index.updated", {"status": "task_start", "mode": mode,
                                                 "progress": task_progress()})
                _run_task(conn_factory, mode, targets, force=force)
            else:
                broker.publish("index.updated", {"status": "task_start", "mode": mode,
                                                 "progress": task_progress()})
                _run_task(conn_factory, mode, paths, force=force)
        except Exception:  # noqa: BLE001 —— 后台任务异常必须记录且不传播
            logger.exception("后台索引任务失败")
            _progress_end()
            with _task_lock:
                _running = False

    threading.Thread(target=_worker, daemon=True, name="indexer-worker").start()
    return {"started": True, "mode": mode, "total": total_hint, "progress": task_progress()}

