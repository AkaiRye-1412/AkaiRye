"""5 类索引位置（售后 / 随手 / PDF / 数据库 / 代码）：多位置配置真正生效。

每个位置最多 3 个，第 1 个为主位置（新建默认写入）。这里验证：
- 售后多个位置：主位置相对主键、其余绝对主键，都进售后列表；
- 随手多个位置：整根识别为 memo，不进售后列表；
- PDF 多个位置：一次扫描覆盖全部根，但**不作为知识库索引来源**；
- 数据库索引位置：其中的 Markdown 进知识库解析（note_type='kb'），不进售后列表；
- PDF 入库范围：只有「数据库索引位置」内的 PDF 能入库，手册目录里的只标「仅浏览」；
- 程序数据库文件位置：可保存、切换时自动复制现有库、提示重启（与资料目录无关）。
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import paths
from app.main import create_app


@pytest.fixture()
def client():
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_locs_"))
    notes_dir = tmp / "客户售后问题"
    notes_dir.mkdir(parents=True)
    (notes_dir / "主位置-项目.md").write_bytes(
        "---\nname: 主位置\nproject: 项目\n---\n\n# 项目\n\n正文\n".encode("utf-8")
    )
    c = TestClient(create_app())
    c.__enter__()
    c.put("/api/config", json={"paths": {"vault_root": str(tmp), "notes_folder": "客户售后问题"}})
    yield c, tmp, notes_dir
    c.__exit__(None, None, None)


def test_multi_after_sales_roots(client):
    """售后笔记最多 3 个位置：主位置用相对主键，其余位置用绝对主键，都进售后列表。"""
    c, tmp, notes_dir = client
    extra = tmp / "售后二区"
    extra.mkdir()
    (extra / "二区-项目B.md").write_bytes(
        "---\nname: 二区\nproject: 项目B\n---\n\n# 项目B\n\n正文\n".encode("utf-8")
    )

    resp = c.put(
        "/api/config",
        json={
            "paths": {
                "vault_root": str(tmp),
                "notes_folder": "客户售后问题",
                "after_sales_roots": [str(notes_dir), str(extra)],
            }
        },
    )
    assert resp.status_code == 200, resp.text

    rows = c.get("/api/notes").json()
    paths_set = {n["path"] for n in rows}
    assert "主位置-项目.md" in paths_set  # 主位置：相对主键（列表可读回）
    assert any(p.endswith("二区-项目B.md") for p in paths_set)  # 第二位置：绝对主键


def test_multi_memo_roots(client):
    """随手笔记最多 3 个位置：整根识别为 memo，不进售后列表。"""
    c, tmp, _ = client
    m1, m2 = tmp / "随手一", tmp / "随手二"
    m1.mkdir()
    m2.mkdir()
    (m1 / "a.md").write_bytes("---\nnote_type: memo\n---\n\n# A\n\n正文\n".encode("utf-8"))
    (m2 / "b.md").write_bytes("---\nnote_type: memo\n---\n\n# B\n\n正文\n".encode("utf-8"))

    resp = c.put(
        "/api/config",
        json={
            "paths": {
                "vault_root": str(tmp),
                "notes_folder": "客户售后问题",
                "memo_roots": [str(m1), str(m2)],
            }
        },
    )
    assert resp.status_code == 200, resp.text

    memos = {m["path"] for m in c.get("/api/memos").json()}
    assert any(p.endswith("a.md") for p in memos)
    assert any(p.endswith("b.md") for p in memos)

    notes = {n["path"] for n in c.get("/api/notes").json()}
    assert not any(p.endswith("a.md") for p in notes)
    assert not any(p.endswith("b.md") for p in notes)


def test_multi_pdf_roots(client):
    """PDF 手册最多 3 个位置：一次扫描覆盖所有根。"""
    c, tmp, _ = client
    p1, p2 = tmp / "手册一", tmp / "手册二"
    p1.mkdir()
    p2.mkdir()
    (p1 / "A.pdf").write_bytes(b"%PDF-1.4\n%fake\n")
    (p2 / "B.pdf").write_bytes(b"%PDF-1.4\n%fake\n")

    resp = c.put(
        "/api/config",
        json={"paths": {"vault_root": str(tmp), "pdf_roots": [str(p1), str(p2)]}},
    )
    assert resp.status_code == 200, resp.text

    from app.db import database
    from app.services import pdf_scan

    conn = database.connect()
    try:
        summary = pdf_scan.scan(conn, probe_new=False)
        conn.commit()
        assert summary["added"] == 2
        assert len(summary["roots"]) == 2
    finally:
        conn.close()


def test_db_roots_feed_knowledge_base(client):
    """「数据库索引位置」= 知识库要解析的资料目录：md 进索引，但不进售后列表。"""
    c, tmp, _ = client
    kb1, kb2 = tmp / "资料库一", tmp / "资料库二"
    kb1.mkdir()
    kb2.mkdir()
    (kb1 / "选型手册.md").write_bytes("# 选型手册\n\n伺服驱动器选型规则。\n".encode("utf-8"))
    (kb2 / "接线说明.md").write_bytes("# 接线说明\n\nCN1 引脚定义。\n".encode("utf-8"))

    resp = c.put(
        "/api/config",
        json={
            "paths": {
                "vault_root": str(tmp),
                "notes_folder": "客户售后问题",
                "db_roots": [str(kb1), str(kb2)],
            }
        },
    )
    assert resp.status_code == 200, resp.text

    rows = c.get("/api/kb/notes").json()  # 内部先 rescan
    kb_rows = [r for r in rows if r["group"] == "数据库"]
    assert {r["display_name"] for r in kb_rows} == {"选型手册.md", "接线说明.md"}
    assert all(Path(r["path"]).is_absolute() for r in kb_rows)

    # 不进售后列表
    notes = {n["path"] for n in c.get("/api/notes").json()}
    assert not any(p.endswith("选型手册.md") for p in notes)

    # 索引范围判定 + 待补齐命中
    from app.db import database
    from app.services import indexer

    conn = database.connect()
    try:
        rel = next(r["path"] for r in kb_rows if r["display_name"] == "选型手册.md")
        assert indexer._in_whitelist(conn, rel) is True
        assert rel in indexer.autofill_targets(conn)
        result = indexer.index_note(conn, rel, force=True)
        conn.commit()
        assert result["status"] == "ready"
        assert result["chunks"] >= 1
        row = conn.execute("SELECT note_type FROM note_file WHERE path=?", (rel,)).fetchone()
        assert row["note_type"] == "kb"
    finally:
        conn.close()


def test_pdf_roots_not_knowledge_base(client):
    """PDF 手册目录只用于浏览：里面的 md 不会被知识库扫描（不作为索引来源）。"""
    c, tmp, _ = client
    pdf_dir = tmp / "手册目录"
    pdf_dir.mkdir()
    (pdf_dir / "说明.md").write_bytes("# 说明\n\n不应进知识库。\n".encode("utf-8"))

    resp = c.put(
        "/api/config",
        json={"paths": {"vault_root": str(tmp), "pdf_roots": [str(pdf_dir)]}},
    )
    assert resp.status_code == 200, resp.text

    rows = c.get("/api/kb/notes").json()
    assert not any(Path(r["path"]).name == "说明.md" for r in rows)


def test_pdf_ingest_scope_follows_db_roots(client):
    """知识库 PDF 入库范围只认「数据库索引位置」：手册目录里的 PDF 仅浏览、不可入库。"""
    c, tmp, _ = client
    manual_dir = tmp / "手册浏览"
    kb_dir = tmp / "知识库资料"
    manual_dir.mkdir()
    kb_dir.mkdir()
    (manual_dir / "只看不入库.pdf").write_bytes(b"%PDF-1.4\n%fake\n")
    (kb_dir / "可入库.pdf").write_bytes(b"%PDF-1.4\n%fake\n")

    resp = c.put(
        "/api/config",
        json={
            "paths": {
                "vault_root": str(tmp),
                "pdf_roots": [str(manual_dir)],
                "db_roots": [str(kb_dir)],
            }
        },
    )
    assert resp.status_code == 200, resp.text

    # 手册页：只列「PDF 手册索引位置」内的 PDF（知识库目录里的不出现）
    manual_tree = c.get("/api/pdf/tree").json()
    assert manual_tree["total"] == 1
    manual_items = {i["name"]: i for g in manual_tree["groups"] for i in g["items"]}
    assert set(manual_items) == {"只看不入库"}
    assert manual_items["只看不入库"]["scope"] == "manual"

    # 知识库页（kb）：只列「数据库索引位置」里的 PDF
    kb_tree = c.get("/api/pdf/tree?scope=kb").json()
    assert kb_tree["total"] == 1
    assert kb_tree["groups"][0]["items"][0]["name"] == "可入库"

    # 入库闸门：非 kb 范围的 PDF 一律跳过，不排队列（也不启动后台任务）
    manual_id = manual_items["只看不入库"]["id"]
    r = c.post("/api/manuals/ingest", json={"manual_ids": [manual_id]}).json()
    assert r["queued"] == []
    assert r["skipped_outside"] == [manual_id]
    assert r["started"] is False
    assert "数据库索引位置" in r["reason"]

    bad = c.post(f"/api/manuals/{manual_id}/rebuild")
    assert bad.status_code == 400


def test_pdf_manual_page_hidden_without_pdf_roots(client):
    """手册页只认「PDF 手册索引位置」：该位置为空时，不展示知识库目录里的 PDF。"""
    c, tmp, _ = client
    kb_dir = tmp / "知识库资料"
    kb_dir.mkdir()
    (kb_dir / "知识库里的.pdf").write_bytes(b"%PDF-1.4\n%fake\n")

    resp = c.put(
        "/api/config",
        json={"paths": {"vault_root": str(tmp), "pdf_roots": [], "pdf_root": "", "db_roots": [str(kb_dir)]}},
    )
    assert resp.status_code == 200, resp.text

    # 手册页：没有配置手册位置 → 明确报「未配置」，而不是把知识库目录的 PDF 列出来
    manual_tree = c.get("/api/pdf/tree")
    assert manual_tree.status_code == 400
    assert manual_tree.json()["error"]["code"] == "path_invalid"

    # 知识库页仍能正常列出该目录内的 PDF
    kb_tree = c.get("/api/pdf/tree?scope=kb")
    assert kb_tree.status_code == 200, kb_tree.text
    assert kb_tree.json()["total"] == 1


def test_db_location_api(client):
    """程序数据库文件位置：可保存，切换时复制现有库并提示重启。"""
    c, tmp, _ = client
    current = c.get("/api/system/db-location").json()
    assert current["db_file"].endswith("workbench.db")
    assert current["dir"]

    target = tmp / "workbench_db"
    resp = c.put("/api/system/db-location", json={"dir": str(target)})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["restart_required"] is True
    assert data["dir"] == str(target.resolve())
    assert (target / "workbench.db").is_file()  # 已把现有库复制过去

    after = c.get("/api/system/db-location").json()
    assert after["dir"] == str(target.resolve())

    # 恢复实例配置，避免影响后续用例（测试隔离）
    paths.INSTANCE_FILE.unlink(missing_ok=True)

    # 校验失败分支：空目录
    bad = c.put("/api/system/db-location", json={"dir": ""})
    assert bad.status_code == 400


def test_pick_folder_endpoint(client, monkeypatch):
    """原生文件夹选择框：返回路径 / 取消 / 不支持 / 已有窗口在等。"""
    c, tmp, _ = client
    from app.services import folder_picker

    monkeypatch.setattr(folder_picker, "available", lambda: True)
    monkeypatch.setattr(folder_picker, "pick_folder", lambda *a, **k: str(tmp))
    ok = c.post("/api/system/pick-folder")
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"path": str(tmp), "cancelled": False}

    # 用户点取消：空路径 + cancelled
    monkeypatch.setattr(folder_picker, "pick_folder", lambda *a, **k: "")
    cancel = c.post("/api/system/pick-folder")
    assert cancel.status_code == 200
    assert cancel.json()["cancelled"] is True

    # 已有对话框在等
    def _busy(*a, **k):
        raise folder_picker.PickerBusy("已有一个文件夹选择窗口在等待操作")

    monkeypatch.setattr(folder_picker, "pick_folder", _busy)
    busy = c.post("/api/system/pick-folder")
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "picker_busy"

    # 平台不支持 → 前端退回手动输入
    monkeypatch.setattr(folder_picker, "available", lambda: False)
    unavailable = c.post("/api/system/pick-folder")
    assert unavailable.status_code == 501
    assert unavailable.json()["error"]["code"] == "picker_unavailable"
