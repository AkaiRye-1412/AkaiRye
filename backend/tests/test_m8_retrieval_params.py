"""设置页「检索与切分参数」（config.retrieval）测试。

覆盖：写库钳制 / TOP_K 生效 / 相似度阈值过滤向量路 / 历史条数可归零 / 分块大小生效。
"""

import shutil
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import database
from app.main import create_app
from app.services import indexer, rag_service, textutil


@pytest.fixture()
def client(monkeypatch):
    """临时 vault（1 篇笔记）+ 模拟 embedding（同向量，余弦恒为 1）。"""
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_rp_"))
    notes_dir = tmp / "客户售后问题"
    notes_dir.mkdir(parents=True)
    (notes_dir / "星河纸业-复卷机.md").write_bytes(
        ("---\nname: 星河纸业\nproject: 复卷机\nstatus: 进行中\n"
         "start_date: 2026-10-28\nupdated: 2026-11-24\n---\n\n"
         "# 复卷机\n\n## 2026-11-20 现场处理\n\n"
         "现象：张力波动大，ACS-2600i 报 ERR-1204。\n"
         "处理：调整 PID 参数后恢复正常。\n").encode("utf-8")
    )

    rag_service._latest_context.clear()
    rag_service._generating.clear()
    monkeypatch.setattr(
        indexer, "_embed_texts", lambda conn, texts: [[0.1] * 1024 for _ in texts]
    )

    c = TestClient(create_app())
    c.__enter__()
    c.put("/api/config", json={"paths": {"vault_root": str(tmp), "notes_folder": "客户售后问题"}})
    c.post("/api/kb/reindex")
    yield c, notes_dir, tmp
    c.__exit__(None, None, None)
    shutil.rmtree(tmp, ignore_errors=True)


def _wait_done(c, message_id: int, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = c.get(f"/api/rag/messages/{message_id}").json()
        if msg["content"]:
            return msg
        time.sleep(0.05)
    raise AssertionError("回答未在超时时间内落库")


# ---- 默认值与写库钳制 ---------------------------------------------------------

def test_defaults_returned_when_not_configured(client):
    c, _, _ = client
    cfg = c.get("/api/config").json()
    assert cfg["retrieval"] == {
        "top_k": 8,
        "min_similarity": 0.0,
        "chunk_size": 1200,
        "pdf_chunk_size": 900,
        "chunk_overlap": 0,
        "history_turns": 10,
        "embed_max_chars": 700,
        "rerank_enabled": False,
        "rerank_model_path": "",
        "rerank_top_n": 30,
        "rerank_keep": 8,
    }


def test_out_of_range_values_are_clamped_on_save(client):
    """直接打 API 塞脏值：写库时钳住，读回来就是合法值（否则设置页会永远显示"待保存"）。"""
    c, _, _ = client
    c.put("/api/config", json={"retrieval": {
        "top_k": 999,
        "min_similarity": 5,
        "chunk_size": 10,
        "pdf_chunk_size": 99999,
        "chunk_overlap": 4000,   # 也要被压到小块的一半以内
        "history_turns": -3,
        "embed_max_chars": 1,
        "rerank_top_n": 9999,
        "rerank_keep": -5,
    }})
    got = c.get("/api/config").json()["retrieval"]
    assert got["top_k"] == 30
    assert got["min_similarity"] == 1.0
    assert got["chunk_size"] == 200
    assert got["pdf_chunk_size"] == 4000
    assert got["chunk_overlap"] == 100          # min(200, 4000) // 2
    assert got["history_turns"] == 0
    assert got["embed_max_chars"] == 100
    assert got["rerank_top_n"] == 100
    assert got["rerank_keep"] == 1


def test_non_numeric_falls_back_to_default(client):
    c, _, _ = client
    c.put("/api/config", json={"retrieval": {"top_k": "很多", "min_similarity": None}})
    got = c.get("/api/config").json()["retrieval"]
    assert got["top_k"] == 8
    assert got["min_similarity"] == 0.0


# ---- TOP_K -------------------------------------------------------------------

def test_top_k_from_config_limits_chunks(client):
    c, _, _ = client
    c.put("/api/config", json={"retrieval": {"top_k": 1}})
    conn = database.connect()
    try:
        res = rag_service.retrieve(conn, "张力 ERR-1204", {"mode": "all"})
    finally:
        conn.close()
    assert len(res["chunks"]) == 1


def test_top_k_argument_overrides_config(client):
    c, _, _ = client
    c.put("/api/config", json={"retrieval": {"top_k": 1}})
    conn = database.connect()
    try:
        res = rag_service.retrieve(conn, "张力 ERR-1204", {"mode": "all"}, top_k=5)
    finally:
        conn.close()
    assert len(res["chunks"]) > 1


# ---- 相似度阈值 ---------------------------------------------------------------

def test_min_similarity_filters_vector_route(client, monkeypatch):
    """查询向量与库内向量正交（相似度 0）：阈值 0.5 时向量路不得再兜出「最相近的几条」。"""
    c, _, _ = client

    # 索引时是 [0.1]*1024；查询时换成 [1.0, 0, 0, ...] —— 余弦相似度为 0
    def ortho_embed(conn, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(indexer, "_embed_texts", ortho_embed)

    conn = database.connect()
    try:
        # 阈值 0：向量路照常返回（RRF 里排名靠前，不算失败）
        loose = rag_service.retrieve(conn, "zzz-no-hit", {"mode": "all"}, min_similarity=0.0)
        # 阈值 0.5：余弦 0 < 0.5，全部被过滤，FTS 也查不到 → 空
        strict = rag_service.retrieve(conn, "zzz-no-hit", {"mode": "all"}, min_similarity=0.5)
    finally:
        conn.close()
    assert loose["vector_used"] is True and loose["chunks"]
    assert strict["chunks"] == []


def test_min_similarity_zero_keeps_similarity_field(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setattr(
        indexer, "_embed_texts", lambda conn, texts: [[0.1] * 1024 for _ in texts]
    )
    conn = database.connect()
    try:
        res = rag_service.retrieve(conn, "张力", {"mode": "all"})
    finally:
        conn.close()
    sims = [ch["similarity"] for ch in res["chunks"] if "similarity" in ch]
    assert sims and all(s == pytest.approx(1.0, abs=1e-6) for s in sims)


# ---- 历史条数 -----------------------------------------------------------------

def test_history_turns_zero_disables_memory(client, monkeypatch):
    """history_turns=0：追问不再带历史，首问无命中时直接回「未找到」。"""
    c, _, _ = client
    c.put("/api/config", json={"retrieval": {"history_turns": 0}})
    # FTS-only：避免向量路把"最相近的几条"兜出来，让第二问真的无命中
    monkeypatch.setattr(indexer, "_embed_texts", lambda conn, texts: None)
    s = c.post("/api/rag/sessions", json={}).json()

    calls: list[list[dict]] = []

    def fake_llm(conn, messages):
        calls.append(messages)
        yield "已结合上文给出建议。"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)

    a1 = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "张力",
                                      "scope": {"mode": "all"}}).json()
    _wait_done(c, a1["message_id"])

    a2 = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "建议如何做",
                                      "scope": {"mode": "all"}}).json()
    msg2 = _wait_done(c, a2["message_id"])
    # 无历史可依托 → 第二问不进 LLM，直接给未找到（只有第一问调用过一次）
    assert "未找到" in msg2["content"]
    assert len(calls) == 1
    assert all(m["role"] != "assistant" for m in calls[0])  # 首问本来就没有历史


# ---- 分块大小 -----------------------------------------------------------------

def test_chunk_size_config_drives_note_chunking(client):
    c, notes_dir, _ = client
    c.put("/api/config", json={"retrieval": {"chunk_size": 200, "chunk_overlap": 0}})
    long_note = notes_dir / "炬光-长文.md"
    long_note.write_bytes(
        ("---\nname: 炬光\nproject: 长文\nstatus: 进行中\n"
         "start_date: 2026-09-01\nupdated: 2026-09-15\n---\n\n"
         "# 长文\n\n## 2026-09-01 记录\n\n"
         + "这是用于验证切块粒度的一句话，描述了设备参数与注意事项。\n" * 60).encode("utf-8")
    )
    assert c.post("/api/kb/reindex").status_code in (200, 202)

    conn = database.connect()
    try:
        rows = conn.execute(
            "SELECT content FROM chunk WHERE note_path LIKE '%长文.md'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) > 1
    assert max(len(r["content"]) for r in rows) <= 200


def test_overlap_shares_tail_between_chunks():
    """重叠是纯函数行为，直接验：相邻块共享尾巴，且超长单段会被硬切而不是整块入库。"""
    body = "故障现象与处理步骤说明。" * 80
    plain = textutil._split_long(body, 300, 0)
    shared = textutil._split_long(body, 300, 80)

    assert all(len(p) <= 300 for p in plain)
    assert all(len(p) <= 300 for p in shared)
    assert len(shared) > len(plain)          # 重叠会多切出几块
    assert shared[1][:12] in shared[0]       # 下一块开头来自上一块尾巴

    # chunk_note_markdown 走同一套切分，长段不该整段入库（旧行为会切出 3000 字的大块）
    body_md = "# 标题\n\n## 小节\n\n" + "故障现象与处理步骤说明。" * 80
    chunks = textutil.chunk_note_markdown(body_md, max_chars=300, overlap=80)
    assert all(len(p["content"]) <= 300 for p in chunks)


# ---- reranker 精排 -----------------------------------------------------------

def test_rerank_disabled_returns_original_order(client):
    """未启用 reranker：retrieve 返回 reranked=False，且不加载模型。"""
    c, _, _ = client
    c.put("/api/config", json={"retrieval": {"rerank_enabled": False}})
    conn = database.connect()
    try:
        res = rag_service.retrieve(conn, "张力", {"mode": "all"})
    finally:
        conn.close()
    assert res["reranked"] is False
    assert "rerank_score" not in res["chunks"][0]


def test_rerank_enabled_with_missing_path_degrades_gracefully(client):
    """启用了 reranker 但路径不存在：不能因此丢候选，回退原顺序。"""
    c, _, _ = client
    c.put("/api/config", json={"retrieval": {
        "rerank_enabled": True,
        "rerank_model_path": "D:/does/not/exist",
        "rerank_top_n": 20,
        "rerank_keep": 5,
    }})
    conn = database.connect()
    try:
        res = rag_service.retrieve(conn, "张力 ERR-1204", {"mode": "all"})
    finally:
        conn.close()
    # 路径无效 → reranker.enabled 为 False → 走普通截断，reranked 仍标记 False
    assert res["reranked"] is False
    assert res["chunks"]  # 候选没有丢


def test_rerank_calls_model_and_truncates(monkeypatch):
    """reranker 单例逻辑：假模型返回分数，rerank() 按分数降序截到 keep 条。"""
    from app.services import reranker

    class FakeModel:
        def __init__(self, path, use_fp16=False):
            pass

        def compute_score(self, pairs, normalize=True):
            # 让分数 = 文档里数字的倒数（文档 1 分最高）
            return [1.0 / (i + 1) for i in range(len(pairs))]

    monkeypatch.setattr(reranker, "_model", FakeModel("", use_fp16=False))
    monkeypatch.setattr(reranker, "_model_path", "x")
    monkeypatch.setattr(reranker, "_load_error", None)

    cands = [{"id": i, "title_path": f"t{i}", "content": f"c{i}"} for i in range(10)]
    out = reranker.rerank("q", cands, "x", keep=3)
    assert len(out) == 3
    # 分数降序：文档 0（分 1.0）应排最前
    assert out[0]["id"] == 0
    assert out[0]["rerank_score"] == pytest.approx(1.0, abs=1e-3)

