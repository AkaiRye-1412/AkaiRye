"""代码仓库只读（M6 / FR-GIT · 三期 · 04 文档 7 章落位：/api/repos）。

- GET    /api/repos                列表（置顶优先；hidden 默认不返回）
- POST   /api/repos/refresh        扫描 + 刷新状态（发现仓库、读 git 只读状态）
- PATCH  /api/repos/{path}         置顶/取消置顶/隐藏（display）
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import git_service

router = APIRouter(prefix="/api/repos", tags=["repos"])


class RepoPatch(BaseModel):
    display: str  # pin | normal | hidden


@router.get("")
def list_repos(include_hidden: bool = False, conn: Connection = Depends(get_db)) -> list[dict]:
    return git_service.list_repos(conn, include_hidden=include_hidden)


@router.post("/refresh")
def refresh(conn: Connection = Depends(get_db)) -> dict:
    try:
        return git_service.refresh(conn)
    except git_service.GitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "git_unavailable", "message": str(exc)},
        ) from exc


@router.patch("")
def patch_repo(
    path: str = Query(..., description="仓库绝对路径"),
    body: RepoPatch = None,
    conn: Connection = Depends(get_db),
) -> dict:
    # 用查询参数传 path，避免 {path:path} 贪婪匹配吞掉 /refresh 静态路由（曾导致 405）
    try:
        return git_service.set_display(conn, path, body.display)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "bad_request", "message": str(exc)},
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "resource_not_found", "message": "仓库不存在，请先刷新"},
        ) from exc
