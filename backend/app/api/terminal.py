"""终端标签 CRUD（M5 / FR-TERM · 三期 · 04 文档 7 章落位：/api/terminal/tabs）。

- 标签元数据持久化 terminal_tab 表；PTY 进程由 WS 附着时启动、随标签常驻；
- 关闭运行中标签需二次确认（FR-TERM-07）：DELETE 不带 force 时若进程存活返回 409 state_conflict，
  前端据此弹确认后带 force=true 重发。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import terminal as terminal_service

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


class TabCreate(BaseModel):
    cwd: str | None = None
    title: str | None = None


class TabRename(BaseModel):
    title: str


@router.get("/tabs")
def list_tabs() -> list[dict]:
    return terminal_service.manager.list_tabs()


@router.post("/tabs", status_code=201)
def create_tab(body: TabCreate, conn: Connection = Depends(get_db)) -> dict:
    tab = terminal_service.manager.create_tab(conn, cwd=body.cwd, title=body.title)
    return _tab_dict(tab)


@router.get("/tabs/{tab_id}")
def get_tab(tab_id: str) -> dict:
    tab = terminal_service.manager.get_tab(tab_id)
    if tab is None:
        raise HTTPException(status_code=404, detail={"code": "resource_not_found", "message": "终端标签不存在"})
    return _tab_dict(tab)


@router.patch("/tabs/{tab_id}")
def rename_tab(tab_id: str, body: TabRename, conn: Connection = Depends(get_db)) -> dict:
    tab = terminal_service.manager.rename_tab(conn, tab_id, body.title)
    if tab is None:
        raise HTTPException(status_code=404, detail={"code": "resource_not_found", "message": "终端标签不存在"})
    return _tab_dict(tab)


@router.delete("/tabs/{tab_id}")
def close_tab(tab_id: str, force: bool = False, conn: Connection = Depends(get_db)) -> dict:
    result = terminal_service.manager.close_tab(conn, tab_id, force=force)
    if not result["closed"]:
        if result.get("reason") == "alive":
            raise HTTPException(
                status_code=409,
                detail={"code": "state_conflict", "message": "标签中仍有进程在运行，关闭将终止该进程"},
            )
        raise HTTPException(status_code=404, detail={"code": "resource_not_found", "message": "终端标签不存在"})
    return result


def _tab_dict(tab: terminal_service.TerminalTab) -> dict:
    return {
        "tab_id": tab.tab_id,
        "cwd": tab.cwd,
        "title": tab.title,
        "sort": tab.sort,
        "alive": tab.alive,
        "created_at": tab.created_at,
    }
