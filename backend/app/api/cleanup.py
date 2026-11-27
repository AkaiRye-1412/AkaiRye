"""存量整理（04 文档 7 章三期预留落位：/api/cleanup/batches，M7 · FR-CLEAN）。

流程：POST /batches 扫描生成草稿 → 前端预览编辑 → POST /execute 备份并写入
→ POST /rollback 按快照还原。错误包络沿用 api-rules：{error:{code,message}}。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import cleanup as service
from app.services import vault

router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(
            status_code=404, detail={"code": "batch_not_found", "message": "整理批次不存在"}
        )
    if isinstance(exc, service.CleanupUnresolved):
        return HTTPException(
            status_code=400,
            detail={"code": "cleanup_unresolved", "message": str(exc)},
        )
    if isinstance(exc, service.CleanupState):
        return HTTPException(
            status_code=409, detail={"code": "batch_state", "message": str(exc)}
        )
    if isinstance(exc, service.CleanupBackupFailed):
        return HTTPException(
            status_code=500, detail={"code": "backup_failed", "message": str(exc)}
        )
    if isinstance(exc, NotADirectoryError):
        return HTTPException(
            status_code=400,
            detail={
                "code": "vault_not_configured",
                "message": "vault 路径未配置或售后笔记目录不存在，请先在系统设置中配置",
            },
        )
    if isinstance(exc, vault.PathOutsideRoot):
        return HTTPException(
            status_code=400, detail={"code": "path_outside_root", "message": "路径越出配置根目录"}
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=400, detail={"code": "bad_request", "message": str(exc)}
        )
    raise exc


@router.get("/summary")
def cleanup_summary(conn: Connection = Depends(get_db)) -> dict:
    """页面头部：售后笔记总数、缺头部字段数、已知客户、最近批次。"""
    return service.summary(conn)


class BatchBody(BaseModel):
    scope: str = Field(default="missing", pattern="^(missing|all)$")


@router.post("/batches", status_code=201)
def create_batch(body: BatchBody | None = None, conn: Connection = Depends(get_db)) -> dict:
    """扫描存量并生成草稿批次（FR-CLEAN-01/02）。重复扫描会丢弃旧草稿。"""
    try:
        return service.create_batch(conn, (body.scope if body else "missing"))
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.get("/batches")
def list_batches(conn: Connection = Depends(get_db)) -> list[dict]:
    """最近整理批次（含草稿），用于「继续预览 / 回滚 / 审计」。"""
    return service.list_batches(conn)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int, conn: Connection = Depends(get_db)) -> dict:
    try:
        return service.get_batch(conn, batch_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


class ItemBody(BaseModel):
    path: str
    customer: str | None = None
    project_name: str | None = None
    status: str | None = None  # active | archived
    start_date: str | None = None  # YYYY-MM-DD
    skip: bool | None = None


class ItemsBody(BaseModel):
    items: list[ItemBody]


@router.put("/batches/{batch_id}/items")
def update_items(batch_id: int, body: ItemsBody, conn: Connection = Depends(get_db)) -> dict:
    """保存预览表修改（FR-CLEAN-03）；仅草稿批次可改。"""
    try:
        return service.update_items(conn, batch_id, [i.model_dump() for i in body.items])
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


class ExecuteBody(BaseModel):
    # 预览表的最终值随执行一起提交：用户改完直接点写入，不必先单独保存
    items: list[ItemBody] | None = None


@router.post("/batches/{batch_id}/execute")
def execute_batch(
    batch_id: int, body: ExecuteBody | None = None, conn: Connection = Depends(get_db)
) -> dict:
    """执行写入：自动备份 + 只改头部字段（FR-CLEAN-04/05）。"""
    try:
        overrides = [i.model_dump() for i in body.items] if body and body.items is not None else None
        return service.execute(conn, batch_id, overrides)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


class RollbackBody(BaseModel):
    force: bool = False  # 整理后被外部编辑过的笔记：默认跳过，force 才强写


@router.post("/batches/{batch_id}/rollback")
def rollback_batch(
    batch_id: int, body: RollbackBody | None = None, conn: Connection = Depends(get_db)
) -> dict:
    """一键回滚：按执行前快照还原（FR-CLEAN-05）。"""
    try:
        return service.rollback(conn, batch_id, bool(body and body.force))
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.delete("/batches")
def clear_batches(keep_draft: bool = True, conn: Connection = Depends(get_db)) -> dict:
    """清空整理记录（默认保留正在编辑的草稿）。"""
    try:
        return {"deleted": service.clear_batches(conn, keep_draft)}
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.delete("/batches/{batch_id}")
def delete_batch(batch_id: int, force: bool = False, conn: Connection = Depends(get_db)) -> dict:
    """删除一条整理记录；草稿直接删，已执行 / 已回滚需 force=true 二次确认。"""
    try:
        info = service.delete_batch(conn, batch_id, allow_finished=force)
        return {"deleted": True, **info}
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
