"""M2 售后笔记读写测试：扫描缓存 / 新建 / 冲突三选 / 状态变更 / 路径越界。"""

import shutil
from datetime import date
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(monkeypatch):
    """临时 vault + 配置注入；测试结束恢复配置。"""
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_vault_"))
    notes_dir = tmp / "客户售后问题"
    notes_dir.mkdir(parents=True)
    # 预置一份存量笔记（含完整头部；字节写入避免 Windows 换行翻译）
    (notes_dir / "星河纸业-复卷机张力控制异常.md").write_bytes(
        ("---\nname: 星河纸业\nproject: 复卷机张力控制异常\nstatus: 进行中\n"
         "start_date: 2026-10-28\nupdated: 2026-11-24\n---\n\n# 复卷机张力控制异常\n\n正文。\n").encode("utf-8")
    )
    # 预置一份缺头部字段的笔记（head_ok=0）
    (notes_dir / "无头笔记.md").write_bytes("# 只有正文\n".encode("utf-8"))

    client = TestClient(create_app())
    client.__enter__()

    client.put("/api/config", json={"paths": {"vault_root": str(tmp), "notes_folder": "客户售后问题"}})
    yield client, notes_dir

    client.__exit__(None, None, None)
    client.put("/api/config", json={"paths": {"vault_root": "", "notes_folder": "客户售后问题"}})
    shutil.rmtree(tmp, ignore_errors=True)


def test_list_notes_scans_vault(client):
    c, _ = client
    resp = c.get("/api/notes")
    assert resp.status_code == 200
    notes = {n["path"]: n for n in resp.json()}
    assert "星河纸业-复卷机张力控制异常.md" in notes
    assert notes["星河纸业-复卷机张力控制异常.md"]["customer"] == "星河纸业"
    assert notes["星河纸业-复卷机张力控制异常.md"]["status"] == "active"
    assert notes["星河纸业-复卷机张力控制异常.md"]["head_ok"] == 1
    assert notes["无头笔记.md"]["head_ok"] == 0
    assert "name" in notes["无头笔记.md"] or notes["无头笔记.md"]["customer"] is None


def test_create_note_template_and_conflict(client):
    c, notes_dir = client
    resp = c.post("/api/notes", json={"customer": "华瑞包装", "project_name": "涂布机伺服报警E-07"})
    assert resp.status_code == 201
    row = resp.json()
    assert row["path"] == "华瑞包装-涂布机伺服报警E-07.md"
    assert row["customer"] == "华瑞包装"
    assert row["status"] == "active"
    # FR-NOTE-10：模板带固定头部 + 日期小节
    text = (notes_dir / "华瑞包装-涂布机伺服报警E-07.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: 华瑞包装\nproject: 涂布机伺服报警E-07\nstatus: 进行中\n")
    assert "## " in text and "**现象**：" in text
    # 重名 409 path_conflict
    dup = c.post("/api/notes", json={"customer": "华瑞包装", "project_name": "涂布机伺服报警E-07"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "path_conflict"


def test_save_and_conflict_three_choices(client):
    c, notes_dir = client
    info = c.get("/api/notes/星河纸业-复卷机张力控制异常.md").json()
    base_hash = info["content_hash"]

    # 1) 正常保存（base_hash 一致）
    new_content = info["content"].replace("正文。", "正文已更新。")
    resp = c.put("/api/notes/星河纸业-复卷机张力控制异常.md", json={"content": new_content, "base_hash": base_hash})
    assert resp.status_code == 200
    assert resp.json()["saved"] is True
    on_disk = (notes_dir / "星河纸业-复卷机张力控制异常.md").read_text(encoding="utf-8")
    assert "正文已更新。" in on_disk

    # 2) 外部修改后再保存（旧 hash）→ 409 edit_conflict（FR-NOTE-05 禁止静默覆盖）
    (notes_dir / "星河纸业-复卷机张力控制异常.md").write_bytes("---\nname: 外部\nproject: 外部改\nstatus: 进行中\nstart_date: 2026-01-01\nupdated: 2026-11-25\n---\n外部改动\n".encode("utf-8"))
    conflict = c.put("/api/notes/星河纸业-复卷机张力控制异常.md", json={"content": new_content, "base_hash": base_hash})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "edit_conflict"

    # 3) ?force=true = 用我的覆盖
    forced = c.put("/api/notes/星河纸业-复卷机张力控制异常.md?force=true", json={"content": new_content, "base_hash": base_hash})
    assert forced.status_code == 200

    # 4) ?reload=true = 重新加载，返回服务器当前内容
    reloaded = c.put("/api/notes/星河纸业-复卷机张力控制异常.md?reload=true", json={"content": "x"})
    assert reloaded.status_code == 200
    # 保存时 frontmatter 的 updated 字段自动刷新为当天（FR-NOTE-03 最近更新语义）
    assert "正文已更新。" in reloaded.json()["content"]
    assert f"updated: {date.today().isoformat()}" in reloaded.json()["content"]

    # 5) 另存副本：不动原文件，冲突时自动加后缀
    sa = c.post("/api/notes/星河纸业-复卷机张力控制异常.md/save-as", json={"content": new_content})
    assert sa.status_code == 201
    assert sa.json()["path"].endswith("-副本.md") or sa.json()["path"] != "星河纸业-复卷机张力控制异常.md"
    assert (notes_dir / "星河纸业-复卷机张力控制异常.md").exists()


def test_project_status_patch(client):
    c, notes_dir = client
    resp = c.patch("/api/projects/星河纸业-复卷机张力控制异常.md", json={"status": "archived"})
    assert resp.status_code == 200
    text = (notes_dir / "星河纸业-复卷机张力控制异常.md").read_text(encoding="utf-8")
    assert "status: 已归档" in text
    assert "正文" in text  # FR-NOTE-09 只改头部不碰正文

    # 项目列表聚合
    projects = c.get("/api/projects").json()
    target = [p for p in projects if p["project_name"] == "复卷机张力控制异常"]
    assert target and target[0]["status"] == "archived" and target[0]["customer"] == "星河纸业"

    bad = c.patch("/api/projects/星河纸业-复卷机张力控制异常.md", json={"status": "x"})
    assert bad.status_code == 422


def test_path_outside_root(client):
    c, _ = client
    # 服务层直测：越界路径必须被拒绝（api-rules 第 6 节）
    import pytest as _pytest

    from app.services import vault as _vault

    conn = _vault.config_service.get_raw  # noqa: F841 —— 仅确认模块可用
    from app.db.database import connect as _connect

    conn = _connect()
    try:
        with _pytest.raises(_vault.PathOutsideRoot):
            _vault.safe_rel_path(conn, "../secret.md")
        with _pytest.raises(_vault.PathOutsideRoot):
            _vault.safe_rel_path(conn, "a/../../secret.md")
    finally:
        conn.close()
    # API 层：另存副本携带越界建议路径 → 400
    resp = c.post(
        "/api/notes/星河纸业-复卷机张力控制异常.md/save-as",
        json={"content": "x", "suggested_path": "../evil.md"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "path_outside_root"


def test_history_endpoint(client):
    c, notes_dir = client
    c.get("/api/notes")  # 触发重扫，建立缓存基线
    resp = c.get("/api/notes/星河纸业-复卷机张力控制异常.md/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["externally_modified"] is False
    # 外部修改后检测为 True
    (notes_dir / "星河纸业-复卷机张力控制异常.md").write_text("changed", encoding="utf-8")
    resp2 = c.get("/api/notes/星河纸业-复卷机张力控制异常.md/history")
    assert resp2.json()["externally_modified"] is True
