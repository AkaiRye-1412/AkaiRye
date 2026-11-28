"""看板服务（M1 · FR-DASH）：指标全部由 note_file 扫描计算，进程内缓存（NFR-PERF-04）。

依据：
- 04 文档 3.3：/api/dashboard/summary、trend、follow-ups；
- FR-DASH-01 进度（进行中/已归档/待跟进）、FR-DASH-02 趋势（按周/月）、FR-DASH-03 客户分布 Top N、
  FR-DASH-04 时效（超阈值项目/平均处理天数）、FR-DASH-05 三档（month/30d/6m，Q-06）、
  FR-DASH-06 待跟进规则（active 且最近更新超 follow_up_days，阈值可配置）、FR-DASH-07 数据来源；
- 缓存策略：以 note_file 数据指纹（行数 + 最新 scanned_at）+ follow_up_days 组成 key，
  指纹不变直接复用上次结果；watchdog 重扫后 scanned_at 变化自然失效，无需事件订阅。
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CUSTOMER_TOP_N = 5  # FR-DASH-03 Top N（FR-CFG 未定义可配项，固定 5）

# 进程内缓存（单进程 uvicorn；锁保护并发首算）
_cache_lock = threading.Lock()
_cache: dict = {"fingerprint": None, "summary": None, "trend": {}}


def _fingerprint(conn, follow_up_days: int) -> tuple:
    row = conn.execute(
        "SELECT COUNT(*), IFNULL(MAX(scanned_at), ''), IFNULL(MAX(file_mtime), 0) FROM note_file"
    ).fetchone()
    return (row[0], row[1], row[2], follow_up_days)


def _get_or_compute(conn):
    """取缓存或全量重算（summary + 各档 trend 一并缓存）。"""
    dashboard_cfg = conn.execute(
        "SELECT value_json FROM config WHERE key = 'dashboard'"
    ).fetchone()
    follow_up_days = 7
    default_range = "month"
    if dashboard_cfg is not None:
        import json

        cfg = json.loads(dashboard_cfg[0])
        follow_up_days = int(cfg.get("follow_up_days", 7))
        default_range = cfg.get("default_range", "month")

    fp = _fingerprint(conn, follow_up_days)
    with _cache_lock:
        if _cache["fingerprint"] == fp:
            return _cache, default_range
        rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM note_file").fetchall()
            # 外部知识库白名单目录（绝对路径主键）不参与售后看板统计
            if not Path(r["path"]).is_absolute()
        ]
        summary = _compute_summary(rows, follow_up_days)
        trend = {r: _compute_trend(rows, r, today=date.today()) for r in ("month", "30d", "6m")}
        _cache["fingerprint"] = fp
        _cache["summary"] = summary
        _cache["trend"] = trend
        return _cache, default_range


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _compute_summary(rows: list[dict], follow_up_days: int) -> dict:
    today = date.today()
    active = archived = month_new = 0
    scanned_at = ""
    follow_ups: list[dict] = []
    handle_days: list[int] = []
    customer_stat: dict[str, dict] = {}
    month_prefix = today.strftime("%Y-%m")

    for r in rows:
        # 统计口径以 note_file 为准（扫描层对缺头笔记已做字段推断：日期←文件名/mtime、
        # 状态←默认进行中、项目名←文件名）；head_ok 仅作为存量整理信号保留。
        if r["scanned_at"] and r["scanned_at"] > scanned_at:
            scanned_at = r["scanned_at"]  # 本次扫描时间（页脚"扫描于"展示用）
        status = r["status"]
        customer = r["customer"] or "（未知客户）"
        stat = customer_stat.setdefault(
            customer,
            {"customer": customer, "total": 0, "open": 0, "last_updated": ""},
        )
        stat["total"] += 1
        # 同数量的客户按「最近更新」排队，新建的客户不会因为名称顺序沉到列表末尾
        if (r["updated_date"] or "") > stat["last_updated"]:
            stat["last_updated"] = r["updated_date"] or ""
        if status == "active":
            active += 1
            stat["open"] += 1
        elif status == "archived":
            archived += 1

        start = _parse_date(r["start_date"])
        updated = _parse_date(r["updated_date"])
        if start is not None and start.strftime("%Y-%m") == month_prefix:
            month_new += 1
        if start is not None and updated is not None and updated >= start:
            handle_days.append((updated - start).days)

        # FR-DASH-06：进行中且最近更新超过阈值（updated 距今天数 > follow_up_days）
        if status == "active" and updated is not None:
            days = (today - updated).days
            if days > follow_up_days:
                follow_ups.append(
                    {
                        "path": r["path"],
                        "customer": r["customer"],
                        # 卡片式售后记录没有 project 字段，用正文 H1 作展示名（避免只显示日期）
                        "project_name": (
                            (r["note_title"] or r["project_name"])
                            if r.get("note_mode") == "card"
                            else r["project_name"]
                        ),
                        "updated_date": r["updated_date"],
                        "days": days,
                    }
                )

    follow_ups.sort(key=lambda x: (x["updated_date"] or ""))  # 04 文档：按 updated_date 升序
    # FR-DASH-03：customer_top 保持 Top N（对外兼容），customer_all 给「客户分布」全量展示——
    # 只看 Top 5 时，新加的小客户（通常只有 1 条）会被同数量的老客户按名称顺序挤掉，
    # 表现为「新建了客户但分布里不出现」。
    customer_all = sorted(customer_stat.values(), key=lambda s: s["customer"])
    customer_all.sort(key=lambda s: s["last_updated"], reverse=True)
    customer_all.sort(key=lambda s: -s["total"])  # 主页记录数降序；同数量按最近更新、再按名称
    customer_top = customer_all[:CUSTOMER_TOP_N]
    return {
        "active_count": active,
        "archived_count": archived,
        "total_count": len(rows),  # 合计记录（首页"合计记录"指标卡 / 页脚"由 N 篇扫描生成"）
        "follow_up_count": len(follow_ups),
        "month_new_count": month_new,
        "avg_handle_days": round(sum(handle_days) / len(handle_days)) if handle_days else None,
        "customer_top": customer_top,
        "customer_all": customer_all,
        "customer_count": len(customer_all),
        "follow_up_days": follow_up_days,
        "scanned_at": scanned_at or None,
        "follow_ups_detail": follow_ups,  # 内部复用；API 层单独输出
    }


def _week_start(d: date) -> date:
    """周一为一周起点（原型 trendFor 同口径）。"""
    return d - timedelta(days=d.weekday())


def _compute_trend(rows: list[dict], range_: str, today: date) -> dict:
    """FR-DASH-02/05：month 本月按周；30d 近 5 周按周；6m 近 6 月按月（原型口径）。

    新增记录口径：按头部 start_date 落桶（"新增售后记录" = 该记录开始处理的日期）。
    """
    points = [(_parse_date(r["start_date"]),) for r in rows]
    starts = [p[0] for p in points if p[0] is not None]

    buckets: list[tuple[date, date]] = []
    if range_ == "month":
        first = today.replace(day=1)
        cursor = _week_start(first)
        while cursor <= today:
            buckets.append((cursor, min(cursor + timedelta(days=6), today)))
            cursor += timedelta(days=7)
    elif range_ == "30d":
        cursor = _week_start(today - timedelta(days=34))  # 近 5 周（原型口径）
        while cursor <= today:
            buckets.append((cursor, min(cursor + timedelta(days=6), today)))
            cursor += timedelta(days=7)
    elif range_ == "6m":
        y, m = today.year, today.month
        months = []
        for _ in range(6):
            months.append((y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        months.reverse()
        for (yy, mm) in months:
            first = date(yy, mm, 1)
            nxt = date(yy + (mm == 12), (mm % 12) + 1, 1)
            buckets.append((first, nxt - timedelta(days=1)))
    else:  # pragma: no cover — 路由层已校验
        raise ValueError(f"unknown range: {range_}")

    series = []
    for lo, hi in buckets:
        count = sum(1 for d in starts if lo <= d <= hi)
        if range_ == "6m":
            label = lo.strftime("%Y-%m")
        elif range_ == "month":
            label = f"{lo.strftime('%m-%d')}~{hi.strftime('%m-%d')}"
        else:
            label = f"{lo.strftime('%m-%d')}~{hi.strftime('%m-%d')}"
        series.append({"bucket": label, "count": count})
    return {"range": range_, "series": series, "total": sum(x["count"] for x in series)}


def summary(conn) -> dict:
    data, _ = _get_or_compute(conn)
    return data["summary"]


def trend(conn, range_: str) -> dict:
    data, _ = _get_or_compute(conn)
    return data["trend"][range_]


def follow_ups(conn) -> list[dict]:
    return summary(conn)["follow_ups_detail"]


def default_range(conn) -> str:
    _, dr = _get_or_compute(conn)
    return dr
