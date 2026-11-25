"""M1 看板测试：summary 指标口径 / trend 三档 / follow-ups / 缓存指纹失效。"""

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import dashboard_service


@pytest.fixture()
def client(monkeypatch):
    """临时 vault + 动态日期笔记（相对今天偏移，保证指标稳定）。"""
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_dash_"))
    notes_dir = tmp / "客户售后问题"
    notes_dir.mkdir(parents=True)
    today = date.today()

    def note(name: str, customer: str, project: str, status: str, start: date, updated: date) -> None:
        (notes_dir / name).write_bytes(
            (
                f"---\nname: {customer}\nproject: {project}\nstatus: {status}\n"
                f"start_date: {start.isoformat()}\nupdated: {updated.isoformat()}\n---\n\n正文\n"
            ).encode("utf-8")
        )

    # 进行中 · 10 天未更新 → 待跟进（阈值默认 7 天）
    note("甲客户-贴标机报警.md", "甲客户", "贴标机报警", "进行中",
         today - timedelta(days=20), today - timedelta(days=10))
    # 进行中 · 3 天前更新 → 非待跟进；start 在本月 → 本月新增
    note("甲客户-输送线改造.md", "甲客户", "输送线改造", "进行中",
         today - timedelta(days=3), today - timedelta(days=1))
    # 已归档 · start 在上月 → 不计入本月新增
    note("乙客户-封箱机异常.md", "乙客户", "封箱机异常", "已归档",
         today - timedelta(days=40), today - timedelta(days=5))

    client = TestClient(create_app())
    client.__enter__()
    client.put("/api/config", json={"paths": {"vault_root": str(tmp), "notes_folder": "客户售后问题"}})
    yield client, notes_dir
    client.__exit__(None, None, None)
    client.put("/api/config", json={"paths": {"vault_root": "", "notes_folder": "客户售后问题"}})
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_cache():
    """进程内缓存按指纹失效，但测试间强制重算保证隔离。"""
    dashboard_service._cache["fingerprint"] = None
    yield
    dashboard_service._cache["fingerprint"] = None


def test_summary_metrics(client):
    c, _ = client
    resp = c.get("/api/dashboard/summary")
    assert resp.status_code == 200
    s = resp.json()
    assert s["active_count"] == 2
    assert s["archived_count"] == 1
    assert s["follow_up_count"] == 1
    assert s["month_new_count"] == 2  # 20 天前与 3 天前两条 start 在本月内
    # 平均处理天数：(10) + (2) + (35) = 47/3 ≈ 16
    assert s["avg_handle_days"] == round((10 + 2 + 35) / 3)
    top = {x["customer"]: x for x in s["customer_top"]}
    assert top["甲客户"]["total"] == 2 and top["甲客户"]["open"] == 2
    assert top["乙客户"]["total"] == 1 and top["乙客户"]["open"] == 0


def test_missing_head_note_still_counted(client):
    """缺头笔记不排除：扫描层已做字段推断（状态←默认进行中、日期←mtime），
    看板统计以 note_file 为准（用户真实 vault 适配）。"""
    c, notes_dir = client
    (notes_dir / "20260825.md").write_bytes(
        "---\nname: 推断客户\ntime: 2026-08-25-周二 23:05\n---\n\n正文\n".encode("utf-8")
    )
    c.get("/api/notes")  # 触发重扫
    s = c.get("/api/dashboard/summary").json()
    assert s["active_count"] == 3  # 无 status 头部 → 默认进行中
    rows = {n["path"]: n for n in c.get("/api/notes").json()}
    assert rows["20260825.md"]["start_date"] == "2026-08-25"  # ← 头部 time 字段推断
    assert rows["20260825.md"]["project_name"] == "20260825"  # ← 文件名回退


def test_follow_ups_sorted_and_days(client):
    c, _ = client
    resp = c.get("/api/dashboard/follow-ups")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["path"] == "甲客户-贴标机报警.md"
    assert items[0]["days"] == 10
    assert items[0]["customer"] == "甲客户"


def test_trend_three_ranges(client):
    c, _ = client
    today = date.today()
    for range_, label_fmt in (("month", None), ("30d", None), ("6m", None)):
        resp = c.get(f"/api/dashboard/trend?range={range_}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["range"] == range_
        series = body["series"]
        assert all("bucket" in x and "count" in x for x in series)
        if range_ == "6m":
            assert len(series) == 6  # 近 6 月按月
        else:
            assert 4 <= len(series) <= 6  # 本月按周 / 近 5 周按周
        total = sum(x["count"] for x in series)
        if range_ == "month":
            # 本月内 start 的有 2 条（20 天前 / 3 天前）——跨月边界周可能覆盖到，宽松断言
            assert total >= 2
    # month 桶标签形如 11-03~11-09
    m = c.get("/api/dashboard/trend?range=month").json()["series"][0]["bucket"]
    assert "~" in m and len(m.split("~")[0]) == 5


def test_cache_fingerprint_invalidation(client):
    """新增笔记重扫后指纹变化 → summary 重算；未变时命中缓存。"""
    c, notes_dir = client
    first = c.get("/api/dashboard/summary").json()
    fp1 = dashboard_service._cache["fingerprint"]
    second = c.get("/api/dashboard/summary").json()
    assert dashboard_service._cache["fingerprint"] == fp1  # 指纹未变，复用
    assert second == first

    (notes_dir / "丙客户-码垛机故障.md").write_bytes(
        ("---\nname: 丙客户\nproject: 码垛机故障\nstatus: 进行中\n"
         f"start_date: {date.today().isoformat()}\nupdated: {date.today().isoformat()}\n---\n\n正文\n").encode("utf-8")
    )
    c.get("/api/notes")  # 触发重扫（scanned_at 变化）
    third = c.get("/api/dashboard/summary").json()
    assert third["active_count"] == 3


def test_follow_up_days_config_effect(client):
    """FR-DASH-06：阈值可配置（follow_up_days=15 时 10 天未更新的不再待跟进）。"""
    c, _ = client
    c.put("/api/config", json={"dashboard": {"follow_up_days": 15}})
    s = c.get("/api/dashboard/summary").json()
    assert s["follow_up_count"] == 0
    assert s["follow_up_days"] == 15
    c.put("/api/config", json={"dashboard": {"follow_up_days": 7}})


def test_external_kb_note_excluded_from_projects(client):
    """外部知识库（绝对路径主键）笔记不进首页项目列表（FR-HOME-04 只呈现售后项目）。

    回归：2026-11-26 实测首页项目列表比看板多 1 个（Python 学习笔记被当成售后项目）。
    """
    c, notes_dir = client
    ext = notes_dir.parent / "外部知识库"
    ext.mkdir()
    (ext / "Python学习.md").write_bytes(
        "---\nname: 外部\nproject: Python学习\nstatus: 进行中\n---\n\n正文\n".encode("utf-8")
    )
    assert c.put("/api/kb/whitelist", json={"folders": [str(ext)]}).status_code == 200

    names = {p["project_name"] for p in c.get("/api/projects?status=active").json()}
    assert "Python学习" not in names
    assert names == {"贴标机报警", "输送线改造"}  # 只保留售后笔记
    # 看板口径同样不受外部笔记影响
    s = c.get("/api/dashboard/summary").json()
    assert s["active_count"] == 2
    assert s["total_count"] == 3


def test_summary_new_fields(client):
    """首页展示所需增量字段：合计记录数与最近扫描时间。"""
    c, _ = client
    s = c.get("/api/dashboard/summary").json()
    assert s["total_count"] == 3
    assert s["scanned_at"] and "T" in s["scanned_at"]
    t = c.get("/api/dashboard/trend?range=6m").json()
    assert t["total"] == sum(x["count"] for x in t["series"])
