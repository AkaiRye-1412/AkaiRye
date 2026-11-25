"""看板（04 文档 3.3：/api/dashboard/*，M1）。数据全部由 note_file 计算（FR-DASH-07）。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import dashboard_service, vault

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(conn: Connection = Depends(get_db)) -> dict:
    """指标卡汇总：进行中/已归档/待跟进/本月新增/平均处理天数/客户分布 Top N。"""
    vault.rescan_all(conn)
    return dashboard_service.summary(conn)


@router.get("/trend")
def trend(
    range: str = Query("month", pattern="^(month|30d|6m)$"),
    conn: Connection = Depends(get_db),
) -> dict:
    """FR-DASH-05 三档（Q-06）；聚合粒度：month/30d 按周、6m 按月（原型口径）。"""
    vault.rescan_all(conn)
    return dashboard_service.trend(conn, range)


@router.get("/follow-ups")
def follow_ups(conn: Connection = Depends(get_db)) -> list[dict]:
    """待跟进项目明细：按 updated_date 升序，附最近更新路径与客户。"""
    vault.rescan_all(conn)
    return dashboard_service.follow_ups(conn)
