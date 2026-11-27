"""M7 存量整理：扫描推断 / 预览编辑 / 备份写入 / 回滚（FR-CLEAN-01~06）。"""

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(monkeypatch):
    """临时 vault：完整笔记 / 缺 status 的存量卡 / 无头部文件 / 客户-项目命名。"""
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_cleanup_"))
    notes = tmp / "客户售后问题"
    notes.mkdir(parents=True)

    # 1) 真实形态的存量售后卡：有 name/project/time，缺 status/start_date/updated
    (notes / "20260818.md").write_bytes(
        (
            "---\nname: 长峰机电210\ntime: 2026-08-18-Tue 19:17\n"
            "project: 军工\nProperty: 售后卡\n---\n\n# mphase变化\n\n## 现象\n\n换相角度一直变。\n"
        ).encode("utf-8")
    )
    # 2) 头部完整：不应进 missing 候选
    (notes / "星河纸业-复卷机.md").write_bytes(
        (
            "---\nname: 星河纸业\nproject: 复卷机\nstatus: 进行中\n"
            "start_date: 2026-10-28\nupdated: 2026-11-24\n---\n\n# 复卷机\n\n正文。\n"
        ).encode("utf-8")
    )
    # 3) 只有正文：客户/项目都无法识别 → 必须补全或跳过
    (notes / "无头笔记.md").write_bytes("# 20260901\n\n正文内容。\n".encode("utf-8"))
    # 4) 缺 project，靠文件名推断（客户-项目 命名）
    (notes / "卓宏-机床.md").write_bytes(
        "---\nname: 卓宏\ntime: 2026-09-01-Tue 10:00\n---\n\n# 机床抖动\n\n正文。\n".encode("utf-8")
    )

    client = TestClient(create_app())
    client.__enter__()
    client.put("/api/config", json={"paths": {"vault_root": str(tmp), "notes_folder": "客户售后问题"}})
    yield client, notes

    client.__exit__(None, None, None)
    client.put("/api/config", json={"paths": {"vault_root": "", "notes_folder": "客户售后问题"}})
    shutil.rmtree(tmp, ignore_errors=True)


def _items(client) -> dict:
    batch = client.post("/api/cleanup/batches", json={"scope": "missing"}).json()
    return {i["path"]: i for i in batch["items"]}, batch


def test_summary_and_scan_inference(client):
    c, notes = client
    s = c.get("/api/cleanup/summary").json()
    assert s["notes_total"] == 4
    assert s["head_missing"] == 3  # 完整的那篇不进候选
    assert "星河纸业" in s["customers"]
    assert s["notes_folder"] == "客户售后问题"

    items, batch = _items(c)
    assert batch["status"] == "draft"
    assert set(items) == {"20260818.md", "无头笔记.md", "卓宏-机床.md"}

    card = items["20260818.md"]
    assert card["customer"] == "长峰机电210"
    assert card["project_name"] == "军工"
    assert card["status"] == "archived"  # FR-CLEAN-06 默认已归档
    assert card["start_date"] == "2026-08-18"  # 文件名日期
    assert card["sources"]["start_date"] == "文件名日期"
    assert set(card["missing"]) == {"status", "start_date", "updated"}
    assert set(card["changes"]) == {"status", "start_date", "updated"}

    named = items["卓宏-机床.md"]
    assert named["project_name"] == "机床"
    assert named["sources"]["project"] == "文件名"

    blank = items["无头笔记.md"]
    assert blank["customer"] == ""  # 无法从文件名/头部认出客户：留空等用户补
    assert blank["sources"]["customer"] == "未识别"
    assert blank["project_name"] == "无头笔记"  # 无从推断时退回文件名，至少不是空值
    assert blank["sources"]["project"] == "文件名"


def test_execute_only_touches_header_and_backs_up(client):
    c, notes = client
    items, batch = _items(c)
    path = notes / "20260818.md"
    before = path.read_bytes()
    before_body = before.decode("utf-8").split("---\n", 2)[2]

    resp = c.post(
        f"/api/cleanup/batches/{batch['id']}/execute",
        json={"items": [items["20260818.md"], {**items["无头笔记.md"], "skip": True}]},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["batch"]["status"] == "done"
    assert result["batch"]["applied"] == 1
    assert result["batch"]["skipped"] == 1
    assert result["batch"]["failed"] == 0

    text = path.read_text(encoding="utf-8")
    assert "status: 已归档" in text
    assert "start_date: 2026-08-18" in text
    assert "updated: 2026-08-18" in text  # 缺失时按开始日期补，不刷成今天
    assert text.split("---\n", 2)[2] == before_body  # 正文逐字未动

    backup = Path(result["batch"]["backup_path"])
    assert backup.is_file() and backup.suffix == ".zip"
    assert backup.name.startswith(f"cleanup_{batch['id']}_")
    import zipfile

    with zipfile.ZipFile(backup) as zf:
        assert zf.read("20260818.md") == before

    # 笔记缓存同步刷新：head_ok 由 0 变 1
    rows = {r["path"]: r for r in c.get("/api/notes").json()}
    assert rows["20260818.md"]["head_ok"] == 1
    assert rows["20260818.md"]["status"] == "archived"


def test_rollback_restores_bytes(client):
    c, notes = client
    items, batch = _items(c)
    path = notes / "20260818.md"
    original = path.read_bytes()

    assert c.post(f"/api/cleanup/batches/{batch['id']}/execute", json={"items": [{**i, "skip": not i["customer"] or not i["project_name"]} for i in items.values()]}).status_code == 200
    assert path.read_bytes() != original

    resp = c.post(f"/api/cleanup/batches/{batch['id']}/rollback", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["restored"] >= 1
    assert body["batch"]["status"] == "rolled_back"
    assert path.read_bytes() == original  # 字节级还原
    assert body["batch"]["applied"] == 0


def test_unresolved_blocks_execute(client):
    c, _notes = client
    items, batch = _items(c)
    resp = c.post(f"/api/cleanup/batches/{batch['id']}/execute", json={"items": list(items.values())})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "cleanup_unresolved"

    # 跳过未识别后可以执行
    payload = [{**items["无头笔记.md"], "skip": True}, items["20260818.md"]]
    resp = c.post(f"/api/cleanup/batches/{batch['id']}/execute", json={"items": payload})
    assert resp.status_code == 200
    assert resp.json()["batch"]["skipped"] == 1


def test_draft_replaced_but_executed_batch_is_kept(client):
    c, _notes = client
    items1, first = _items(c)
    # 在草稿上改一个值：重新扫描后应当回到磁盘推断值（旧草稿被丢弃）
    c.put(
        f"/api/cleanup/batches/{first['id']}/items",
        json={"items": [{**items1["无头笔记.md"], "customer": "临时客户"}]},
    )
    assert c.get(f"/api/cleanup/batches/{first['id']}").json()["items"][-1]["customer"] == "临时客户"

    items2, second = _items(c)
    assert second["scope"] == "missing"
    assert items2["无头笔记.md"]["customer"] == ""
    assert len(c.get("/api/cleanup/batches").json()) == 1  # 只剩新草稿

    items = items2
    c.post(
        f"/api/cleanup/batches/{second['id']}/execute",
        json={"items": [items["20260818.md"], {**items["无头笔记.md"], "skip": True}]},
    )
    # 已执行批次是回滚依据，不允许删除/覆盖
    assert c.delete(f"/api/cleanup/batches/{second['id']}").status_code == 409
    assert c.post(f"/api/cleanup/batches/{second['id']}/execute", json={}).status_code == 409


def test_rollback_skips_external_edit(client):
    c, notes = client
    items, batch = _items(c)
    path = notes / "20260818.md"
    c.post(
        f"/api/cleanup/batches/{batch['id']}/execute",
        json={"items": [items["20260818.md"], {**items["无头笔记.md"], "skip": True}]},
    )

    # 整理后用户在外部编辑器又改了一行：回滚不能默默覆盖
    edited = path.read_text(encoding="utf-8") + "\n补记一句。\n"
    path.write_bytes(edited.encode("utf-8"))

    resp = c.post(f"/api/cleanup/batches/{batch['id']}/rollback", json={})
    assert resp.status_code == 409
    assert "外部修改" in resp.json()["error"]["message"]
    assert path.read_text(encoding="utf-8") == edited

    forced = c.post(f"/api/cleanup/batches/{batch['id']}/rollback", json={"force": True})
    assert forced.status_code == 200
    assert forced.json()["restored"] == 1


def test_delete_finished_record_keeps_notes(client):
    """删除整理记录：只删记录，笔记保持整理后的样子（删除 ≠ 还原）。"""
    c, notes = client
    items, batch = _items(c)
    c.post(
        f"/api/cleanup/batches/{batch['id']}/execute",
        json={"items": [items["20260818.md"], {**items["无头笔记.md"], "skip": True}]},
    )
    written = (notes / "20260818.md").read_bytes()
    assert b"status:" in written

    # 不给 force：已执行批次仍是回滚依据，409 且记录还在
    assert c.delete(f"/api/cleanup/batches/{batch['id']}").status_code == 409
    assert c.get(f"/api/cleanup/batches/{batch['id']}").status_code == 200

    # 显式 force：记录与明细一起消失，笔记原样保留
    resp = c.delete(f"/api/cleanup/batches/{batch['id']}?force=true")
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert c.get(f"/api/cleanup/batches/{batch['id']}").status_code == 404
    assert c.get("/api/cleanup/batches").json() == []
    assert (notes / "20260818.md").read_bytes() == written


def test_clear_batches_keeps_draft(client):
    """清空记录默认保留正在编辑的草稿；keep_draft=false 才一起清。"""
    c, notes = client
    items, first = _items(c)
    c.post(
        f"/api/cleanup/batches/{first['id']}/execute",
        json={"items": [items["20260818.md"], {**items["无头笔记.md"], "skip": True}]},
    )
    _, draft = _items(c)
    assert len(c.get("/api/cleanup/batches").json()) == 2

    assert c.delete("/api/cleanup/batches").json()["deleted"] == 1
    rest = c.get("/api/cleanup/batches").json()
    assert [b["id"] for b in rest] == [draft["id"]]
    assert rest[0]["status"] == "draft"

    assert c.delete("/api/cleanup/batches?keep_draft=false").json()["deleted"] == 1
    assert c.get("/api/cleanup/batches").json() == []
