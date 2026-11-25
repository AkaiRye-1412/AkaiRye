"""RAG 问答服务（C1 · FR-RAG-16~22）：混合检索 + RRF 融合 + 流式生成 + 引用落库。

依据：
- 02 文档管线 (3)：FTS5 关键词 + sqlite-vec 向量双路 → RRF 融合 → scope 过滤（SQL）→
  LLM 生成带来源角标；无命中明确"未找到"禁止编造（FR-RAG-20）；
- 04 文档 Q-14：POST /api/rag/ask 立即返回 message_id，token 流经 WS rag.stream 推送；
  done=true 后前端 REST 补拉完整回答与引用；
- 04 文档 3.10 业务约束：同会话不允许并发提问（409 state_conflict）；
- R4：embedding 未配置时向量侧自动缺席（FTS-only 降级，与 indexer 一致）；
- FR-RAG-18：context.report 上报的"当前笔记"上下文用于 current 范围判定。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from app.services import config_service, indexer, textutil
from app.ws.events import broker

logger = logging.getLogger(__name__)

RRF_K = 60          # RRF 常数（业界默认）
FTS_FETCH = 50      # 每路候选过取量（先过取再过滤 scope，避免 SQL 复杂化）
VEC_FETCH = 50
DEFAULT_TOP_K = 8   # 进 prompt 的片段数（FR-RAG-22 Top-K 展示同源）

# 同会话并发防护（04 文档 3.10）：单进程单用户，set 足够
_generating: set[int] = set()
_gen_lock = threading.Lock()

# 自动感知上下文（FR-RAG-18）：WS context.report 上报的最新快照
_latest_context: dict = {}
_ctx_lock = threading.Lock()


# ---- 上下文（FR-RAG-18） ----------------------------------------------------

def update_context(payload: dict) -> None:
    """WS context.report 入口（events_ws 转发）；只保留已知键。

    前端历史上报字段名为 path（M2 笔记侧），此处兼容 path → notePath，
    避免上下文里永远缺笔记路径。
    """
    payload = dict(payload or {})
    if not payload.get("notePath") and payload.get("path"):
        payload["notePath"] = payload["path"]
    snap = {k: payload.get(k) for k in ("notePath", "customer", "project") if payload.get(k)}
    with _ctx_lock:
        _latest_context.clear()
        _latest_context.update(snap)
    broker.publish("context.report", snap)


def current_context() -> dict:
    with _ctx_lock:
        return dict(_latest_context)


# ---- 会话 CRUD（FR-RAG-25/26，OQ-08 永久保留） --------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_sessions(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, created_at, updated_at FROM rag_session ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def create_session(conn, title: str | None = None) -> dict:
    now = _now()
    cur = conn.execute(
        "INSERT INTO rag_session (title, created_at, updated_at) VALUES (?, ?, ?)",
        (title or "新会话", now, now),
    )
    conn.commit()
    row = conn.execute("SELECT id, title, created_at, updated_at FROM rag_session WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def rename_session(conn, session_id: int, title: str) -> dict | None:
    cur = conn.execute(
        "UPDATE rag_session SET title=?, updated_at=? WHERE id=?", (title, _now(), session_id)
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    row = conn.execute("SELECT id, title, created_at, updated_at FROM rag_session WHERE id=?", (session_id,)).fetchone()
    return dict(row)


def _delete_session_tx(conn, session_id: int) -> bool:
    """单事务 citation → message → session（api-rules 禁级联，OQ-08）。"""
    conn.execute(
        "DELETE FROM rag_citation WHERE message_id IN (SELECT id FROM rag_message WHERE session_id=?)",
        (session_id,),
    )
    conn.execute("DELETE FROM rag_message WHERE session_id=?", (session_id,))
    cur = conn.execute("DELETE FROM rag_session WHERE id=?", (session_id,))
    return cur.rowcount > 0


def delete_session(conn, session_id: int) -> bool:
    ok = _delete_session_tx(conn, session_id)
    conn.commit()
    return ok


def clear_sessions(conn) -> int:
    ids = [r["id"] for r in conn.execute("SELECT id FROM rag_session").fetchall()]
    for sid in ids:
        _delete_session_tx(conn, sid)
    conn.commit()
    return len(ids)


def clear_session_messages(conn, session_id: int) -> int | None:
    """清空单个会话的全部消息（保留会话本身）；会话不存在返回 None。

    与删除会话同口径：citation → message 顺序删除（api-rules 禁级联），
    会话 updated_at 刷新后仍可按最近活跃排序。
    """
    if conn.execute("SELECT 1 FROM rag_session WHERE id=?", (session_id,)).fetchone() is None:
        return None
    conn.execute(
        "DELETE FROM rag_citation WHERE message_id IN (SELECT id FROM rag_message WHERE session_id=?)",
        (session_id,),
    )
    cur = conn.execute("DELETE FROM rag_message WHERE session_id=?", (session_id,))
    conn.execute("UPDATE rag_session SET updated_at=? WHERE id=?", (_now(), session_id))
    conn.commit()
    return cur.rowcount


def session_messages(conn, session_id: int) -> list[dict]:
    """会话消息（含每条 assistant 消息的 citations 数组，供回放）。"""
    msgs = conn.execute(
        "SELECT id, session_id, role, content, scope_json, context_json, model_id, created_at"
        " FROM rag_message WHERE session_id=? ORDER BY id",
        (session_id,),
    ).fetchall()
    result = []
    for m in msgs:
        d = dict(m)
        d["scope"] = json.loads(d.pop("scope_json")) if d["scope_json"] else None
        d["context"] = json.loads(d.pop("context_json")) if d["context_json"] else None
        if d["role"] == "assistant":
            d["citations"] = citations_of_message(conn, d["id"])
        result.append(d)
    return result


def citations_of_message(conn, message_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT seq, source_type, source_name, chapter, page, snippet, note_path, manual_id, chunk_id"
        " FROM rag_citation WHERE message_id=? ORDER BY seq",
        (message_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # chunk 断链标记（FR-RAG-23）：chunk_id 存在但块已被重建/删除 → False
        d["chunk_valid"] = bool(d["chunk_id"]) and conn.execute(
            "SELECT 1 FROM chunk WHERE id=?", (d["chunk_id"],)
        ).fetchone() is not None
        out.append(d)
    return out


# ---- 混合检索（FR-RAG-16/17） --------------------------------------------------

def _scope_predicate(conn, scope: dict) -> object:
    """返回 chunk 行 → bool 的范围判定函数（FR-RAG-17 四种模式）。"""
    mode = scope.get("mode") or "all"
    if mode == "all":
        return lambda chunk: True

    customer = scope.get("customer")
    project = scope.get("project")
    # 前端显式传了 customer/project（即使是 null）→ 以它为准，不再回落到进程级快照：
    # 页面切走后快照可能已过期，回落会导致"范围内查不到"或"范围外的结果"。
    explicit = "customer" in scope or "project" in scope
    if mode in ("current", "project") and not explicit:
        ctx = current_context()
        customer, project = ctx.get("customer"), ctx.get("project")
    if mode in ("current", "project") and (not customer and not project):
        # 无上下文可感知 → 等同全库（与前端提示一致；否则会过滤掉全部块）
        return lambda chunk: True

    def pred(chunk: dict) -> bool:
        if mode == "brand":
            # 品牌维度属手册链路（二期）；笔记侧按客户名近似匹配枚举
            brands = scope.get("brands") or []
            return bool(brands) and chunk.get("customer") in brands
        # current / project：客户或项目命中即纳入
        if customer and chunk.get("customer") == customer:
            return True
        if project and chunk.get("project") == project:
            return True
        return False

    return pred


def _enrich(conn, rows: list[dict]) -> list[dict]:
    """附 customer/project（范围过滤与展示需要），来自 note_file。"""
    paths = {r["note_path"] for r in rows if r["note_path"]}
    info = {}
    for p in paths:
        row = conn.execute("SELECT customer, project_name FROM note_file WHERE path=?", (p,)).fetchone()
        info[p] = (row["customer"], row["project_name"]) if row else (None, None)
    for r in rows:
        r["customer"], r["project"] = info.get(r["note_path"], (None, None))
    return rows


def retrieve(conn, question: str, scope: dict, top_k: int = DEFAULT_TOP_K) -> dict:
    """混合检索：FTS + 向量双路 → RRF 融合 → scope 过滤。返回 {chunks, vector_used}。"""
    pred = _scope_predicate(conn, scope)

    # 关键词路（FTS5，V2 spike phrase_expr：短语邻接 + token 精确）
    fts_rows: list[dict] = []
    expr = textutil.phrase_expr(question)
    if expr:
        rows = conn.execute(
            "SELECT c.id, c.note_path, c.title_path, c.content, c.source_type, c.manual_id, c.page"
            " FROM chunk_fts f JOIN chunk c ON c.id=f.chunk_id"
            " WHERE chunk_fts MATCH ? ORDER BY rank LIMIT ?",
            (expr, FTS_FETCH),
        ).fetchall()
        fts_rows = _enrich(conn, [dict(r) for r in rows])

    # 向量路（embedding 已配置且库中存在向量时启用；R4 维度一致性由 model_id 保证）
    vec_rows: list[dict] = []
    vector_used = False
    has_vec = conn.execute("SELECT 1 FROM chunk_vec LIMIT 1").fetchone() is not None
    if has_vec:
        try:
            qvec = indexer._embed_texts(conn, [question])  # 未配置返回 None（FTS-only 降级）
            if qvec:
                vector_used = True
                rows = conn.execute(
                    "SELECT c.id, c.note_path, c.title_path, c.content, c.source_type, c.manual_id, c.page"
                    " FROM chunk_vec v JOIN chunk c ON c.id=v.rowid"
                    " WHERE v.embedding MATCH ? AND k=?",
                    (json.dumps(qvec[0]), VEC_FETCH),
                ).fetchall()
                vec_rows = _enrich(conn, [dict(r) for r in rows])
        except Exception as e:  # noqa: BLE001 —— 向量路失败降级为纯关键词（不阻断问答）
            logger.warning("向量检索失败，降级纯关键词：%s", e)

    # RRF 融合（去重：同一 chunk 取两路中较优排名叠加）
    scores: dict[int, float] = {}
    first: dict[int, dict] = {}
    for ranked in (fts_rows, vec_rows):
        for rank, chunk in enumerate(ranked):
            cid = chunk["id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            first.setdefault(cid, chunk)

    merged = []
    for cid, score in sorted(scores.items(), key=lambda kv: -kv[1]):
        chunk = first[cid]
        chunk["rrf_score"] = round(score, 6)
        if pred(chunk):
            merged.append(chunk)
        if len(merged) >= top_k:
            break
    return {"chunks": merged, "vector_used": vector_used}


# ---- Prompt 组装 --------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是售后知识库助手。回答规则：\n"
    "1. 结合下方对话历史理解用户的追问（如\"建议如何做\"指上一轮讨论的问题）；\n"
    "2. 事实依据仅来自本次提供的编号资料或对话历史中已检索过的资料；"
    "引用本次资料时在句末标注来源角标如 [1][2]（编号即资料编号）；\n"
    "3. 两方面都不足以回答时，明确回答\"知识库中未找到相关依据\"，禁止编造。"
)

HISTORY_TURNS = 10  # 携带的最近消息条数（约 5 轮对话，个人规模足够）


def _load_history(conn, session_id: int, before_id: int) -> list[dict]:
    """会话历史（当前提问之前的消息；跳过空的 assistant 占位）。"""
    rows = conn.execute(
        "SELECT role, content FROM rag_message WHERE session_id=? AND id<? AND content != ''"
        " ORDER BY id DESC LIMIT ?",
        (session_id, before_id, HISTORY_TURNS),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _compose_user(question: str, chunks: list[dict]) -> str:
    parts = []
    if chunks:
        parts.append("以下是本次知识库检索到的资料片段：\n")
        for i, c in enumerate(chunks, 1):
            src = c["note_path"] or (f"手册#{c['manual_id']}" if c["manual_id"] else "未知")
            title = f" · {c['title_path']}" if c["title_path"] else ""
            parts.append(f"[{i}] 来源：{src}{title}\n{c['content']}\n")
    else:
        parts.append("（本次检索未命中新资料，请结合对话历史回答；历史中的资料不足以回答时，"
                     "明确说明\"知识库中未找到相关依据\"。）")
    parts.append(f"\n用户问题：{question}")
    return "\n".join(parts)


def _build_prompt(question: str, chunks: list[dict], history: list[dict] | None = None) -> list[dict]:
    """多轮 prompt：system（溯源规则）+ 会话历史（上下文记忆）+ 本次资料与问题。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": _compose_user(question, chunks)})
    return messages


# ---- 提问与流式生成（Q-14） -----------------------------------------------------

def _llm_stream(conn, messages: list[dict]):
    """OpenAI 兼容流式调用；未配置抛 ValueError。返回迭代器（delta 文本）。"""
    cfg = config_service.get_raw(conn, "llm")
    if not cfg.get("base_url") or not cfg.get("model"):
        raise ValueError("未配置 LLM 服务，请在设置页填写 base_url 与模型名")
    from openai import OpenAI

    client = OpenAI(base_url=cfg["base_url"], api_key=cfg.get("api_key") or "sk-empty")
    stream = client.chat.completions.create(
        model=cfg["model"],
        messages=messages,
        temperature=cfg.get("temperature", 0.7),
        stream=True,
    )
    for event in stream:
        if event.choices and event.choices[0].delta and event.choices[0].delta.content:
            yield event.choices[0].delta.content


def _insert_citations(conn, message_id: int, chunks: list[dict]) -> None:
    for seq, c in enumerate(chunks, 1):
        conn.execute(
            "INSERT INTO rag_citation (message_id, seq, source_type, source_name, chapter, page,"
            " snippet, note_path, manual_id, chunk_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, seq, c["source_type"],
             c["note_path"] or (f"manual#{c['manual_id']}" if c["manual_id"] else "未知"),
             c["title_path"], c["page"], c["content"][:200],
             c["note_path"], c["manual_id"], c["id"]),
        )


def _finish_message(conn, message_id: int, session_id: int, content: str, model_id: str) -> None:
    conn.execute(
        "UPDATE rag_message SET content=?, model_id=? WHERE id=?", (content, model_id, message_id)
    )
    conn.execute("UPDATE rag_session SET updated_at=? WHERE id=?", (_now(), session_id))
    conn.commit()


def ask(conn, session_id: int, question: str, scope: dict, conn_factory) -> dict:
    """提问入口：落 user+assistant 消息，后台线程流式生成。返回 {message_id}。

    - 同会话并发 → ValueError('state_conflict')（API 层转 409）；
    - 会话不存在 → KeyError（API 层转 404）。
    """
    if conn.execute("SELECT 1 FROM rag_session WHERE id=?", (session_id,)).fetchone() is None:
        raise KeyError(session_id)
    with _gen_lock:
        if session_id in _generating:
            raise ValueError("state_conflict")
        _generating.add(session_id)

    now = _now()
    ctx_snap = current_context()
    cur = conn.execute(
        "INSERT INTO rag_message (session_id, role, content, scope_json, context_json, created_at)"
        " VALUES (?, 'user', ?, ?, ?, ?)",
        (session_id, question, json.dumps(scope, ensure_ascii=False), json.dumps(ctx_snap, ensure_ascii=False), now),
    )
    a_cur = conn.execute(
        "INSERT INTO rag_message (session_id, role, content, scope_json, context_json, created_at)"
        " VALUES (?, 'assistant', '', ?, ?, ?)",
        (session_id, json.dumps(scope, ensure_ascii=False), json.dumps(ctx_snap, ensure_ascii=False), now),
    )
    message_id = a_cur.lastrowid
    user_message_id = cur.lastrowid
    conn.commit()

    threading.Thread(
        target=_generate_worker, daemon=True, name=f"rag-gen-{message_id}",
        args=(conn_factory, session_id, message_id, user_message_id, question, scope, ctx_snap),
    ).start()
    return {"message_id": message_id}


def _generate_worker(conn_factory, session_id: int, message_id: int, user_message_id: int,
                     question: str, scope: dict, ctx_snap: dict) -> None:
    """后台生成（Q-14）：检索 → 会话历史组装 → LLM 流式 → rag.stream 帧推送 → 落库。"""
    try:
        conn = conn_factory()
        try:
            result = retrieve(conn, question, scope)
            chunks = result["chunks"]
            history = _load_history(conn, session_id, user_message_id)

            if not chunks and not history:
                # 首问且无命中：无上下文可依托，直接明确未找到（不浪费 LLM 调用）
                _finish_message(conn, message_id, session_id,
                                "知识库中未找到相关依据。请尝试调整提问范围或换用其他关键词。", "(no-hit)")
                broker.publish("rag.stream", {"message_id": message_id, "delta": "", "done": True})
                return

            messages = _build_prompt(question, chunks, history)
            model_id = config_service.get_raw(conn, "llm").get("model") or "(unknown)"
            acc: list[str] = []
            try:
                for delta in _llm_stream(conn, messages):
                    acc.append(delta)
                    broker.publish("rag.stream", {"message_id": message_id, "delta": delta, "done": False})
            except ValueError as e:
                _finish_message(conn, message_id, session_id, f"生成失败：{e}", "(config-error)")
                broker.publish("rag.stream", {"message_id": message_id, "delta": "", "done": True})
                return
            except Exception as e:  # noqa: BLE001 —— 模型调用失败（04：model_error 落库可见）
                logger.exception("RAG 生成失败")
                _finish_message(conn, message_id, session_id, f"生成失败（model_error）：{e}", model_id)
                broker.publish("rag.stream", {"message_id": message_id, "delta": "", "done": True})
                return

            content = "".join(acc)
            conn.execute("BEGIN")
            _insert_citations(conn, message_id, chunks)
            conn.commit()
            _finish_message(conn, message_id, session_id, content, model_id)
            broker.publish("rag.stream", {"message_id": message_id, "delta": "", "done": True,
                                          "chunk_count": len(chunks)})
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 —— 兜底：任何后台异常都要释放并发标记并收尾
        logger.exception("RAG 后台生成任务异常")
        try:
            conn = conn_factory()
            try:
                _finish_message(conn, message_id, session_id, "生成失败（model_error）：内部错误", "(error)")
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            logger.exception("RAG 失败兜底落库也失败")
        broker.publish("rag.stream", {"message_id": message_id, "delta": "", "done": True})
    finally:
        with _gen_lock:
            _generating.discard(session_id)


def get_message(conn, message_id: int) -> dict | None:
    m = conn.execute(
        "SELECT id, session_id, role, content, scope_json, context_json, model_id, created_at"
        " FROM rag_message WHERE id=?",
        (message_id,),
    ).fetchone()
    if m is None:
        return None
    d = dict(m)
    d["scope"] = json.loads(d.pop("scope_json")) if d["scope_json"] else None
    d["context"] = json.loads(d.pop("context_json")) if d["context_json"] else None
    if d["role"] == "assistant":
        d["citations"] = citations_of_message(conn, d["id"])
    return d
