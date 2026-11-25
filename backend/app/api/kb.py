"""知识库（04 文档 3.6：/api/kb/*，M3 白名单/索引状态/统计）。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import config_service, indexer, vault
from app.ws.events import broker

router = APIRouter(prefix="/api/kb", tags=["kb"])


@router.get("/whitelist")
def get_whitelist(conn: Connection = Depends(get_db)) -> dict:
    """索引白名单：一期文件夹级（Q-05）；响应预留 files/excluded 扩展字段。"""
    folders = config_service.get_raw(conn, "index.whitelist").get("folders") or []
    return {
        "folders": folders,
        "invalid": [f for f in folders if not _entry_dir_exists(conn, f)],
        "files": [],
        "excluded": [],
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

    affected = reindexed = 0
    for r in conn.execute("SELECT path FROM note_file").fetchall():
        rel = r["path"]
        if indexer._in_whitelist(conn, rel):
            result = indexer.index_note(conn, rel)
            if result["status"] in ("ready", "skipped"):
                reindexed += 1
        else:
            result = indexer.index_note(conn, rel)  # 移出白名单 → 删块 + excluded
            affected += 1
    conn.commit()
    broker.publish("index.updated", {"reason": "whitelist_changed", "excluded": affected,
                                     "indexed": reindexed, "folders": folders})
    return {"folders": folders, "invalid": invalid, "excluded": affected, "indexed": reindexed}


@router.get("/notes")
def kb_notes(
    indexed_status: str | None = Query(None, pattern="^(ready|pending|failed|excluded)$"),
    conn: Connection = Depends(get_db),
) -> list[dict]:
    """索引状态总览：全部白名单内笔记 + 块数统计；失败可见可重试。"""
    vault.rescan_all(conn)
    sql = (
        "SELECT n.path, n.customer, n.project_name, n.head_ok, n.indexed_status, n.last_error,"
        " (SELECT COUNT(*) FROM chunk c WHERE c.note_path = n.path) AS chunks"
        " FROM note_file n"
    )
    params: list = []
    if indexed_status:
        sql += " WHERE n.indexed_status = ?"
        params.append(indexed_status)
    sql += " ORDER BY n.path"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


@router.post("/reindex")
def reindex_all(conn: Connection = Depends(get_db)) -> dict:
    """全量重建笔记索引（幂等）。同步执行（个人规模秒级）；进度经 index.updated 推送。"""
    vault.rescan_all(conn)
    return indexer.reindex_all_notes(conn, force=True)


@router.get("/stats")
def stats(conn: Connection = Depends(get_db)) -> dict:
    """笔记数/手册数/切块总数/向量模型标识与维度（R4）。"""
    chunk_count = conn.execute("SELECT COUNT(*) c FROM chunk WHERE status='ready'").fetchone()["c"]
    note_count = conn.execute("SELECT COUNT(*) c FROM note_file").fetchone()["c"]
    manual_count = conn.execute("SELECT COUNT(*) c FROM manual WHERE status='ready'").fetchone()["c"]
    row = conn.execute("SELECT DISTINCT model_id, dim FROM chunk LIMIT 1").fetchone()
    return {
        "notes": note_count,
        "manuals": manual_count,
        "chunks": chunk_count,
        "vector_model": {"model_id": row["model_id"] if row else None, "dim": row["dim"] if row else None},
    }


class ReindexBody(BaseModel):
    path: str


@router.post("/notes/reindex")
def reindex_one(body: ReindexBody, conn: Connection = Depends(get_db)) -> dict:
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
