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

from app.services import config_service, indexer, reranker, textutil
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
    # manualId/Name/Page：PDF 手册页上报，让「当前笔记」在手册页落到具体这本 PDF
    snap = {
        k: payload.get(k)
        for k in ("notePath", "customer", "project", "manualId", "manualName", "manualPage")
        if payload.get(k)
    }
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


DEFAULT_SESSION_TITLE = "新会话"
SESSION_TITLE_MAX = 24


def derive_session_title(question: str) -> str:
    """用首次提问的内容作会话标题：取首行、压缩空白、超长截断。

    标题纯展示用（下拉/列表），不参与检索与排序口径。
    """
    text = (question or "").strip()
    if not text:
        return DEFAULT_SESSION_TITLE
    first_line = text.splitlines()[0]
    flat = " ".join(first_line.split())
    if not flat:
        return DEFAULT_SESSION_TITLE
    return flat if len(flat) <= SESSION_TITLE_MAX else flat[:SESSION_TITLE_MAX].rstrip() + "…"


def list_sessions(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, created_at, updated_at FROM rag_session ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def create_session(conn, title: str | None = None) -> dict:
    now = _now()
    cur = conn.execute(
        "INSERT INTO rag_session (title, created_at, updated_at) VALUES (?, ?, ?)",
        (title or DEFAULT_SESSION_TITLE, now, now),
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


def backfill_default_titles(conn) -> int:
    """把仍是默认标题的会话按「第一条提问」补齐（旧数据/重启后跑，幂等）。

    没有用户消息的空会话保持默认标题，不动。
    """
    rows = conn.execute("SELECT id FROM rag_session WHERE title=?", (DEFAULT_SESSION_TITLE,)).fetchall()
    changed = 0
    for row in rows:
        first = conn.execute(
            "SELECT content FROM rag_message WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
            (row["id"],),
        ).fetchone()
        if first is None:
            continue
        conn.execute(
            "UPDATE rag_session SET title=? WHERE id=?",
            (derive_session_title(first["content"]), row["id"]),
        )
        changed += 1
    if changed:
        conn.commit()
    return changed


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

    # PDF 手册页：当前范围 = 这一本手册。优先级高于客户/项目——手册页没有笔记上下文，
    # 若继续按 customer/project 过滤只会得到空结果。
    manual_id = scope.get("manualId")
    if manual_id is None and mode == "current":
        manual_id = current_context().get("manualId")
    if manual_id is not None and mode in ("current", "project"):
        try:
            mid = int(manual_id)
        except (TypeError, ValueError):
            mid = None
        if mid is not None:
            return lambda chunk: chunk.get("manual_id") == mid

    if mode in ("current", "project") and (not customer and not project):
        # 无客户/项目可感知 → 再看能不能落到具体某一篇笔记（笔记页的「当前笔记」）
        note_path = scope.get("notePath")
        if note_path is None and mode == "current":
            note_path = current_context().get("notePath")
        if mode == "current" and note_path:
            # 「当前笔记」= 这一篇：按 note_path 精确匹配。之前用客户名近似，
            # 同一客户的其它笔记（如安图生物的 20260825 / 20260904）会一起混进来。
            return lambda chunk: chunk.get("note_path") == note_path
        # 无上下文可感知 → 等同全库（与前端提示一致；否则会过滤掉全部块）
        return lambda chunk: True

    def pred(chunk: dict) -> bool:
        if mode == "brand":
            # 手册按 brand 字段精确匹配；笔记侧仍按客户名近似（历史口径）
            brands = scope.get("brands") or []
            if not brands:
                return False
            if chunk.get("manual_id"):
                return chunk.get("manual_brand") in brands
            return chunk.get("customer") in brands
        # current / project：客户或项目命中即纳入
        if customer and chunk.get("customer") == customer:
            return True
        if project and chunk.get("project") == project:
            return True
        return False

    return pred


def _enrich(conn, rows: list[dict]) -> list[dict]:
    """附展示字段：笔记取 customer/project，手册取名称/品牌（引用与提示词都要用）。"""
    paths = {r["note_path"] for r in rows if r["note_path"]}
    info = {}
    for p in paths:
        row = conn.execute("SELECT customer, project_name FROM note_file WHERE path=?", (p,)).fetchone()
        info[p] = (row["customer"], row["project_name"]) if row else (None, None)

    manual_ids = {r["manual_id"] for r in rows if r.get("manual_id")}
    manuals: dict[int, tuple[str | None, str | None]] = {}
    for mid in manual_ids:
        row = conn.execute("SELECT name, brand FROM manual WHERE id=?", (mid,)).fetchone()
        manuals[mid] = (row["name"], row["brand"]) if row else (None, None)

    for r in rows:
        r["customer"], r["project"] = info.get(r["note_path"], (None, None))
        r["manual_name"], r["manual_brand"] = manuals.get(r.get("manual_id"), (None, None))
    return rows


def retrieve(conn, question: str, scope: dict, top_k: int | None = None,
             min_similarity: float | None = None, rerank: dict | None = None) -> dict:
    """混合检索：FTS + 向量双路 → RRF 融合 → scope 过滤 →（可选）reranker 精排。

    top_k / min_similarity 传 None 时读设置页配置（config.retrieval）。
    min_similarity 作用于**向量路**：余弦相似度低于阈值的候选在进 RRF 前就被丢掉，
    避免"沾边就进 prompt"；阈值 0 = 不设限（默认）。
    rerank：启用时先多召回一批（rerank_top_n），用本地 reranker 精排后取 rerank_keep 条。
    """
    if top_k is None or min_similarity is None or rerank is None:
        params = config_service.retrieval_params(conn)
        top_k = params["top_k"] if top_k is None else top_k
        min_similarity = params["min_similarity"] if min_similarity is None else min_similarity
        if rerank is None:
            rerank = {
                "enabled": params["rerank_enabled"],
                "model_path": params["rerank_model_path"],
                "top_n": params["rerank_top_n"],
                "keep": params["rerank_keep"],
            }
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
                qjson = json.dumps(qvec[0])
                # 相似度阈值用 vec_distance_cosine 实测（不依赖向量是否归一化），
                # 低于阈值的候选直接不参与 RRF：宁可少给几块，也别把不相关的内容喂给模型
                rows = conn.execute(
                    "SELECT c.id, c.note_path, c.title_path, c.content, c.source_type, c.manual_id, c.page,"
                    " vec_distance_cosine(v.embedding, ?) AS cos_dist"
                    " FROM chunk_vec v JOIN chunk c ON c.id=v.rowid"
                    " WHERE v.embedding MATCH ? AND k=?",
                    (qjson, qjson, VEC_FETCH),
                ).fetchall()
                kept = []
                for r in rows:
                    item = dict(r)
                    item["similarity"] = round(1.0 - float(item["cos_dist"]), 4)
                    kept.append(item)
                if min_similarity and min_similarity > 0:
                    kept = [r for r in kept if r["similarity"] >= min_similarity]
                vec_rows = _enrich(conn, kept)
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
    # 启用 rerank 时先多召回一批候选，供 reranker 精排；否则直接按 top_k 截
    fetch_limit = max(top_k, rerank["top_n"]) if rerank.get("enabled") else top_k
    for cid, score in sorted(scores.items(), key=lambda kv: -kv[1]):
        chunk = first[cid]
        chunk["rrf_score"] = round(score, 6)
        if pred(chunk):
            merged.append(chunk)
        if len(merged) >= fetch_limit:
            break

    if rerank.get("enabled") and len(merged) > 1:
        model_path = rerank.get("model_path") or ""
        if reranker.enabled(model_path):
            before = merged
            merged = reranker.rerank(question, merged, model_path, rerank["keep"])
            return {"chunks": merged, "vector_used": vector_used, "reranked": True,
                    "rerank_candidates": len(before)}
    return {"chunks": merged, "vector_used": vector_used, "reranked": False}


# ---- Prompt 组装 --------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是售后知识库助手。回答规则：\n"
    "1. 结合下方对话历史理解用户的追问（如\"建议如何做\"指上一轮讨论的问题）；\n"
    "2. 事实依据仅来自本次提供的编号资料或对话历史中已检索过的资料；"
    "引用本次资料时在句末标注来源角标如 [1][2]（编号即资料编号）；\n"
    "3. 两方面都不足以回答时，明确回答\"知识库中未找到相关依据\"，禁止编造。"
)

HISTORY_TURNS = 10  # 默认携带的最近消息条数（约 5 轮；设置页 config.retrieval 可调，0 = 不记忆）


def _load_history(conn, session_id: int, before_id: int, limit: int | None = None) -> list[dict]:
    """会话历史（当前提问之前的消息；跳过空的 assistant 占位）。limit<=0 即完全不带历史。"""
    if limit is None:
        limit = config_service.retrieval_params(conn)["history_turns"]
    if limit <= 0:
        return []
    rows = conn.execute(
        "SELECT role, content FROM rag_message WHERE session_id=? AND id<? AND content != ''"
        " ORDER BY id DESC LIMIT ?",
        (session_id, before_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _compose_user(question: str, chunks: list[dict]) -> str:
    parts = []
    if chunks:
        parts.append("以下是本次知识库检索到的资料片段：\n")
        for i, c in enumerate(chunks, 1):
            if c.get("manual_id"):
                src = f"手册《{c.get('manual_name') or c['manual_id']}》"
                if c.get("page"):
                    src += f" 第 {c['page']} 页"
            else:
                src = c["note_path"] or "未知"
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
             c["note_path"] or (c.get("manual_name") or f"手册#{c['manual_id']}" if c["manual_id"] else "未知"),
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
    """提问入口：落 user+assistant 消息，后台线程流式生成。返回 {message_id, title}。

    - 同会话并发 → ValueError('state_conflict')（API 层转 409）；
    - 会话不存在 → KeyError（API 层转 404）；
    - title：本次自动命名后的会话标题；未改动（已有标题）时为 None。
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

    # 会话标题自动命名：仍是默认标题时，用会话里的第一条提问命名（用户手动改过的标题不动）。
    # 取"第一条"而非本次问题：清空消息后重建、或历史会话一直是默认标题时，口径仍是首次输入。
    auto_title = None
    row = conn.execute("SELECT title FROM rag_session WHERE id=?", (session_id,)).fetchone()
    if row is not None and row["title"] == DEFAULT_SESSION_TITLE:
        first = conn.execute(
            "SELECT content FROM rag_message WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
            (session_id,),
        ).fetchone()
        auto_title = derive_session_title(first["content"] if first is not None else question)
        conn.execute("UPDATE rag_session SET title=? WHERE id=?", (auto_title, session_id))

    conn.commit()

    threading.Thread(
        target=_generate_worker, daemon=True, name=f"rag-gen-{message_id}",
        args=(conn_factory, session_id, message_id, user_message_id, question, scope, ctx_snap),
    ).start()
    return {"message_id": message_id, "title": auto_title}


def _generate_worker(conn_factory, session_id: int, message_id: int, user_message_id: int,
                     question: str, scope: dict, ctx_snap: dict) -> None:
    """后台生成（Q-14）：检索 → 会话历史组装 → LLM 流式 → rag.stream 帧推送 → 落库。"""
    try:
        conn = conn_factory()
        try:
            params = config_service.retrieval_params(conn)
            result = retrieve(conn, question, scope, top_k=params["top_k"],
                              min_similarity=params["min_similarity"])
            chunks = result["chunks"]
            if result.get("reranked"):
                logger.info("reranker 精排：%d 候选 → 取 %d 条", result.get("rerank_candidates", 0), len(chunks))
            history = _load_history(conn, session_id, user_message_id, params["history_turns"])

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
