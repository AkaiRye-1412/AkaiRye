"""PDF 目录与阅读器（04 文档 3.8/3.9：M9 浏览 + C2 双模式统一入口）。"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlite3 import Connection

from app.api.deps import get_db
from app.services.config_service import load_all

router = APIRouter(prefix="/api", tags=["pdf"])


def _pdf_root(conn: Connection) -> Path:
    root = Path(load_all(conn)["paths"].get("pdf_root", ""))
    if not root.is_dir():
        raise HTTPException(status_code=400, detail={"code": "path_invalid", "message": "pdf_root 未配置或不存在"})
    return root


@router.get("/pdf/tree")
def pdf_tree(conn: Connection = Depends(get_db)) -> dict:
    """扫描 pdf_root（新文件建 manual/discovered，Q-15）。TODO(业务实现)：pdf_scan_service。"""
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "PDF 目录树：业务实现阶段提供"})


@router.post("/pdf/rescan")
def pdf_rescan(conn: Connection = Depends(get_db)) -> dict:
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "重新扫描：业务实现阶段提供"})


@router.get("/pdf/search")
def pdf_search(conn: Connection = Depends(get_db)) -> list[dict]:
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "手册筛选：业务实现阶段提供"})


@router.get("/reader/pdf/{manual_id}")
def reader_pdf(manual_id: int, conn: Connection = Depends(get_db)):
    """PDF 字节流（支持 Range）；C2 双模式共用（已入库跳转 + 未入库预览，Q-04）。

    路径安全：必须位于 pdf_root 内（api-rules 第 8 节防目录穿越）。
    TODO(业务实现)：FileResponse 已支持 Range；需先按 manual_id 查 path 并校验越界。
    """
    row = conn.execute("SELECT path, name FROM manual WHERE id = ?", (manual_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "手册不存在"})
    root = _pdf_root(conn).resolve()
    pdf_path = Path(row["path"])
    try:
        pdf_path.resolve().relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": "path_outside_root", "message": "路径越界"}) from e
    if not pdf_path.is_file():
        raise HTTPException(status_code=410, detail={"code": "file_gone", "message": "PDF 文件不存在（路径失效）"})
    return FileResponse(pdf_path, media_type="application/pdf", filename=row["name"])


@router.get("/reader/meta/{manual_id}")
def reader_meta(manual_id: int, conn: Connection = Depends(get_db)) -> dict:
    row = conn.execute(
        "SELECT id, name, brand, pages, status FROM manual WHERE id = ?", (manual_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "手册不存在"})
    return dict(row)
