"""草稿箱（右侧 dock 速记）测试：增删改查 + 文件名跟随一级标题 + 与笔记/索引的隔离。"""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(monkeypatch):
    """临时 vault；草稿箱按口径落在 vault_root/随手笔记/草稿箱（memo_roots 未配置的回落）。"""
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_drafts_"))
    (tmp / "客户售后问题").mkdir(parents=True)

    client = TestClient(create_app())
    client.__enter__()

    client.put("/api/config", json={"paths": {"vault_root": str(tmp), "notes_folder": "客户售后问题"}})
    yield client, tmp

    client.__exit__(None, None, None)
    # 配置表是真实库：用例改过的位置必须清回去，否则会污染后续用例
    client.put(
        "/api/config",
        json={
            "paths": {
                "vault_root": "",
                "notes_folder": "客户售后问题",
                "memo_roots": [],
                "after_sales_roots": [],
                "draft_roots": [],
            }
        },
    )
    shutil.rmtree(tmp, ignore_errors=True)


def test_empty_draft_dir_lists_nothing(client):
    c, _ = client
    assert c.get("/api/drafts").json() == []


def test_create_save_read_delete(client):
    c, root = client
    draft_root = root / "随手笔记" / "草稿箱"
    today = datetime.now().strftime("%Y-%m-%d")

    created = c.post("/api/drafts", json={"content": "第一行是标题\n第二行是正文"})
    assert created.status_code == 201
    row = created.json()
    assert Path(row["path"]).is_file()
    assert Path(row["path"]).parent == draft_root.resolve()
    # 还没写一级标题 → 先用时间戳名字
    assert Path(row["path"]).stem.startswith("速记 ")
    assert row["title"] == "第一行是标题"
    assert row["preview"] == "第二行是正文"

    old_path = row["path"]
    saved = c.put("/api/drafts", json={"path": old_path, "content": "# 改过的标题\n\n新内容"})
    assert saved.status_code == 200
    assert saved.json()["title"] == "改过的标题"
    # 一级标题写上了 → 文件名跟着改，旧名字不留
    new_path = saved.json()["path"]
    assert Path(new_path).name == f"改过的标题{today}.md"
    assert not Path(old_path).exists()
    # 直接落盘验证（不是只改了内存/数据库）
    assert Path(new_path).read_text(encoding="utf-8") == "# 改过的标题\n\n新内容"

    listed = c.get("/api/drafts").json()
    assert [x["path"] for x in listed] == [new_path]

    read = c.get("/api/drafts/content", params={"path": new_path}).json()
    assert read["content"] == "# 改过的标题\n\n新内容"

    assert c.delete("/api/drafts", params={"path": new_path}).json()["deleted"] is True
    assert c.get("/api/drafts").json() == []
    assert not Path(new_path).exists()


def test_filename_follows_h1_heading(client):
    """文件名跟着一级标题走：每次保存都对齐，标题删掉后保持原名。"""
    c, root = client
    draft_root = root / "随手笔记" / "草稿箱"
    today = datetime.now().strftime("%Y-%m-%d")

    row = c.post("/api/drafts", json={"content": ""}).json()
    blank = Path(row["path"])
    assert blank.stem.startswith("速记 ")

    row = c.put("/api/drafts", json={"path": row["path"], "content": "# 本地部署\n\nollama"}).json()
    assert Path(row["path"]).name == f"本地部署{today}.md"
    assert not blank.exists()

    # 改一级标题 → 名字继续跟随，旧文件不残留
    first = Path(row["path"])
    row = c.put("/api/drafts", json={"path": row["path"], "content": "# 本地部署（改）\n\n正文"}).json()
    assert Path(row["path"]).name == f"本地部署（改）{today}.md"
    assert not first.exists()

    # 一级标题被删掉（只剩二级标题）：保持现在的名字，不退回时间戳
    kept = row["path"]
    row = c.put("/api/drafts", json={"path": kept, "content": "## 二级标题\n\n正文"}).json()
    assert row["path"] == kept
    assert len(list(draft_root.glob("*.md"))) == 1


def test_filename_sanitized_and_deduped(client):
    """非法字符换成空格；同一天同一标题会补时分，不会互相覆盖。"""
    c, root = client
    today = datetime.now().strftime("%Y-%m-%d")

    a = c.post("/api/drafts", json={"content": ""}).json()
    a2 = c.put("/api/drafts", json={"path": a["path"], "content": "# 复盘/总结: 第1版\n正文"}).json()
    assert Path(a2["path"]).name == f"复盘 总结 第1版{today}.md"

    b = c.post("/api/drafts", json={"content": ""}).json()
    b2 = c.put("/api/drafts", json={"path": b["path"], "content": "# 复盘/总结: 第1版\n另一篇"}).json()
    assert b2["path"] != a2["path"]
    assert Path(b2["path"]).name.startswith(f"复盘 总结 第1版{today} ")
    # 原来那篇没被覆盖
    assert Path(a2["path"]).read_text(encoding="utf-8") == "# 复盘/总结: 第1版\n正文"


def test_date_kept_from_existing_name(client):
    """跨天接着写：日期沿用文件名里原来的那一天，不因为今天保存就改名。"""
    c, root = client
    draft_root = root / "随手笔记" / "草稿箱"
    draft_root.mkdir(parents=True, exist_ok=True)
    old = draft_root / "速记 2020-01-01 09-00.md"
    old.write_bytes("# 本地部署\n正文".encode("utf-8"))

    row = c.put("/api/drafts", json={"path": str(old), "content": "# 本地部署\n正文"}).json()
    assert Path(row["path"]).name == "本地部署2020-01-01.md"
    assert not old.exists()



def test_drafts_stay_out_of_memos_and_notes(client):
    """速记不能混进个人笔记列表 / 售后笔记列表（草稿不是笔记）。"""
    c, root = client
    c.post("/api/memos", json={"title": "正式的个人笔记", "body": "正文", "folder": ""})
    c.post("/api/drafts", json={"content": "一张速记"})

    memos = c.get("/api/memos").json()
    assert [m["note_title"] for m in memos] == ["正式的个人笔记"]

    notes = c.get("/api/notes").json()
    assert all("草稿箱" not in n["path"] for n in notes)

    # 重新扫描也不能把速记扫成 note_file 行
    c.get("/api/memos")
    drafts_dir = root / "随手笔记" / "草稿箱"
    assert len(list(drafts_dir.glob("*.md"))) == 1


def test_reject_path_outside_draft_dir(client):
    c, root = client
    outside = root / "客户售后问题" / "越界.md"
    outside.write_bytes("x".encode("utf-8"))

    resp = c.put("/api/drafts", json={"path": str(outside), "content": "改掉"})
    assert resp.status_code == 400
    assert outside.read_text(encoding="utf-8") == "x"


def test_custom_draft_root_takes_effect(client):
    """设置页「草稿箱索引位置」：配了就用配置目录，默认目录不再收录。"""
    c, root = client
    custom = root / "我的速记"
    c.put("/api/config", json={"paths": {"draft_roots": [str(custom)]}})

    row = c.post("/api/drafts", json={"content": "自定义位置的速记"}).json()
    assert Path(row["path"]).parent == custom.resolve()
    assert [x["path"] for x in c.get("/api/drafts").json()] == [row["path"]]

    # 清空配置 → 回到默认口径（个人笔记第 1 个位置下的 草稿箱/）
    c.put("/api/config", json={"paths": {"draft_roots": []}})
    assert c.get("/api/drafts").json() == []
    back = c.post("/api/drafts", json={"content": "默认位置"}).json()
    assert Path(back["path"]).parent == (root / "随手笔记" / "草稿箱").resolve()


def test_draft_root_anywhere_stays_out_of_notes(client):
    """草稿目录放进个人笔记根里（甚至换个名字），也不能变成正式笔记。"""
    c, root = client
    memo_root = root / "随手笔记"
    custom = memo_root / "速记暂存"
    c.put("/api/config", json={"paths": {"memo_roots": [str(memo_root)], "draft_roots": [str(custom)]}})

    c.post("/api/memos", json={"title": "正式笔记", "body": "正文", "folder": ""})
    c.post("/api/drafts", json={"content": "速记不该进笔记列表"})

    assert [m["note_title"] for m in c.get("/api/memos").json()] == ["正式笔记"]
    assert c.get("/api/notes").json() == []
    # 知识库也不收
    kb = [n["path"] for n in c.get("/api/kb/notes").json()]
    assert all("速记暂存" not in p for p in kb)
