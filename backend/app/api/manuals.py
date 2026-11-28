"""手册与入库队列（04 文档 3.6/3.7：/api/manuals、/api/ingest，M3 队列管理）。

约定：
- 列表/详情只读 manual + chunk，不改磁盘；删除手册**只清索引**，不删 PDF（FR-RAG-09）；
- 入库/重建统一走队列（ingest_task），接口立即返回 {started, tasks}，进度看 WS 或轮询；
- 状态 8 态见 schema.sql，前端按 discovered/queue/parsing/rebuilding/ready/failed/update/invalid 展示。
"""

from pathlib import Path
from sqlite3 import Connection

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_db
from app.db import database
from app.services import manual_index, pdf_scan

router = APIRouter(prefix="/api", tags=["manuals"])


class IngestBody(BaseModel):
    """批量入库：显式 ids 优先；都不传 = 全部未入库（discovered）。"""

    manual_ids: list[int] | None = None
    action: str = "ingest"


class RelocateBody(BaseModel):
    path: str


@router.get("/manuals")
def list_manuals(
    keyword: str | None = Query(None, description="名称/路径关键词"),
    brand: str | None = Query(None),
    status: str | None = Query(None),
    conn: Connection = Depends(get_db),
) -> list[dict]:
    sql = (
        "SELECT m.*, (SELECT COUNT(*) FROM chunk c WHERE c.manual_id=m.id) AS chunks"
        " FROM manual m WHERE 1=1"
    )
    params: list = []
    if keyword:
        sql += " AND (m.name LIKE ? OR m.path LIKE ?)"
        like = f"%{keyword}%"
        params.extend([like, like])
    if brand:
        sql += " AND IFNULL(m.brand,'') = ?"
        params.append(brand)
    if status:
        sql += " AND m.status = ?"
        params.append(status)
    sql += " ORDER BY m.updated_at DESC, m.id DESC"
    return [dict(r) for r in conn.execute(sql, params)]


@router.post("/manuals/ingest")
def ingest_manuals(body: IngestBody | None = None, conn: Connection = Depends(get_db)) -> dict:
    """M9 一键入库触发；已在队列中的自动跳过（幂等）。

    只有位于「数据库索引位置」（paths.db_roots）内的 PDF 才能进知识库；
    「PDF 手册索引位置」内的 PDF 仅用于手册页浏览，不能入库。
    """
    ids = body.manual_ids if body else None
    action = (body.action if body else "ingest") or "ingest"
    if action not in ("ingest", "rebuild"):
        raise HTTPException(status_code=422, detail={"code": "bad_request", "message": "action 仅支持 ingest / rebuild"})
    if not ids:
        # NOT IN 子查询含 NULL（笔记块的 manual_id 为空）会整体失效，改用 NOT EXISTS
        sql = (
            "SELECT m.id FROM manual m WHERE m.status IN ('discovered','failed')"
            " AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.manual_id = m.id)"
        )
        if action == "rebuild":
            sql = "SELECT id FROM manual WHERE status IN ('ready','update','invalid','failed')"
        ids = [r["id"] for r in conn.execute(sql).fetchall()]

    # 入库范围闸门：仅「数据库索引位置」内的 PDF（按当前配置实时判定）
    in_kb = {
        r["id"]
        for r in conn.execute("SELECT id, path FROM manual").fetchall()
        if pdf_scan.in_kb_roots(conn, Path(r["path"]))
    }
    outside = [i for i in ids if i not in in_kb]
    ids = [i for i in ids if i in in_kb]
    if not ids:
        if outside:
            return {
                "started": False,
                "queued": [],
                "skipped": [],
                "skipped_outside": outside,
                "reason": "这些 PDF 不在「数据库索引位置」内，无法加入知识库",
            }
        return {"started": False, "queued": [], "skipped": [], "reason": "没有可入库的手册"}
    result = manual_index.enqueue(conn, ids, action=action)
    started = manual_index.start(database.connect) if result["queued"] else {"started": False}
    return {**result, **started, "pending": len(result["queued"]), "skipped_outside": outside}


@router.get("/manuals/{manual_id}")
def manual_detail(manual_id: int, conn: Connection = Depends(get_db)) -> dict:
    """手册详情：元数据 + 章节页码表 + 少量块预览（入库质量自检用）。"""
    row = conn.execute("SELECT * FROM manual WHERE id=?", (manual_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "手册不存在"})
    sections = [
        {"title_path": r["title_path"], "page": r["page"], "chunks": r["n"], "chars": r["chars"]}
        for r in conn.execute(
            "SELECT title_path, MIN(page) page, COUNT(*) n, SUM(LENGTH(content)) chars"
            " FROM chunk WHERE manual_id=? AND status='ready'"
            " GROUP BY title_path ORDER BY MIN(page), MIN(seq)",
            (manual_id,),
        )
    ]
    preview = [
        {"seq": r["seq"], "page": r["page"], "title_path": r["title_path"], "content": r["content"][:240]}
        for r in conn.execute(
            "SELECT seq, page, title_path, content FROM chunk WHERE manual_id=?"
            " ORDER BY seq LIMIT 5",
            (manual_id,),
        )
    ]
    chunks = conn.execute("SELECT COUNT(*) c FROM chunk WHERE manual_id=?", (manual_id,)).fetchone()["c"]
    return {**dict(row), "chunks": chunks, "path_name": Path(row["path"]).name,
            "sections": sections, "preview": preview}


@router.post("/manuals/{manual_id}/rebuild")
def rebuild(manual_id: int, conn: Connection = Depends(get_db)) -> dict:
    """重建索引：旧块保留到新解析完成，重建期间仍可检索（OQ-12）；仅知识库范围内的 PDF。"""
    row = conn.execute("SELECT path FROM manual WHERE id=?", (manual_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "手册不存在"})
    if not pdf_scan.in_kb_roots(conn, Path(row["path"])):
        raise HTTPException(
            status_code=400,
            detail={"code": "path_outside_root", "message": "该 PDF 不在「数据库索引位置」内，无法重建知识库索引"},
        )
    result = manual_index.enqueue(conn, [manual_id], action="rebuild")
    started = manual_index.start(database.connect) if result["queued"] else {"started": False}
    if result["skipped"]:
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": "该手册已在队列中"})
    return {**result, **started}


@router.post("/manuals/{manual_id}/relocate")
def relocate(manual_id: int, body: RelocateBody, conn: Connection = Depends(get_db)) -> dict:
    """重新定位（FR-RAG-09）：仅 invalid 可用；路径须在 pdf_root 内，哈希不一致拒绝。"""
    row = conn.execute("SELECT status FROM manual WHERE id=?", (manual_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "手册不存在"})
    if row["status"] != "invalid":
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": "仅「失效」状态的手册可以重新定位"})
    try:
        return manual_index.relocate(conn, manual_id, body.path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail={"code": "file_gone", "message": str(e)}) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": "path_invalid", "message": str(e)}) from e


@router.post("/manuals/{manual_id}/ignore-update")
def ignore_update(manual_id: int, conn: Connection = Depends(get_db)) -> dict:
    """忽略文件变化（FR-RAG-10）：同步 mtime，状态回 ready，不重建。"""
    try:
        return manual_index.ignore_update(conn, manual_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(e)}) from e


@router.delete("/manuals/{manual_id}")
def delete_manual(manual_id: int, conn: Connection = Depends(get_db)) -> dict:
    """单事务删块三件套 + 队列记录 + manual 行；**不删除磁盘 PDF**。"""
    try:
        return manual_index.delete_manual(conn, manual_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(e)}) from e


@router.post("/ingest/tasks/{task_id}/retry")
def retry_task(task_id: int, conn: Connection = Depends(get_db)) -> dict:
    """仅 failed 可重试，否则 409 state_conflict。"""
    row = conn.execute("SELECT * FROM ingest_task WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "任务不存在"})
    if row["status"] != "failed":
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": "仅失败任务可重试"})
    conn.execute(
        "UPDATE ingest_task SET status='queued', error=NULL, progress=0, started_at=NULL, finished_at=NULL"
        " WHERE id=?",
        (task_id,),
    )
    next_status = "rebuilding" if row["action"] == "rebuild" else "queue"
    conn.execute("UPDATE manual SET status=?, error=NULL WHERE id=?", (next_status, row["manual_id"]))
    conn.commit()
    return {**manual_index.start(database.connect), "task_id": task_id}


@router.post("/ingest/tasks/{task_id}/cancel")
def cancel_task(task_id: int, conn: Connection = Depends(get_db)) -> dict:
    """取消排队任务；已在解析中的任务不强行中断（避免写库半成品）。"""
    row = conn.execute("SELECT * FROM ingest_task WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "任务不存在"})
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": "任务正在执行，无法取消"})
    if row["status"] not in ("queued",):
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": "仅排队中的任务可取消"})
    conn.execute("UPDATE ingest_task SET status='canceled', finished_at=? WHERE id=?",
                 (manual_index._now(), task_id))
    has_chunks = conn.execute("SELECT 1 FROM chunk WHERE manual_id=? LIMIT 1", (row["manual_id"],)).fetchone()
    conn.execute(
        "UPDATE manual SET status=? WHERE id=?",
        ("ready" if has_chunks else "discovered", row["manual_id"]),
    )
    conn.commit()
    return {"task_id": task_id, "status": "canceled"}
