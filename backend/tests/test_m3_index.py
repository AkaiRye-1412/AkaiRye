"""M3 笔记索引测试：切块器 / FTS-only 降级 / 向量入库 / 增量重索引 / 白名单 / 中文检索。"""

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import indexer, textutil


@pytest.fixture()
def client(monkeypatch):
    """临时 vault（conftest 已隔离 DB 到独立临时目录）。"""
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_idx_"))
    notes_dir = tmp / "客户售后问题"
    notes_dir.mkdir(parents=True)
    (notes_dir / "星河纸业-复卷机张力控制异常.md").write_bytes(
        ("---\nname: 星河纸业\nproject: 复卷机张力控制异常\nstatus: 进行中\n"
         "start_date: 2026-10-28\nupdated: 2026-11-24\n---\n\n"
         "# 复卷机张力控制异常\n\n"
         "## 2026-11-20 现场处理\n\n"
         "现象：张力波动大，ACS-2600i 报 ERR-1204。\n"
         "处理：调整 PID 参数后恢复正常。\n\n"
         "## 2026-11-24 回访\n\n"
         "客户确认连续运行三天无异常，故障码未再出现。\n").encode("utf-8")
    )
    (notes_dir / "炬光-振镜温控.md").write_bytes(
        ("---\nname: 炬光\nproject: 振镜温控\nstatus: 进行中\n"
         "start_date: 2026-09-01\nupdated: 2026-09-15\n---\n\n"
         "# 振镜温控\n\n## 2026-09-01 初访\n\n水冷机流量报警，GK-800 传感器接线松动。\n").encode("utf-8")
    )

    client = TestClient(create_app())
    client.__enter__()
    client.put("/api/config", json={"paths": {"vault_root": str(tmp), "notes_folder": "客户售后问题"}})
    yield client, notes_dir
    client.__exit__(None, None, None)
    client.put("/api/config", json={"paths": {"vault_root": "", "notes_folder": "客户售后问题"}})
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fake_embedding(monkeypatch):
    """模拟 embedding 接口（1024 维 = VEC_DIM_DEFAULT），测试向量入库路径。"""
    state = {"calls": 0}

    def fake_embed(conn, texts):
        state["calls"] += 1
        return [[0.1 * (len(t) % 8 + 1)] * 1024 for t in texts]

    monkeypatch.setattr(indexer, "_embed_texts", fake_embed)
    indexer._embed_state = state
    yield state


# ---- 切块器 ----------------------------------------------------------------

def test_chunk_by_sections():
    text = (
        "---\nname: x\n---\n\n"
        "# 主标题\n\n引言段。\n\n"
        "## 2026-11-20 现场处理\n\n现象：A。\n处理：B。\n\n"
        "## 2026-11-24 回访\n\n客户确认正常。\n"
    )
    chunks = textutil.chunk_note_markdown(text)
    assert len(chunks) == 3
    assert chunks[0]["title_path"] == "主标题"
    assert chunks[1]["title_path"] == "主标题 / 2026-11-20 现场处理"
    assert "现象：A" in chunks[1]["content"]
    assert [c["seq"] for c in chunks] == [0, 1, 2]


def test_chunk_long_text_split():
    body = "段落内容。\n\n" * 200  # ~2000+ 字符
    chunks = textutil.chunk_note_markdown(body, max_chars=1200)
    assert len(chunks) >= 2
    assert all(len(c["content"]) <= 1300 for c in chunks)


# ---- 索引主流程 ------------------------------------------------------------

def test_index_and_search_chinese(client):
    c, _ = client
    resp = c.post("/api/kb/reindex")
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] == 2 and body["failed"] == 0

    # FTS 中文检索（bigram 短语）：DB 直查验证
    from app.db.database import connect

    conn = connect()
    try:
        expr = textutil.phrase_expr("张力波动")
        hits = conn.execute(
            "SELECT c.note_path FROM chunk_fts f JOIN chunk c ON c.id = f.chunk_id WHERE chunk_fts MATCH ?",
            (expr,),
        ).fetchall()
        assert len(hits) >= 1 and hits[0]["note_path"].startswith("星河纸业")
        # ASCII 型号整体命中
        expr2 = textutil.phrase_expr("ACS-2600i")
        hits2 = conn.execute(
            "SELECT c.note_path FROM chunk_fts f JOIN chunk c ON c.id = f.chunk_id WHERE chunk_fts MATCH ?",
            (expr2,),
        ).fetchall()
        assert len(hits2) >= 1
        # 向量已入库（模拟 embedding）
        vec_count = conn.execute("SELECT COUNT(*) c FROM chunk_vec").fetchone()["c"]
        chunk_count = conn.execute("SELECT COUNT(*) c FROM chunk").fetchone()["c"]
        assert vec_count == chunk_count > 0
        assert indexer._embed_state["calls"] >= 1
    finally:
        conn.close()

    statuses = {n["path"]: n for n in c.get("/api/kb/notes").json()}
    assert all(s["indexed_status"] == "ready" for s in statuses.values())
    assert statuses["星河纸业-复卷机张力控制异常.md"]["chunks"] == 3


def test_fts_only_degradation(monkeypatch, client):
    """embedding 未配置 → FTS-only 降级：chunk/fts 写入、无 vec 行。"""
    c, _ = client
    monkeypatch.setattr(indexer, "_embed_texts", lambda conn, texts: None)
    resp = c.post("/api/kb/reindex")
    assert resp.status_code == 200 and resp.json()["done"] == 2

    from app.db.database import connect

    conn = connect()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM chunk").fetchone()["c"] > 0
        assert conn.execute("SELECT COUNT(*) c FROM chunk_vec").fetchone()["c"] == 0
        row = conn.execute("SELECT DISTINCT model_id FROM chunk").fetchone()
        assert row["model_id"] == indexer.FTS_ONLY_MODEL
    finally:
        conn.close()


def test_incremental_reindex_replaces_chunks(client):
    """修改笔记后重索引：旧块三件套删除、新块写入（03 场景 6）。"""
    c, notes_dir = client
    c.post("/api/kb/reindex")

    from app.db.database import connect

    target = notes_dir / "炬光-振镜温控.md"
    old = target.read_text(encoding="utf-8")
    target.write_bytes((old + "\n## 2026-09-15 复访\n\n更换温控探头，问题闭环。\n").encode("utf-8"))

    conn = connect()
    try:
        result = indexer.index_note(conn, "炬光-振镜温控.md")
        conn.commit()
        assert result["status"] == "ready"
        chunks = conn.execute(
            "SELECT seq, content FROM chunk WHERE note_path=? ORDER BY seq", ("炬光-振镜温控.md",)
        ).fetchall()
        assert len(chunks) == 3 and "复访" in chunks[2]["content"]
        vec = conn.execute("SELECT COUNT(*) c FROM chunk_vec").fetchone()["c"]
        assert vec == 6  # 星河 3 块 + 炬光 3 块，全部有向量
    finally:
        conn.close()


def test_whitelist_exclude_deletes_chunks(client):
    """白名单（03 场景 2）：根下文件恒在白名单内（notes_folder 核心）；
    子目录需顶层目录 ∈ folders，移出 → 删块 + excluded，纳入 → 重新索引。"""
    c, notes_dir = client
    c.post("/api/kb/reindex")

    sub = notes_dir / "外部项目笔记"
    sub.mkdir()
    (sub / "外部-某项目.md").write_bytes(
        ("---\nname: 外部\nproject: 某项目\nstatus: 进行中\n"
         "start_date: 2026-11-01\nupdated: 2026-11-02\n---\n\n# 某项目\n\n## 2026-11-01\n\n外部目录笔记内容。\n").encode("utf-8")
    )
    c.post("/api/kb/reindex")
    from app.db.database import connect

    conn = connect()
    try:
        excluded = conn.execute(
            "SELECT COUNT(*) c FROM note_file WHERE indexed_status='excluded'"
        ).fetchone()["c"]
        assert excluded == 1  # 顶层目录 ∉ folders
        assert conn.execute(
            "SELECT COUNT(*) c FROM chunk WHERE note_path='外部项目笔记/外部-某项目.md'"
        ).fetchone()["c"] == 0
    finally:
        conn.close()

    # 纳入白名单 → 重新索引出块
    resp = c.put("/api/kb/whitelist", json={"folders": ["客户售后问题", "外部项目笔记"]})
    assert resp.status_code == 200 and resp.json()["indexed"] >= 1
    conn = connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM chunk WHERE note_path='外部项目笔记/外部-某项目.md'"
        ).fetchone()["c"] >= 1
    finally:
        conn.close()


def test_single_reindex_endpoint(client):
    c, _ = client
    resp = c.post("/api/kb/notes/reindex", json={"path": "炬光-振镜温控.md"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
    missing = c.post("/api/kb/notes/reindex", json={"path": "不存在.md"})
    assert missing.status_code == 404


def test_stats_endpoint(client):
    c, _ = client
    c.post("/api/kb/reindex")
    s = c.get("/api/kb/stats").json()
    assert s["notes"] == 2 and s["chunks"] >= 3
    assert s["vector_model"]["dim"] == 1024  # 配置默认 dim（= VEC_DIM_DEFAULT）


# ---- 外部知识库文件夹（2026-11-26 扩展：白名单支持绝对路径） ------------------

def test_external_folder_whitelist(client):
    """白名单加绝对路径的外部文件夹 → 扫描入库、可检索；不进售后列表与看板。"""
    c, notes_dir = client
    ext = Path(tempfile.mkdtemp(prefix="wb_test_ext_")).resolve()
    sub = notes_dir / "子项目"
    sub.mkdir(exist_ok=True)
    try:
        (ext / "Python基础-装饰器.md").write_bytes(
            ("---\ntitle: Python 装饰器\n---\n\n# 装饰器\n\nfunctools.wraps 用于保留被装饰函数的元信息。\n").encode("utf-8")
        )
        (sub / "内部-子文件夹.md").write_bytes(
            ("---\nname: 内部\n---\n\n# 子文件夹笔记\n\n售后文件夹下的子目录内容。\n").encode("utf-8")
        )
        resp = c.put("/api/kb/whitelist", json={"folders": ["子项目", str(ext)]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["invalid"] == [] and body["indexed"] >= 4  # 2 篇根下 + 1 子文件夹 + 1 外部

        # 知识库状态表包含外部笔记（绝对路径主键）
        kb_notes = {n["path"]: n for n in c.get("/api/kb/notes").json()}
        ext_key = (ext / "Python基础-装饰器.md").as_posix()
        assert ext_key in kb_notes and kb_notes[ext_key]["indexed_status"] == "ready"

        # 售后列表与看板不包含外部笔记
        note_paths = {n["path"] for n in c.get("/api/notes").json()}
        assert ext_key not in note_paths
        s = c.get("/api/dashboard/summary").json()
        assert s["active_count"] == 3  # 2 根下 + 1 子文件夹；外部笔记不计入

        # 中文检索命中外部笔记内容
        from app.db.database import connect

        conn = connect()
        try:
            expr = textutil.phrase_expr("装饰器")
            hits = conn.execute(
                "SELECT c.note_path FROM chunk_fts f JOIN chunk c ON c.id=f.chunk_id "
                "WHERE chunk_fts MATCH ?", (expr,)
            ).fetchall()
            assert any(r["note_path"] == ext_key for r in hits)
        finally:
            conn.close()

        # 移出 → 删块
        resp = c.put("/api/kb/whitelist", json={"folders": ["客户售后问题"]})
        assert resp.status_code == 200
        kb_after = {n["path"]: n for n in c.get("/api/kb/notes").json()}
        assert ext_key not in kb_after
    finally:
        shutil.rmtree(ext, ignore_errors=True)


# ---- 查询表达式：bigram 逐词 OR（2026-11-26 V3 修复纯中文整段短语 0 命中） ----------

def test_phrase_expr_multi_segment_or():
    """每个 bigram 独立成短语后 OR：不再要求段内/段间紧邻。"""
    assert textutil.phrase_expr("__main__什么意思") == '"main" OR "什么" OR "么意" OR "意思"'
    # 纯中文查询：跨词 bigram（绍下/下集）索引中通常不存在，不引入误命中
    assert textutil.phrase_expr("介绍下集合") == '"介绍" OR "绍下" OR "下集" OR "集合"'
    assert textutil.phrase_expr("追焦") == '"追焦"'
    assert textutil.phrase_expr("张力波动") == '"张力" OR "力波" OR "波动"'


def test_mixed_query_hits_ascii_term(client):
    """文档含 __main__ 的块能被 "__main__什么意思" 命中。"""
    from app.db.database import connect

    c, notes_dir = client
    (notes_dir / "py-入门.md").write_bytes(
        ("---\nname: py\n---\n\n# 入门\n\n## main 的作用\n\n"
         "if __name__ == '__main__' 表示直接运行该文件时执行主逻辑。\n").encode("utf-8")
    )
    c.post("/api/kb/reindex")
    from app.services import rag_service

    conn = connect()
    try:
        r = rag_service.retrieve(conn, "__main__什么意思", {"mode": "all"})
        assert any("py-入门.md" in ch["note_path"] for ch in r["chunks"])
    finally:
        conn.close()
