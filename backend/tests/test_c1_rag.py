"""C1 RAG 测试：会话 CRUD / 混合检索 RRF / scope 过滤 / 流式 ask / 引用 / 409 并发。"""

import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import indexer, rag_service
from app.ws.events import broker


@pytest.fixture()
def client(monkeypatch):
    """临时 vault（conftest 已隔离 DB），2 篇笔记 + 已索引。"""
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_rag_"))
    notes_dir = tmp / "客户售后问题"
    notes_dir.mkdir(parents=True)
    (notes_dir / "星河纸业-复卷机.md").write_bytes(
        ("---\nname: 星河纸业\nproject: 复卷机\nstatus: 进行中\n"
         "start_date: 2026-10-28\nupdated: 2026-11-24\n---\n\n"
         "# 复卷机\n\n## 2026-11-20 现场处理\n\n"
         "现象：张力波动大，ACS-2600i 报 ERR-1204。\n"
         "处理：调整 PID 参数后恢复正常。\n").encode("utf-8")
    )
    (notes_dir / "炬光-振镜温控.md").write_bytes(
        ("---\nname: 炬光\nproject: 振镜温控\nstatus: 进行中\n"
         "start_date: 2026-09-01\nupdated: 2026-09-15\n---\n\n"
         "# 振镜温控\n\n## 2026-09-01 初访\n\n水冷机流量报警，GK-800 传感器接线松动。\n").encode("utf-8")
    )

    c = TestClient(create_app())
    c.__enter__()
    c.put("/api/config", json={"paths": {"vault_root": str(tmp), "notes_folder": "客户售后问题"}})
    c.post("/api/kb/reindex")
    yield c, notes_dir, tmp
    c.__exit__(None, None, None)
    c.put("/api/config", json={"paths": {"vault_root": "", "notes_folder": "客户售后问题"}})
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """清空上下文/并发标记；默认模拟 embedding（向量路可测）。"""
    rag_service._latest_context.clear()
    rag_service._generating.clear()

    def fake_embed(conn, texts):
        return [[0.1 * (len(t) % 8 + 1)] * 1024 for t in texts]

    monkeypatch.setattr(indexer, "_embed_texts", fake_embed)
    yield
    # 等待可能的后台生成线程收尾，避免污染下一个测试
    for _ in range(50):
        if not rag_service._generating:
            break
        time.sleep(0.1)


def _wait_done(c, message_id: int, timeout: float = 5.0) -> dict:
    """轮询 REST 补拉端点直至 assistant 消息落库完成（Q-14 对账口径）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = c.get(f"/api/rag/messages/{message_id}").json()
        if msg["content"]:
            return msg
        time.sleep(0.05)
    raise AssertionError("回答未在超时时间内落库")


# ---- 会话 CRUD（FR-RAG-25/26） ------------------------------------------------

def test_session_crud(client):
    c, _, _ = client
    s1 = c.post("/api/rag/sessions", json={}).json()
    assert s1["title"] == "新会话"
    s2 = c.post("/api/rag/sessions", json={"title": "复卷机排查"}).json()

    lst = c.get("/api/rag/sessions").json()
    assert [s["id"] for s in lst] == [s2["id"], s1["id"]]  # updated_at 倒序

    renamed = c.patch(f"/api/rag/sessions/{s1['id']}", json={"title": "改名"}).json()
    assert renamed["title"] == "改名"

    assert c.delete(f"/api/rag/sessions/{s2['id']}").json()["deleted"] is True
    assert len(c.get("/api/rag/sessions").json()) == 1

    # 级联删除：消息与引用一并清理（OQ-08 单事务）
    ask = c.post("/api/rag/ask", json={"session_id": s1["id"], "question": "张力",
                                       "scope": {"mode": "all"}}).json()
    _wait_done(c, ask["message_id"])
    c.delete(f"/api/rag/sessions/{s1['id']}")
    assert c.get(f"/api/rag/sessions/{s1['id']}/messages").status_code == 404
    assert c.get(f"/api/rag/citations/{ask['message_id']}").status_code == 404

    # 一键清空
    c.post("/api/rag/sessions", json={})
    assert c.delete("/api/rag/sessions").json()["cleared"] == 1


# ---- 流式 ask 与引用（Q-14 / FR-RAG-20/21） -------------------------------------

def test_first_question_auto_names_session(client, monkeypatch):
    """首次提问用问题内容自动命名会话；已有标题（含手动改名）不被覆盖。"""
    c, _, _ = client

    def fake_llm(conn, messages):
        yield "张力已通过调整 PID 恢复 [1]。"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)

    s = c.post("/api/rag/sessions", json={}).json()
    assert s["title"] == "新会话"

    ask = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "张力波动",
                                       "scope": {"mode": "all"}}).json()
    assert ask["title"] == "张力波动"
    _wait_done(c, ask["message_id"])

    lst = c.get("/api/rag/sessions").json()
    assert lst[0]["title"] == "张力波动"

    # 第二次提问不再改名（title 回 None）
    ask2 = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "再问一个别的",
                                        "scope": {"mode": "all"}}).json()
    assert ask2["title"] is None
    _wait_done(c, ask2["message_id"])
    assert c.get("/api/rag/sessions").json()[0]["title"] == "张力波动"

    # 手动改名后提问：不覆盖用户命名
    s2 = c.post("/api/rag/sessions", json={"title": "手工标题"}).json()
    ask3 = c.post("/api/rag/ask", json={"session_id": s2["id"], "question": "随便问问",
                                        "scope": {"mode": "all"}}).json()
    assert ask3["title"] is None
    _wait_done(c, ask3["message_id"])
    titles = {x["id"]: x["title"] for x in c.get("/api/rag/sessions").json()}
    assert titles[s2["id"]] == "手工标题"

    # 兼容旧会话：标题仍是默认值但已有历史 → 用会话里的第一条问题命名
    s3 = c.post("/api/rag/sessions", json={}).json()
    c.post("/api/rag/ask", json={"session_id": s3["id"], "question": "第一条问题",
                                 "scope": {"mode": "all"}})
    c.patch(f"/api/rag/sessions/{s3['id']}", json={"title": "新会话"})  # 模拟历史遗留数据
    ask4 = c.post("/api/rag/ask", json={"session_id": s3["id"], "question": "第二条不同的问题",
                                        "scope": {"mode": "all"}}).json()
    assert ask4["title"] == "第一条问题"
    _wait_done(c, ask4["message_id"])


def test_backfill_default_titles(client, monkeypatch):
    """启动补齐：仍是默认标题且有历史的会话，按第一条提问命名（幂等）。"""
    from app.db import database

    c, _, _ = client
    monkeypatch.setattr(rag_service, "_llm_stream", lambda conn, messages: iter(["资料见 [1]。"]))
    s = c.post("/api/rag/sessions", json={}).json()
    ask = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "第一条问题",
                                       "scope": {"mode": "all"}}).json()
    _wait_done(c, ask["message_id"])
    c.patch(f"/api/rag/sessions/{s['id']}", json={"title": "新会话"})  # 模拟旧数据

    empty = c.post("/api/rag/sessions", json={}).json()  # 空会话保持默认标题

    with database.get_conn() as conn:
        renamed = rag_service.backfill_default_titles(conn)
        assert renamed == 1
        assert rag_service.backfill_default_titles(conn) == 0  # 幂等

    titles = {x["id"]: x["title"] for x in c.get("/api/rag/sessions").json()}
    assert titles[s["id"]] == "第一条问题"
    assert titles[empty["id"]] == "新会话"


def test_derive_session_title():
    """标题派生：取首行、压缩空白、超长截断加省略号。"""
    d = rag_service.derive_session_title
    assert d("  张力波动  ") == "张力波动"
    assert d("第一行问题\n第二行补充") == "第一行问题"
    assert d("多余    空白") == "多余 空白"
    assert d("") == "新会话"
    long = "超长问题" * 10
    out = d(long)
    assert len(out) == rag_service.SESSION_TITLE_MAX + 1 and out.endswith("…")


def test_ask_stream_and_citations(client, monkeypatch):
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()

    frames: list[dict] = []
    broker.subscribe("rag.stream", frames.append)

    def fake_llm(conn, messages):
        # 校验 prompt 含编号资料与系统禁编造指令
        sys = messages[0]["content"]
        assert "禁止编造" in sys and "[1]" in messages[1]["content"]
        yield "根据资料，"
        yield "调整 PID 参数可解决张力问题 [1]。"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)

    ask = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "张力波动",
                                       "scope": {"mode": "all"}}).json()
    assert "message_id" in ask
    msg = _wait_done(c, ask["message_id"])

    # 流式帧：delta 逐帧 + done 收尾帧
    deltas = [f["delta"] for f in frames if f["message_id"] == ask["message_id"] and not f["done"]]
    dones = [f for f in frames if f["message_id"] == ask["message_id"] and f["done"]]
    assert "".join(deltas) == msg["content"]
    assert dones and dones[-1]["chunk_count"] >= 1

    # 引用：来源为命中的星河笔记，chunk 断链校验为 True
    cits = c.get(f"/api/rag/citations/{ask['message_id']}").json()
    assert cits and cits[0]["source_type"] == "note"
    assert "星河纸业-复卷机.md" in cits[0]["note_path"]
    assert cits[0]["chunk_valid"] is True
    assert "张力" in cits[0]["snippet"]

    # 消息回放：scope/context 快照随消息返回
    msgs = c.get(f"/api/rag/sessions/{s['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["scope"] == {"mode": "all"}
    assert msgs[1]["citations"]

    broker._subscribers.get("rag.stream", []).clear()


def test_reindex_after_citations_does_not_break_fk(client, monkeypatch):
    """引用落库后重索引笔记：应先给 rag_citation 脱钩，不触发 FK 约束错误（2026-11-26 修复）。"""
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()

    def fake_llm(conn, messages):
        yield "资料见 [1]。"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)
    ask = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "张力波动",
                                       "scope": {"mode": "all"}}).json()
    _wait_done(c, ask["message_id"])

    from app.db.database import connect

    conn = connect()
    try:
        assert conn.execute("SELECT count(*) FROM rag_citation").fetchone()[0] >= 1
    finally:
        conn.close()

    # 重索引（删旧块 → 重插）不应因外键失败
    resp = c.post("/api/kb/reindex")
    assert resp.status_code == 200
    assert resp.json()["failed"] == 0

    # 旧引用仍保留（chunk_id 置空但 snippet/source_name 在，展示不丢）
    conn = connect()
    try:
        row = conn.execute(
            "SELECT chunk_id, snippet FROM rag_citation WHERE message_id=?", (ask["message_id"],)
        ).fetchone()
        assert row is not None and row["chunk_id"] is None
    finally:
        conn.close()


def test_ask_no_hit_returns_not_found(client, monkeypatch):
    # 向量 KNN 恒有最近邻，"无命中"场景存在于 FTS-only 模式（未配置 embedding）
    monkeypatch.setattr(indexer, "_embed_texts", lambda conn, texts: None)
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()
    ask = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "量子纠缠叠加态",
                                       "scope": {"mode": "all"}}).json()
    msg = _wait_done(c, ask["message_id"])
    assert "未找到" in msg["content"]
    assert msg["citations"] == []


def test_ask_llm_not_configured(client):
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()
    ask = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "张力",
                                       "scope": {"mode": "all"}}).json()
    msg = _wait_done(c, ask["message_id"])
    assert "未配置 LLM" in msg["content"] and msg["model_id"] == "(config-error)"


# ---- 范围过滤（FR-RAG-17/18） ---------------------------------------------------

def test_scope_current_uses_reported_context(client, monkeypatch):
    # FTS-only：关键词精确命中路径下验证范围过滤（向量 KNN 恒有最近邻，不适合判"无命中"）
    monkeypatch.setattr(indexer, "_embed_texts", lambda conn, texts: None)
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()

    def fake_llm(conn, messages):
        yield "ok"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)

    # 上报上下文为星河 → current 范围下查"水冷机"（仅炬光命中）→ 范围内无依据
    rag_service.update_context({"notePath": "星河纸业-复卷机.md", "customer": "星河纸业", "project": "复卷机"})
    ask = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "水冷机",
                                       "scope": {"mode": "current"}}).json()
    msg = _wait_done(c, ask["message_id"])
    assert "未找到" in msg["content"]
    assert msg["context"]["customer"] == "星河纸业"  # 快照落库

    # 显式 customer 覆盖 context：查炬光内容可命中
    ask2 = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "水冷机",
                                        "scope": {"mode": "current", "customer": "炬光"}}).json()
    msg2 = _wait_done(c, ask2["message_id"])
    cits = msg2["citations"]
    assert cits and all("炬光" in (ct["note_path"] or "") for ct in cits)


# ---- 同会话并发 409（04 文档 3.10） ----------------------------------------------

def test_ask_conflict_while_generating(client, monkeypatch):
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()

    gate = threading.Event()

    def slow_llm(conn, messages):
        gate.wait(timeout=5)
        yield "slow answer"

    monkeypatch.setattr(rag_service, "_llm_stream", slow_llm)

    first = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "张力",
                                         "scope": {"mode": "all"}}).json()
    second = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "水冷机",
                                          "scope": {"mode": "all"}})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "state_conflict"

    gate.set()
    _wait_done(c, first["message_id"])


# ---- 混合检索单元（RRF 融合） ----------------------------------------------------

def test_retrieve_rrf_fusion(client):
    from app.db.database import connect

    conn = connect()
    try:
        result = rag_service.retrieve(conn, "张力", {"mode": "all"})
        assert result["vector_used"] is True  # fake embedding 使向量路可用
        assert result["chunks"]
        scores = [ch["rrf_score"] for ch in result["chunks"]]
        assert scores == sorted(scores, reverse=True)  # 融合分降序
        # 关键词精确命中 ACS-2600i（型号字符，FR-RAG-16）
        exact = rag_service.retrieve(conn, "ACS-2600i", {"mode": "all"})
        assert any("ACS-2600i" in ch["content"] for ch in exact["chunks"])
    finally:
        conn.close()


# ---- 多轮上下文记忆（2026-11-26 用户反馈：追问需依托对话历史） ---------------------

def test_context_memory_followup(client, monkeypatch):
    """第二轮短追问（检索无命中）→ 携带会话历史调用 LLM，而非直接"未找到"。"""
    monkeypatch.setattr(indexer, "_embed_texts", lambda conn, texts: None)  # FTS-only
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()

    captured: dict = {}

    def fake_llm(conn, messages):
        captured["messages"] = messages
        yield "已结合上文给出建议。"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)

    # 第一轮：命中"张力"，回答落库
    a1 = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "张力",
                                      "scope": {"mode": "all"}}).json()
    m1 = _wait_done(c, a1["message_id"])
    assert m1["content"]  # 第一轮回答已落库

    # 第二轮：短追问，FTS 无命中 → 走历史上下文路径
    a2 = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "建议如何做",
                                      "scope": {"mode": "all"}}).json()
    msg2 = _wait_done(c, a2["message_id"])
    assert "未找到" not in msg2["content"]

    # LLM 收到：system + 上一轮 user/assistant + 本次问题
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system" and "禁止编造" in msgs[0]["content"]
    assert msgs[1]["role"] == "user" and "张力" in msgs[1]["content"]
    assert msgs[2]["role"] == "assistant"
    assert "本次检索未命中" in msgs[3]["content"]  # 无新资料提示


def test_no_hit_first_question_still_short_circuit(client, monkeypatch):
    """首问无命中且无历史：保持短路"未找到"（不浪费 LLM 调用）。"""
    monkeypatch.setattr(indexer, "_embed_texts", lambda conn, texts: None)
    called = {"n": 0}

    def fake_llm(conn, messages):
        called["n"] += 1
        yield "should not be called"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()
    a = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "量子纠缠",
                                     "scope": {"mode": "all"}}).json()
    msg = _wait_done(c, a["message_id"])
    assert "未找到" in msg["content"] and called["n"] == 0


def test_clear_current_session_messages(client, monkeypatch):
    """清空当前会话：只清消息与引用，会话本身保留（面板"清空"按键的契约）。"""
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()

    def fake_llm(conn, messages):
        yield "资料见 [1]。"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)
    ask = c.post("/api/rag/ask", json={"session_id": s["id"], "question": "张力波动",
                                       "scope": {"mode": "all"}}).json()
    _wait_done(c, ask["message_id"])
    assert len(c.get(f"/api/rag/sessions/{s['id']}/messages").json()) == 2

    resp = c.delete(f"/api/rag/sessions/{s['id']}/messages")
    assert resp.status_code == 200 and resp.json()["cleared"] == 2

    # 消息与引用清空，会话仍在（可继续追问）
    assert c.get(f"/api/rag/sessions/{s['id']}/messages").json() == []
    assert c.get(f"/api/rag/citations/{ask['message_id']}").status_code == 404
    assert any(x["id"] == s["id"] for x in c.get("/api/rag/sessions").json())

    # 会话不存在 → 404
    assert c.delete("/api/rag/sessions/99999/messages").status_code == 404


def test_context_report_accepts_path_alias():
    """context.report 历史上报字段为 path（M2 笔记侧）：应兼容映射到 notePath。"""
    rag_service.update_context({"path": "a/b.md", "customer": "星河纸业"})
    assert rag_service.current_context() == {"notePath": "a/b.md", "customer": "星河纸业"}
    rag_service.update_context({})  # 清空
    assert rag_service.current_context() == {}


def test_scope_explicit_null_skips_stale_context(client, monkeypatch):
    """显式传 customer/project=null 不得回落到过期进程快照（面板"未感知到笔记=全库"）。

    回归场景：页面切走后后端仍留着上一篇笔记的上下文，前端已按全库发送，
    若后端强行回落会静默过滤掉其他笔记 → 表现为"切了范围查不到"。
    """
    monkeypatch.setattr(indexer, "_embed_texts", lambda conn, texts: None)
    c, _, _ = client
    s = c.post("/api/rag/sessions", json={}).json()

    def fake_llm(conn, messages):
        yield "ok"

    monkeypatch.setattr(rag_service, "_llm_stream", fake_llm)
    rag_service.update_context({"customer": "星河纸业", "project": "复卷机"})

    # 水冷机仅炬光笔记命中；若回落到星河快照则一条都拿不到
    ask = c.post(
        "/api/rag/ask",
        json={"session_id": s["id"], "question": "水冷机",
              "scope": {"mode": "current", "customer": None, "project": None}},
    ).json()
    msg = _wait_done(c, ask["message_id"])
    assert any("炬光" in (ct["note_path"] or "") for ct in msg["citations"])
    rag_service.update_context({})
