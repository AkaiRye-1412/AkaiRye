"""RAG 会话与问答（04 文档 3.10：C1；Q-14 流式通道 A）。

契约要点：
- 提问走 REST（POST /ask 立即返回 message_id），token 流经 WS rag.stream 推送；
- 同会话并发提问 → 409 state_conflict（04 文档 3.10 业务约束）；
- 删除单事务 citation → message → session（api-rules 禁级联，OQ-08）；
- 补拉端点供断线重连兜底（推送不作唯一数据来源）。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlite3 import Connection

from app.api.deps import get_db
from app.db import database
from app.services import rag_service

router = APIRouter(prefix="/api/rag", tags=["rag"])


class SessionBody(BaseModel):
    title: str | None = None


class AskBody(BaseModel):
    session_id: int
    question: str
    scope: dict = {}


@router.get("/sessions")
def list_sessions(conn: Connection = Depends(get_db)) -> list[dict]:
    """会话列表，按 updated_at 倒序（FR-RAG-25）。"""
    return rag_service.list_sessions(conn)


@router.post("/sessions", status_code=201)
def create_session(body: SessionBody | None = None, conn: Connection = Depends(get_db)) -> dict:
    return rag_service.create_session(conn, body.title if body else None)


@router.patch("/sessions/{session_id}")
def rename_session(session_id: int, body: SessionBody, conn: Connection = Depends(get_db)) -> dict:
    result = rag_service.rename_session(conn, session_id, body.title or "新会话")
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "会话不存在"})
    return result


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, conn: Connection = Depends(get_db)) -> dict:
    """单事务 citation → message → session（OQ-08）。"""
    if not rag_service.delete_session(conn, session_id):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "会话不存在"})
    return {"deleted": True, "id": session_id}


@router.delete("/sessions")
def clear_sessions(conn: Connection = Depends(get_db)) -> dict:
    """一键清空（二次确认由前端负责）。"""
    return {"cleared": rag_service.clear_sessions(conn)}


@router.delete("/sessions/{session_id}/messages")
def clear_session_messages(session_id: int, conn: Connection = Depends(get_db)) -> dict:
    """清空当前会话消息（保留会话本身，FR-RAG-25 的"清空"语义）。"""
    cleared = rag_service.clear_session_messages(conn, session_id)
    if cleared is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "会话不存在"})
    return {"cleared": cleared, "session_id": session_id}


@router.get("/sessions/{session_id}/messages")
def session_messages(session_id: int, conn: Connection = Depends(get_db)) -> list[dict]:
    """会话消息（含 citations 数组与 scope/context 快照，供回放）。"""
    if conn.execute("SELECT 1 FROM rag_session WHERE id=?", (session_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "会话不存在"})
    return rag_service.session_messages(conn, session_id)


@router.post("/ask")
def ask(body: AskBody, conn: Connection = Depends(get_db)) -> dict:
    """提问（Q-14 核心）：立即返回 {message_id, title}，token 流经 WS rag.stream。

    title：会话仍是默认标题「新会话」时，后端用本次提问内容自动命名并回传该标题，
    前端据此直接刷新下拉里的当前会话名（无需再拉一次会话列表）；已有标题时为 null。
    """
    if not body.question.strip():
        raise HTTPException(status_code=422, detail={"code": "bad_request", "message": "问题不能为空"})
    try:
        return rag_service.ask(conn, body.session_id, body.question.strip(), body.scope, database.connect)
    except KeyError:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "会话不存在"}) from None
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail={"code": "state_conflict", "message": "该会话已有回答生成中，请等待完成后再提问"},
        ) from None


@router.get("/messages/{message_id}")
def get_message(message_id: int, conn: Connection = Depends(get_db)) -> dict:
    """补拉单条回答（含 content 全文 + citations；断线重连兜底，Q-14）。"""
    result = rag_service.get_message(conn, message_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "消息不存在"})
    return result


@router.get("/citations/{message_id}")
def get_citations(message_id: int, conn: Connection = Depends(get_db)) -> list[dict]:
    """引用明细；chunk 断链时 chunk_valid:false（展示仍用冗余字段，FR-RAG-23）。"""
    if rag_service.get_message(conn, message_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "消息不存在"})
    return rag_service.citations_of_message(conn, message_id)
