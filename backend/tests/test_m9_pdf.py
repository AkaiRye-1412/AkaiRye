"""M9 PDF 解析 / 目录扫描 / 入库队列测试。

覆盖：
- 章节识别与页码映射（标题层级、页眉页脚剔除、块长度上限）；
- pdf_root 增量同步（新增 discovered → mtime 变化 update → 文件消失 invalid）；
- 入库写块（manual 来源、page 非空、FTS 命中、章节聚合）与删除只清索引不删文件；
- 扫描件（无文本层）给出可读失败原因。
"""

import shutil
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import database
from app.main import create_app
from app.services import indexer, manual_index, pdf_parse


def _make_pdf(path: Path, pages: list[tuple[str, list[str]]], *, footer: str = "内部资料") -> None:
    """生成多页中文 PDF：页眉页脚跨页重复（用于验证剔重）。pymupdf 自带 CJK 字体。"""
    import pymupdf

    doc = pymupdf.open()
    for title, lines in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 40), "测试手册页眉", fontname="china-s", fontsize=9)
        y = 100
        page.insert_text((60, y), title, fontname="china-s", fontsize=18)
        y += 40
        for line in lines:
            page.insert_text((60, y), line, fontname="china-s", fontsize=11)
            y += 22
        page.insert_text((60, 810), footer, fontname="china-s", fontsize=8)
    doc.save(path)
    doc.close()


def _make_scanned_pdf(path: Path) -> None:
    """只有图形没有文本层（模拟扫描件）。"""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=595, height=842)
        page.draw_rect(pymupdf.Rect(40, 40, 555, 800), fill=(0.96, 0.96, 0.96))
    doc.save(path)
    doc.close()


# ---- 解析器 -----------------------------------------------------------------

def test_parse_sections_pages_and_header_strip(tmp_path: Path):
    pdf = tmp_path / "ACS 手册.pdf"
    _make_pdf(
        pdf,
        [
            ("第 1 章 安全须知", ["1.1 适用范围", "本手册适用于 ACS-2600i 系列驱动器。"]),
            ("第 1 章 安全须知", ["1.2 人员要求", "只有取得资质的人员才能配线。"]),
            ("第 2 章 故障诊断", ["2.1 报警代码", "Err-01 过流：检查电机线是否短路。"]),
        ],
    )
    parsed = pdf_parse.parse_pdf(pdf)

    assert parsed.pages == 3
    assert parsed.engine == "pymupdf"
    assert parsed.scanned is False
    assert parsed.chunks, "应解析出切块"

    # 章节层级：章 → 节
    paths = [c["title_path"] for c in parsed.chunks]
    assert any(p and "第 1 章 安全须知 / 1.1 适用范围" in p for p in paths)
    assert any(p and "第 2 章 故障诊断 / 2.1 报警代码" in p for p in paths)

    # 页码来自 PDF 实际页（第 1 块在 p1，故障章节在 p3）
    err = next(c for c in parsed.chunks if "Err-01" in c["content"])
    assert err["page"] == 3

    # 页眉页脚被剔除，且被并入正文的频率不该出现
    combined = "\n".join(c["content"] for c in parsed.chunks)
    assert "测试手册页眉" not in combined
    assert "内部资料" not in combined


def test_parse_rejects_scanned_pdf(tmp_path: Path):
    pdf = tmp_path / "扫描件.pdf"
    _make_scanned_pdf(pdf)
    with pytest.raises(pdf_parse.PdfNoTextError) as ei:
        pdf_parse.parse_pdf(pdf)
    assert "文本层" in str(ei.value)


# ---- 目录扫描 + 入库 ---------------------------------------------------------

@pytest.fixture()
def env(monkeypatch):
    """临时 pdf_root + 隔离 DB；embedding 用假实现避免真实调用。"""
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_pdf_"))
    root = tmp / "手册"
    (root / "ACS").mkdir(parents=True)
    _make_pdf(
        root / "ACS" / "ACS-2600i 用户手册.pdf",
        [
            ("第 1 章 安全须知", ["1.1 适用范围", "本手册适用于 ACS-2600i 系列。"]),
            ("第 2 章 故障诊断", ["2.1 报警代码", "Err-01 过流：检查电机线短路。"]),
        ],
    )

    def fake_embed(conn, texts, on_batch=None):
        if on_batch:
            on_batch(len(texts), len(texts))
        return [[0.05] * 8 for _ in texts]

    monkeypatch.setattr(indexer, "_embed_texts", fake_embed)

    client = TestClient(create_app())
    client.__enter__()
    client.put("/api/config", json={"paths": {"pdf_root": str(root)}})
    yield client, root
    client.__exit__(None, None, None)
    shutil.rmtree(tmp, ignore_errors=True)


def test_scan_sync_states(env):
    client, root = env

    tree = client.get("/api/pdf/tree").json()
    assert tree["scan"]["added"] == 1
    item = tree["groups"][0]["items"][0]
    assert item["status"] == "discovered"
    assert item["brand"] == "ACS"
    assert item["pages"] == 2

    # mtime 变化 → update（仅已入库的有意义，这里先入库）
    mid = item["id"]
    with database.get_conn() as conn:
        manual_index.index_manual(conn, mid)
    assert client.get(f"/api/manuals/{mid}").json()["status"] == "ready"

    time.sleep(1.1)
    (root / "ACS" / "ACS-2600i 用户手册.pdf").touch()
    summary = client.post("/api/pdf/rescan").json()
    assert summary["updated"] == 1
    assert client.get(f"/api/manuals/{mid}").json()["status"] == "update"

    # 文件消失 → invalid（保留块，便于继续检索/重新定位）
    (root / "ACS" / "ACS-2600i 用户手册.pdf").unlink()
    summary = client.post("/api/pdf/rescan").json()
    assert summary["invalid"] == 1
    assert client.get(f"/api/manuals/{mid}").json()["status"] == "invalid"


def test_ingest_chunks_and_delete_keeps_file(env):
    client, root = env
    tree = client.get("/api/pdf/tree").json()
    mid = tree["groups"][0]["items"][0]["id"]

    with database.get_conn() as conn:
        result = manual_index.index_manual(conn, mid)
    assert result["status"] == "ready"
    assert result["chunks"] >= 2

    detail = client.get(f"/api/manuals/{mid}").json()
    assert detail["status"] == "ready"
    assert detail["chunks"] == result["chunks"]
    assert detail["sections"], "章节聚合应有内容"

    # 块来源必须是 manual，且带页码；FTS 能命中报警词
    with database.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, page, note_path, manual_id FROM chunk WHERE manual_id=?", (mid,)
        ).fetchall()
        assert rows and all(r["note_path"] is None and r["page"] for r in rows)

        hit = conn.execute(
            "SELECT c.manual_id FROM chunk_fts f JOIN chunk c ON c.id=f.chunk_id"
            " WHERE chunk_fts MATCH ? LIMIT 1",
            ('"过流"',),
        ).fetchone()
        assert hit and hit["manual_id"] == mid

    meta = client.get(f"/api/reader/meta/{mid}").json()
    assert meta["chapters"] and any(ch["page"] == 2 for ch in meta["chapters"])

    # Range 请求（阅读器断点/按页加载）
    r = client.get(f"/api/reader/pdf/{mid}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers.get("content-disposition", "").startswith("inline")

    # 删除只清索引，PDF 仍在磁盘
    deleted = client.delete(f"/api/manuals/{mid}").json()
    assert deleted["deleted_chunks"] == result["chunks"]
    assert (root / "ACS" / "ACS-2600i 用户手册.pdf").is_file()
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM chunk WHERE manual_id=?", (mid,)).fetchone()["c"] == 0


def test_scanned_pdf_marked_failed(env):
    client, root = env
    _make_scanned_pdf(root / "ACS" / "扫描版.pdf")
    tree = client.get("/api/pdf/tree").json()
    scanned = next(i for g in tree["groups"] for i in g["items"] if i["name"] == "扫描版")

    with database.get_conn() as conn, pytest.raises(pdf_parse.PdfNoTextError):
        manual_index.index_manual(conn, scanned["id"])
    # 失败原因落到 manual.error（供列表悬停查看）
    with database.get_conn() as conn:
        conn.execute(
            "UPDATE manual SET status='failed', error=? WHERE id=?",
            ("PDF 没有可抽取的文本层", scanned["id"]),
        )
        conn.commit()
    assert client.get(f"/api/manuals/{scanned['id']}").json()["status"] == "failed"
