"""PDF 目录与阅读器（04 文档 3.8/3.9：M9 浏览 + C2 双模式统一入口）。

- 目录扫描结果全部落 manual 表（Q-15），本模块只做「扫描 → 组装视图」，不复制 PDF；
- 文件流端点必须限制在 pdf_root 内（api-rules 第 8 节防目录穿越）；
- 阅读元信息额外返回章节 → 页码聚合，供前端跳页高亮（FR-RAG-24）。
"""

from pathlib import Path
from sqlite3 import Connection

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import get_db
from app.services import manual_index, pdf_scan

router = APIRouter(prefix="/api", tags=["pdf"])

# 手册状态 → 前端筛选口径（M9：已入库/未入库/处理中/异常）
STATUS_GROUPS: dict[str, tuple[str, ...]] = {
    "ready": ("ready",),
    "unindexed": ("discovered",),
    "processing": ("queue", "parsing", "rebuilding"),
    "error": ("failed", "invalid", "update"),
}


def _root(conn: Connection, scope: str = "manual") -> Path:
    """校验对应页面至少有一个可用目录。

    - 手册页（manual）：必须有「PDF 手册索引位置」；
    - 知识库页（kb）：必须有「数据库索引位置」。
    """
    try:
        if scope == "kb":
            roots = pdf_scan.kb_roots(conn)
            if not roots:
                raise pdf_scan.PdfRootError(
                    "尚未配置「数据库索引位置」：只有该目录内的 PDF 才能进知识库"
                )
            return roots[0]
        return pdf_scan.pdf_root(conn)
    except pdf_scan.PdfRootError as e:
        raise HTTPException(status_code=400, detail={"code": "path_invalid", "message": str(e)}) from e


@router.get("/pdf/tree")
def pdf_tree(
    scope: str = Query("manual", pattern="^(manual|kb)$", description="manual=手册页；kb=知识库（数据库索引位置）"),
    conn: Connection = Depends(get_db),
) -> dict:
    """扫描配置目录并返回按文件夹分组的目录树（含未入库文件与状态）。

    每次请求都会做一次增量同步：新增登记为 discovered、mtime 变化标 update、
    消失的标 invalid —— 这样用户在资源管理器里拷进 PDF，刷新页面就能看到。

    两个页面各看各的目录：手册页（默认）只列「PDF 手册索引位置」内的 PDF，
    知识库页（scope='kb'）只列「数据库索引位置」内的 PDF。
    """
    _root(conn, scope)
    summary = pdf_scan.scan(conn)
    conn.commit()
    data = pdf_scan.tree(conn, scope=scope)
    return {**data, "scan": summary}


@router.post("/pdf/rescan")
def pdf_rescan(
    scope: str = Query("manual", pattern="^(manual|kb)$"),
    conn: Connection = Depends(get_db),
) -> dict:
    """手动重新扫描：返回差异摘要 {added, updated, invalid, removed}。"""
    _root(conn, scope)
    summary = pdf_scan.scan(conn)
    conn.commit()
    return summary


@router.get("/pdf/search")
def pdf_search(
    keyword: str | None = Query(None),
    brand: str | None = Query(None),
    status: str | None = Query(None, description="ready|unindexed|processing|error"),
    conn: Connection = Depends(get_db),
) -> list[dict]:
    """手册筛选检索：名称/相对路径关键词 + 品牌 + 入库状态组合过滤。"""
    _root(conn)
    sql = (
        "SELECT m.*, (SELECT COUNT(*) FROM chunk c WHERE c.manual_id=m.id) AS chunks"
        " FROM manual m WHERE 1=1"
    )
    params: list = []
    if brand:
        sql += " AND IFNULL(m.brand,'') = ?"
        params.append(brand)
    if status:
        keys = STATUS_GROUPS.get(status)
        if not keys:
            raise HTTPException(status_code=422, detail={"code": "bad_request", "message": "status 取值不合法"})
        sql += f" AND m.status IN ({','.join('?' * len(keys))})"
        params.extend(keys)
    if keyword:
        sql += " AND (m.name LIKE ? OR m.path LIKE ?)"
        like = f"%{keyword}%"
        params.extend([like, like])
    sql += " ORDER BY m.path"
    out = []
    for row in conn.execute(sql, params).fetchall():
        item = dict(row)
        own_root = pdf_scan.root_of(conn, Path(item["path"]))
        try:
            item["rel_path"] = (
                Path(item["path"]).resolve().relative_to(own_root).as_posix()
                if own_root
                else Path(item["path"]).name
            )
        except (ValueError, OSError):
            item["rel_path"] = Path(item["path"]).name
        out.append(item)
    return out


@router.get("/reader/pdf/{manual_id}")
def reader_pdf(manual_id: int, conn: Connection = Depends(get_db)):
    """PDF 字节流（Starlette FileResponse 原生支持 Range/断点续读）。

    C2 双模式共用：已入库手册带页码跳转，未入库只做本地预览；
    路径必须位于任一「PDF 手册索引位置」或「数据库索引位置」内。
    """
    row = conn.execute("SELECT path, name FROM manual WHERE id = ?", (manual_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "手册不存在"})
    pdf_path = Path(row["path"])
    if not pdf_scan.in_scan_roots(conn, pdf_path):
        raise HTTPException(status_code=400, detail={"code": "path_outside_root", "message": "路径越界"})
    if not pdf_path.is_file():
        raise HTTPException(status_code=410, detail={"code": "file_gone", "message": "PDF 文件不存在（路径失效）"})
    # inline：供阅读器内嵌显示（attachment 会触发下载，C2 就没法预览了）
    return FileResponse(
        pdf_path, media_type="application/pdf", filename=f"{row['name']}.pdf",
        content_disposition_type="inline",
    )


@router.get("/reader/meta/{manual_id}")
def reader_meta(manual_id: int, conn: Connection = Depends(get_db)) -> dict:
    """阅读元信息 + 章节页码表（RAG 引用跳页 / 侧边目录）。"""
    row = conn.execute(
        "SELECT id, name, brand, pages, status, error FROM manual WHERE id = ?", (manual_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "手册不存在"})
    chapters = [
        {"title_path": r["title_path"], "page": r["page"], "chunks": r["n"]}
        for r in conn.execute(
            "SELECT title_path, MIN(page) AS page, COUNT(*) AS n FROM chunk"
            " WHERE manual_id=? AND status='ready' AND title_path IS NOT NULL"
            " GROUP BY title_path ORDER BY page, MIN(seq)",
            (manual_id,),
        )
    ]
    return {**dict(row), "chapters": chapters}


@router.get("/ingest/tasks")
def list_tasks(
    status: str | None = Query(None, pattern="^(queued|running|done|failed|canceled)$"),
    limit: int = Query(50, ge=1, le=200),
    conn: Connection = Depends(get_db),
) -> list[dict]:
    """入库队列任务（失败可见可重试，FR-RAG-08）。"""
    sql = (
        "SELECT t.*, m.name AS manual_name, m.status AS manual_status"
        " FROM ingest_task t LEFT JOIN manual m ON m.id = t.manual_id"
    )
    params: list = []
    if status:
        sql += " WHERE t.status = ?"
        params.append(status)
    sql += " ORDER BY t.created_at DESC, t.id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params)]


@router.get("/ingest/state")
def ingest_state() -> dict:
    """当前入库进度快照（断线重连兜底；正常走 WS ingest.progress）。"""
    return manual_index.task_state()
