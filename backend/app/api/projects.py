"""项目（04 文档 3.4：/api/projects，FR-PROJ-01/02 + FR-NOTE-09 状态变更）。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pathlib import Path
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import vault

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects(
    status: str | None = None,
    customer: str | None = None,
    conn: Connection = Depends(get_db),
) -> list[dict]:
    """项目列表：由 note_file 聚合（customer + project_name 一组 = 一个项目）。"""
    vault.rescan_all(conn)
    sql = "SELECT * FROM note_file WHERE project_name IS NOT NULL AND project_name != ''"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if customer:
        sql += " AND customer = ?"
        params.append(customer)
    sql += " ORDER BY updated_date DESC"
    rows = [
        dict(r)
        for r in conn.execute(sql, params).fetchall()
        # 外部知识库白名单目录（绝对路径主键）不属于售后项目，不进首页项目列表
        if not Path(r["path"]).is_absolute()
    ]

    projects: dict[tuple, dict] = {}
    for r in rows:
        # 卡片式售后记录（按日卡片，无 project 字段）：一条记录一项，展示名取正文 H1
        is_card = r["note_mode"] == "card"
        key = (r["customer"] or "", r["path"]) if is_card else (r["customer"] or "", r["project_name"])
        if key not in projects:
            projects[key] = {
                "customer": r["customer"],
                "project_name": (r["note_title"] or r["project_name"]) if is_card else r["project_name"],
                "status": r["status"],
                "start_date": r["start_date"],
                "updated_date": r["updated_date"],
                "note_path": r["path"],
                "head_ok": r["head_ok"],
            }
    return list(projects.values())


class ProjectPatchBody(BaseModel):
    status: str  # active | archived（FR-NOTE-09）


@router.patch("/{path:path}")
def patch_project(path: str, body: ProjectPatchBody, conn: Connection = Depends(get_db)) -> dict:
    """更新项目状态：写入 vault 头部 status 字段（FR-NOTE-09），随后由监听触发增量索引。"""
    if body.status not in ("active", "archived"):
        raise HTTPException(status_code=422, detail={"code": "bad_request", "message": "status 仅支持 active / archived"})
    try:
        row = vault.set_status(conn, path, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "bad_request", "message": "该笔记缺少 status 头部字段，请先通过存量整理补齐"}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "note_not_found", "message": "笔记不存在"}) from exc
    except vault.PathOutsideRoot as exc:
        raise HTTPException(status_code=400, detail={"code": "path_outside_root", "message": "路径越出配置根目录"}) from exc
    return {"updated": True, "note": row}
