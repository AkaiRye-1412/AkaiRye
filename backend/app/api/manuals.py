"""手册与入库队列（04 文档 3.6/3.7：/api/manuals、/api/ingest，M3 队列管理）。TODO(业务实现)。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlite3 import Connection

from app.api.deps import get_db

router = APIRouter(prefix="/api", tags=["manuals"])


@router.get("/manuals")
def list_manuals(conn: Connection = Depends(get_db)) -> list[dict]:
    """状态 8 态（含 discovered，Q-15）。TODO(业务实现)：品牌/状态/关键词过滤。"""
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "手册列表：业务实现阶段提供"})


@router.get("/manuals/{manual_id}")
def manual_detail(manual_id: int, conn: Connection = Depends(get_db)) -> dict:
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "手册详情：业务实现阶段提供"})


@router.post("/manuals/ingest")
def ingest_manuals(conn: Connection = Depends(get_db)) -> dict:
    """M9 一键入库触发；重复提交返回既有任务（幂等）。TODO(业务实现)。"""
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "批量入库：业务实现阶段提供"})


@router.post("/manuals/{manual_id}/rebuild")
def rebuild(manual_id: int) -> dict:
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "重建：业务实现阶段提供"})


@router.post("/manuals/{manual_id}/relocate")
def relocate(manual_id: int) -> dict:
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "重新定位：业务实现阶段提供"})


@router.post("/manuals/{manual_id}/ignore-update")
def ignore_update(manual_id: int) -> dict:
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "忽略更新：业务实现阶段提供"})


@router.delete("/manuals/{manual_id}")
def delete_manual(manual_id: int, conn: Connection = Depends(get_db)) -> dict:
    """单事务删块三件套 + manual 行（03 场景 5）。TODO(业务实现)。"""
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "删除手册：业务实现阶段提供"})


@router.get("/ingest/tasks")
def list_tasks(conn: Connection = Depends(get_db)) -> list[dict]:
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "队列任务列表：业务实现阶段提供"})


@router.post("/ingest/tasks/{task_id}/retry")
def retry_task(task_id: int) -> dict:
    """仅 failed 可重试 → 否则 409 state_conflict。TODO(业务实现)。"""
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "重试任务：业务实现阶段提供"})


@router.post("/ingest/tasks/{task_id}/cancel")
def cancel_task(task_id: int) -> dict:
    raise HTTPException(status_code=501, detail={"code": "not_implemented", "message": "取消任务：业务实现阶段提供"})
