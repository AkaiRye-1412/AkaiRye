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

from app.services import config_service, textutil, vault
from app.ws.events import broker

logger = logging.getLogger(__name__)

FTS_ONLY_MODEL = "(fts-only)"
# 嵌入输入截断长度：省 embedding 配额（长笔记块数多时触发 429）
EMBED_MAX_CHARS = 700

# 后台任务串行化（个人单机场景：同时只跑一个索引任务）
_task_lock = threading.Lock()
_running = False


# ---- 配置与白名单 ----------------------------------------------------------

def _embedding_cfg(conn) -> dict:
    return config_service.get_raw(conn, "embedding")


def _model_id(cfg: dict) -> str:
    return cfg.get("model") or FTS_ONLY_MODEL


def _in_whitelist(conn, rel: str) -> bool:
    """范围判定（2026-11-26 扩展）：
    - 售后文件夹根下的文件恒在（用户日期命名售后卡形态）；
    - 其余按归一化白名单目录前缀匹配（子文件夹名 / vault 相对路径 / 绝对路径均可）。
    """
    p = Path(rel)
    if not p.is_absolute():
        base = vault.notes_dir(conn)
        abs_p = (base / rel).resolve()
        if abs_p.parent == base:
            return True
    else:
        abs_p = p.resolve()
    for _key, abs_dir in vault.whitelist_abs_dirs(conn):
        if abs_p == abs_dir or abs_p.is_relative_to(abs_dir):
            return True
    return False


def _embed_texts(conn, texts: list[str]) -> list[list[float]] | None:
    """调用 embedding 接口；未配置返回 None（FTS-only 降级），失败抛异常。"""
    cfg = _embedding_cfg(conn)
    # 兼容两种保存键名：base_url（代码约定）/ provider（设置页实际写入，2026-11-26 修复）
    base_url = cfg.get("base_url") or cfg.get("provider")
    if not base_url or not cfg.get("model"):
        return None
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=cfg.get("api_key") or "sk-empty")
    # 分批调用：火山 Embeddings API 单次 input 上限 10 条（2026-11-26 实测）；
    # 突发频率限制严格（429），批次间节流 + 指数退避重试
    out: list[list[float]] = []
    batch = 10
    for i in range(0, len(texts), batch):
        part = texts[i : i + batch]
        resp = None
        for attempt in range(5):
            try:
                resp = client.embeddings.create(model=cfg["model"], input=part)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 4:
                    raise
                wait = 3.0 * (attempt + 1)
                logger.warning(
                    "embedding 批次限频/失败，%.0fs 后重试（%s/4）：%s", wait, attempt + 1, str(e)[:120]
                )
                time.sleep(wait)
        out.extend(d.embedding for d in resp.data)
        if i + batch < len(texts):
            time.sleep(3.0)  # 批次间节流，规避 429 限频
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

    if not _in_whitelist(conn, rel):
        deleted = _delete_chunks(conn, rel)
        _set_status(conn, rel, "excluded")
        return {"status": "excluded", "chunks": 0, "reason": f"移出白名单（删 {deleted} 块）"}

    abs_path = vault.resolve_note_abs(conn, rel)
    if not abs_path.is_file():
        _set_status(conn, rel, "failed", "文件不存在（可能已被移动或删除）")
        return {"status": "missing", "chunks": 0, "reason": "文件不存在"}
    text = abs_path.read_bytes().decode("utf-8", errors="replace")
    pieces = textutil.chunk_note_markdown(text)
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

    # 嵌入输入：标题 + 内容前段（截断可省配额；标题已能代表该块主题）
    embed_inputs = [f"{p['title_path'] or ''}\n{p['content'][:EMBED_MAX_CHARS]}".strip() for p in pieces]
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
    """全量重建（POST /api/kb/reindex）。同步执行——个人规模（数百篇）秒级完成。"""
    vault.rescan_all(conn)
    done = failed = excluded = 0
    rows = conn.execute("SELECT path FROM note_file").fetchall()
    for r in rows:
        rel = r["path"]
        result = index_note(conn, rel, force=force)
        if result["status"] in ("ready", "skipped"):
            done += 1
        elif result["status"] == "excluded":
            excluded += 1
        else:
            failed += 1
        broker.publish("index.updated", {"note_path": rel, "status": result["status"],
                                         "chunks": result["chunks"]})
    conn.commit()
    return {"done": done, "failed": failed, "excluded": excluded, "total": len(rows)}


def reindex_async(conn_factory, note_paths: list[str] | None = None, *, force: bool = True) -> dict:
    """后台线程执行索引（API 立即返回任务确认；进度经 index.updated 推送）。"""
    global _running
    with _task_lock:
        if _running:
            return {"started": False, "reason": "已有索引任务进行中"}
        _running = True

    def _worker() -> None:
        global _running
        try:
            conn = conn_factory()
            try:
                if note_paths is None:
                    reindex_all_notes(conn, force=force)
                else:
                    for rel in note_paths:
                        index_note(conn, rel, force=force)
                        broker.publish("index.updated", {"note_path": rel, "status": "indexed"})
                    conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 —— 后台任务异常必须记录且不传播
            logger.exception("后台索引任务失败")
        finally:
            with _task_lock:
                _running = False

    threading.Thread(target=_worker, daemon=True, name="indexer-worker").start()
    return {"started": True, "notes": len(note_paths) if note_paths else "all"}
