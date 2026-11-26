"""知识库（04 文档 3.6：/api/kb/*，M3 白名单/索引状态/统计）。

M3 一期（笔记侧）契约要点：
- 自动索引：白名单内的笔记无需手动触发，启动/白名单变更/保存后自动增量（FR-RAG-01~03）；
- 全量重建与补齐缺失都走后台任务（embedding 有节流，大库同步调用会 HTTP 超时），
  进度经 GET /api/kb/stats 的 indexing 字段 + WS index.updated 获取；
- 白名单条目支持「售后文件夹子目录名 / vault 相对路径 / 绝对路径（外部知识库）」；
- 展示字段：display_name（相对所属根目录的路径）+ group（归属分组），前端不必解析绝对路径。
"""

from pathlib import Path
from sqlite3 import Connection
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_db
from app.db import database
from app.services import config_service, indexer, vault
from app.ws.events import broker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb", tags=["kb"])

# 白名单变更后同步索引的篇数上限：超过即转后台任务（embedding 有 3s/批 节流）
SYNC_INDEX_LIMIT = 8


class ReindexBody(BaseModel):
    """全量重建参数：force=忽略 hash 强制重建；background=后台执行（推荐）。"""

    force: bool = True
    background: bool = False


# ---- 白名单 ----------------------------------------------------------------

@router.get("/whitelist")
def get_whitelist(conn: Connection = Depends(get_db)) -> dict:
    """索引白名单：文件夹级（Q-05）+ 单篇移出名单（excluded）。"""
    cfg = config_service.get_raw(conn, "index.whitelist")
    folders = cfg.get("folders") or []
    return {
        "folders": folders,
        "invalid": [f for f in folders if not _entry_dir_exists(conn, f)],
        "files": [],
        "excluded": [x for x in (cfg.get("excluded") or []) if isinstance(x, str)],
        "notes_folder": config_service.get_raw(conn, "paths").get("notes_folder"),
    }


def _entry_dir_exists(conn, entry: str) -> bool:
    try:
        _key, abs_dir = vault.resolve_entry(conn, entry)
    except Exception:  # noqa: BLE001 —— 非法路径按不存在处理
        return False
    return abs_dir.is_dir()


@router.put("/whitelist")
def put_whitelist(body: dict, conn: Connection = Depends(get_db)) -> dict:
    """更新白名单（03 场景 2）：移出范围删块三件套 + excluded；纳入范围触发增量索引。

    条目支持：售后文件夹下的子文件夹名 / vault 相对路径 / 绝对路径（外部知识库，
    2026-11-26 扩展）。目录不存在的条目保留但经 invalid 字段报告，不静默吞掉。
    新增的条目会让一批既有笔记变为「待索引」，这里直接启动后台补齐任务
    （此前只有文件被保存时才会索引，历史笔记永远停在未索引 —— 2026-11-26 修复）。
    """
    folders = body.get("folders")
    if not isinstance(folders, list) or any(not isinstance(f, str) or not f.strip() for f in folders):
        raise HTTPException(status_code=422, detail={"code": "bad_request", "message": "folders 必须为非空字符串数组"})
    folders = [f.strip().replace("\\", "/").rstrip("/") or f.strip() for f in folders]
    # 去重保序
    folders = list(dict.fromkeys(folders))
    invalid = [f for f in folders if not _entry_dir_exists(conn, f)]
    config_service.save_many(conn, {"index.whitelist": {"folders": folders}})
    vault.rescan_all(conn)

    # 移出的条目：立即删块并标 excluded（快操作，无需后台）
    affected = 0
    for r in conn.execute("SELECT path FROM note_file").fetchall():
        rel = r["path"]
        if not indexer._in_whitelist(conn, rel):
            indexer.index_note(conn, rel)  # 内部走「移出白名单 → 删块 + excluded」分支
            affected += 1
    conn.commit()

    # 纳入的条目：待补齐的笔记
    # 少量（≤ SYNC_INDEX_LIMIT）直接同步做完，接口即可返回真实结果；
    # 大批（如新挂一个几十篇的目录）转后台，避免 embedding 节流把 HTTP 请求卡到超时。
    pending = indexer.autofill_targets(conn)
    indexed = 0
    started = False
    if len(pending) <= SYNC_INDEX_LIMIT:
        for rel in pending:
            try:
                if indexer.index_note(conn, rel, force=False)["status"] in ("ready", "skipped"):
                    indexed += 1
            except Exception:  # noqa: BLE001 —— 单篇失败不影响白名单保存
                logger.exception("白名单变更后索引失败：%s", rel)
        conn.commit()
    elif pending:
        started = indexer.start_task(
            database.connect, mode="autofill", paths=pending, force=False
        ).get("started", False)

    broker.publish("index.updated", {"reason": "whitelist_changed", "excluded": affected,
                                     "pending": len(pending), "folders": folders})
    return {
        "folders": folders,
        "invalid": invalid,
        "excluded": affected,
        "indexed": indexed,
        "pending": len(pending),
        "indexing": started,
    }


# ---- 状态总览 --------------------------------------------------------------

def _present(conn, rel: str) -> tuple[str, str]:
    """(display_name, group)：把绝对路径收敛成「分组长 + 相对路径」，避免列表被长路径撑爆。"""
    p = Path(rel)
    base = vault.notes_dir(conn)
    abs_p = p if p.is_absolute() else (base / rel)
    try:
        rel_notes = abs_p.resolve().relative_to(base.resolve())
        return rel_notes.as_posix(), "售后卡片"
    except (ValueError, OSError):
        pass
    for key, abs_dir in vault.whitelist_abs_dirs(conn):
        try:
            rel_dir = abs_p.resolve().relative_to(Path(abs_dir).resolve())
            return rel_dir.as_posix(), Path(key).name or key
        except (ValueError, OSError):
            continue
    return rel, "其他"


@router.get("/notes")
def kb_notes(
    indexed_status: str | None = Query(None, pattern="^(ready|pending|failed|excluded)$"),
    keyword: str | None = Query(None, description="按路径/标题/客户过滤"),
    conn: Connection = Depends(get_db),
) -> list[dict]:
    """索引状态总览：全部笔记 + 块数统计；失败可见可重试。"""
    vault.rescan_all(conn)
    sql = (
        "SELECT n.path, n.customer, n.project_name, n.head_ok, n.indexed_status, n.last_error,"
        " n.note_title, n.summary, n.updated_date,"
        " (SELECT COUNT(*) FROM chunk c WHERE c.note_path = n.path) AS chunks"
        " FROM note_file n"
    )
    where: list[str] = []
    params: list = []
    if indexed_status:
        where.append("n.indexed_status = ?")
        params.append(indexed_status)
    if keyword:
        # 关键词在 SQL 里粗筛（路径/客户/标题），前端仍会再做一次精确过滤
        where.append("(n.path LIKE ? OR IFNULL(n.customer,'') LIKE ? OR IFNULL(n.note_title,'') LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY n.path"
    out = []
    for row in conn.execute(sql, params).fetchall():
        item = dict(row)
        item["display_name"], item["group"] = _present(conn, item["path"])
        out.append(item)
    return out


@router.post("/reindex")
def reindex_all(body: ReindexBody | None = None, conn: Connection = Depends(get_db)) -> dict:
    """全量重建笔记索引（幂等）。

    - 默认同步（测试与小库直接拿结果）；
    - `background=true` → 后台任务，立即返回 {started,total}，进度看 /api/kb/stats。
      前端一律用后台模式：1425 块 embedding 需数分钟，同步必超时。
    """
    force = True if body is None else body.force
    if body is not None and body.background:
        return indexer.start_task(database.connect, mode="full", paths=None, force=force)
    return indexer.reindex_all_notes(conn, force=force)


@router.post("/autofill")
def autofill(conn: Connection = Depends(get_db)) -> dict:
    """补齐缺失索引（FR-RAG-01）：把白名单内未索引/缺块/失败的笔记排进后台队列。"""
    vault.rescan_all(conn)
    targets = indexer.autofill_targets(conn)
    conn.commit()
    if not targets:
        return {"started": False, "pending": 0, "reason": "没有待补齐的笔记"}
    result = indexer.start_task(database.connect, mode="autofill", paths=targets, force=False)
    return {**result, "pending": len(targets)}


@router.get("/stats")
def stats(conn: Connection = Depends(get_db)) -> dict:
    """笔记数/手册数/切块总数/向量模型标识与维度（R4）+ 索引任务进度 + 最近索引时间。"""
    chunk_count = conn.execute("SELECT COUNT(*) c FROM chunk WHERE status='ready'").fetchone()["c"]
    note_count = conn.execute("SELECT COUNT(*) c FROM note_file").fetchone()["c"]
    manual_count = conn.execute("SELECT COUNT(*) c FROM manual WHERE status='ready'").fetchone()["c"]
    row = conn.execute("SELECT DISTINCT model_id, dim FROM chunk LIMIT 1").fetchone()

    by_status = {"ready": 0, "pending": 0, "failed": 0, "excluded": 0, "unindexed": 0}
    for r in conn.execute(
        "SELECT IFNULL(indexed_status,'unindexed') s, COUNT(*) n FROM note_file GROUP BY s"
    ):
        by_status[r["s"]] = r["n"]

    last = conn.execute("SELECT MAX(indexed_at) t FROM chunk").fetchone()["t"]
    return {
        "notes": note_count,
        "notes_by_status": by_status,
        "manuals": manual_count,
        "chunks": chunk_count,
        "vector_model": {"model_id": row["model_id"] if row else None, "dim": row["dim"] if row else None},
        "indexing": indexer.task_progress(),
        "last_indexed_at": last,
    }


class ReindexPathBody(BaseModel):
    path: str


@router.post("/notes/reindex")
def reindex_one(body: ReindexPathBody, conn: Connection = Depends(get_db)) -> dict:
    """单篇重索引（失败重试入口，FR-RAG-03）。同步触发，返回任务结果。"""
    try:
        vault.safe_rel_path(conn, body.path)
    except vault.PathOutsideRoot as exc:
        raise HTTPException(status_code=400, detail={"code": "path_outside_root", "message": "路径越出配置根目录"}) from exc
    vault.rescan_all(conn)  # 保证 note_file 行就位（新建库/新配置后单篇直接索引的场景）
    result = indexer.index_note(conn, body.path, force=True)
    conn.commit()
    if result["status"] == "missing":
        raise HTTPException(status_code=404, detail={"code": "note_not_found", "message": "笔记不存在"})
    return result


class ExcludeBody(BaseModel):
    """单篇移出/恢复知识库：excluded=True 移出，False 恢复索引。"""

    path: str
    excluded: bool = True


@router.post("/notes/exclude")
def exclude_note(body: ExcludeBody, conn: Connection = Depends(get_db)) -> dict:
    """把单篇笔记移出 / 恢复知识库（2026-11-26，知识库列表行内操作）。

    语义是「退出检索」，**不动磁盘文件**：移出 → 删块三件套 + status=excluded，
    并记入 config `index.whitelist.excluded`，此后的自动补齐 / 保存触发都不会把它拉回来；
    恢复 → 从名单移除并立即增量索引（文件已删除时返回 404）。
    所属文件夹仍不在白名单里时，恢复后会保持 excluded（范围外），由 in_whitelist 告知前端。
    """
    try:
        vault.safe_rel_path(conn, body.path)
    except vault.PathOutsideRoot as exc:
        raise HTTPException(status_code=400, detail={"code": "path_outside_root", "message": "路径越出配置根目录"}) from exc
    vault.rescan_all(conn)  # 保证 note_file 行就位，并清掉已删除文件的缓存行
    if conn.execute("SELECT 1 FROM note_file WHERE path=?", (body.path,)).fetchone() is None:
        raise HTTPException(status_code=404, detail={"code": "note_not_found", "message": "笔记不存在"})

    cfg = config_service.get_raw(conn, "index.whitelist")
    target = Path(body.path).as_posix()
    # 顺带清理已不存在文件的陈旧条目（避免名单无限增长）
    alive = {Path(r["path"]).as_posix() for r in conn.execute("SELECT path FROM note_file").fetchall()}
    current = [x for x in (cfg.get("excluded") or []) if isinstance(x, str) and Path(x).as_posix() in alive]
    if body.excluded:
        current = list(dict.fromkeys([*current, target]))
    else:
        current = [x for x in current if Path(x).as_posix() != target]
    config_service.save_many(conn, {"index.whitelist": {**cfg, "excluded": current}})

    # 移出 → index_note 走 excluded 分支删块；恢复 → 直接重建该篇
    result = indexer.index_note(conn, body.path, force=True)
    conn.commit()
    in_whitelist = indexer._in_whitelist(conn, body.path)
    broker.publish("index.updated", {"reason": "note_excluded" if body.excluded else "note_included",
                                     "note_path": body.path, "status": result["status"],
                                     "chunks": result["chunks"]})
    return {
        "path": body.path,
        "excluded": body.excluded,
        "status": result["status"],
        "chunks": result["chunks"],
        "in_whitelist": in_whitelist,
        "reason": result.get("reason", ""),
    }
