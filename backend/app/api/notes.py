"""售后笔记（04 文档 3.5：/api/notes/*，M2 · FR-NOTE）。"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import indexer, vault
from app.ws.events import broker

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _to_409(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "edit_conflict", "message": "文件已被外部修改，请选择：重新加载 / 用我的覆盖 / 另存副本"})


def _map_common(exc: Exception, not_found: str = "note_not_found") -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail={"code": not_found, "message": "笔记不存在"})
    if isinstance(exc, vault.PathOutsideRoot):
        return HTTPException(status_code=400, detail={"code": "path_outside_root", "message": "路径越出配置根目录"})
    if isinstance(exc, NotADirectoryError):
        return HTTPException(status_code=400, detail={"code": "path_invalid", "message": "vault 路径未配置或目录不存在，请先在系统设置中配置 vault_root"})
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail={"code": "bad_request", "message": str(exc)})
    raise exc


class NoteListFilters:
    pass


@router.get("")
def list_notes(
    customer: str | None = Query(None),
    project: str | None = Query(None),
    status: str | None = Query(None),
    head_ok: bool | None = Query(None),
    indexed_status: str | None = Query(None),
    conn: Connection = Depends(get_db),
) -> list[dict]:
    """笔记列表（字段即 note_file 列；先增量重扫保证与磁盘一致）。

    外部知识库白名单目录的笔记（绝对路径主键）不属于售后笔记，不在此列表。
    """
    vault.rescan_all(conn)
    from pathlib import Path

    sql = "SELECT * FROM note_file WHERE 1=1"
    params: list = []
    if customer:
        sql += " AND customer = ?"
        params.append(customer)
    if project:
        sql += " AND project_name = ?"
        params.append(project)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if head_ok is not None:
        sql += " AND head_ok = ?"
        params.append(1 if head_ok else 0)
    if indexed_status:
        sql += " AND indexed_status = ?"
        params.append(indexed_status)
    sql += " ORDER BY updated_date DESC, path"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return [r for r in rows if not Path(r["path"]).is_absolute()]


class CreateNoteBody(BaseModel):
    customer: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    start_date: str | None = None  # YYYY-MM-DD，缺省取今天


@router.post("", status_code=201)
def create_note(body: CreateNoteBody, conn: Connection = Depends(get_db)) -> dict:
    """FR-NOTE-02/08/10：按命名规范落 vault，重名 409 path_conflict。"""
    start = body.start_date or date.today().isoformat()
    try:
        return vault.create_note(conn, body.customer.strip(), body.project_name.strip(), start)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail={"code": "path_conflict", "message": "同名笔记已存在（一项目一笔记）"}) from exc
    except (NotADirectoryError, FileNotFoundError) as exc:
        raise _map_common(exc, "vault_not_configured") from exc


class SaveNoteBody(BaseModel):
    content: str
    base_hash: str | None = None  # 客户端加载时的 content_hash；缺失视为未知基线


class MetaBody(BaseModel):
    """头部字段更新（FR-NOTE-03/11）：只改 frontmatter，正文不动。"""

    customer: str | None = None
    project_name: str | None = None
    status: str | None = None  # active | archived
    start_date: str | None = None  # YYYY-MM-DD
    base_hash: str | None = None
    force: bool = False


@router.patch("/{path:path}/meta")
def patch_meta(path: str, body: MetaBody, conn: Connection = Depends(get_db)) -> dict:
    """更新客户 / 项目名 / 状态 / 开始日期；冲突语义同保存（409 edit_conflict）。"""
    updates = {
        "customer": body.customer,
        "project_name": body.project_name,
        "status": body.status,
        "start_date": body.start_date,
    }
    try:
        return vault.update_meta(conn, path, updates, body.base_hash, body.force)
    except vault.EditConflict as exc:
        raise _to_409(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _map_common(exc) from exc


@router.put("/{path:path}")
def save_note(
    path: str,
    body: SaveNoteBody,
    force: bool = Query(False),
    reload: bool = Query(False, alias="reload"),
    conn: Connection = Depends(get_db),
) -> dict:
    """保存内容（FR-NOTE-05 冲突三选）：

    - 常规保存：带 base_hash，外部已修改 → 409 edit_conflict；
    - `?force=true` = 「用我的覆盖」；
    - `?reload=true` = 「重新加载」，返回服务器当前内容供编辑器替换。
    """
    if reload:
        try:
            return vault.read_note(conn, path)
        except Exception as exc:  # noqa: BLE001
            raise _map_common(exc) from exc
    try:
        row = vault.save_note(conn, path, body.content, body.base_hash, force)
    except vault.EditConflict as exc:
        raise _to_409(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _map_common(exc) from exc
    return {"saved": True, "note": row, "content_hash": row["content_hash"]}


class SaveAsBody(BaseModel):
    content: str
    suggested_path: str | None = None  # vault 内相对路径（可含目录），冲突时自动加后缀


@router.post("/{path:path}/save-as", status_code=201)
def save_as(path: str, body: SaveAsBody, conn: Connection = Depends(get_db)) -> dict:
    """冲突三选之三：另存副本，不动原文件（FR-NOTE-05）。"""
    try:
        return vault.save_as(conn, path, body.content, body.suggested_path)
    except Exception as exc:  # noqa: BLE001
        raise _map_common(exc) from exc


@router.get("/{path:path}/history")
def note_history(path: str, conn: Connection = Depends(get_db)) -> dict:
    """外部修改检测：磁盘现状 vs 缓存基线比对，供编辑器打开前冲突提示。"""
    try:
        info = vault.read_note(conn, path)
    except Exception as exc:  # noqa: BLE001
        raise _map_common(exc) from exc
    cached = conn.execute(
        "SELECT content_hash, file_mtime FROM note_file WHERE path=?", (path,)
    ).fetchone()
    cached_hash = cached["content_hash"] if cached else None
    return {
        "path": path,
        "mtime": info["mtime"],
        "content_hash": info["content_hash"],
        "externally_modified": bool(cached_hash and cached_hash != info["content_hash"]),
    }


@router.post("/{path:path}/reindex")
def reindex_note(path: str, conn: Connection = Depends(get_db)) -> dict:
    """手动重建该笔记索引（M3）：同步触发，返回索引结果；失败含 last_error 可见。"""
    try:
        vault.safe_rel_path(conn, path)
    except vault.PathOutsideRoot as exc:
        raise HTTPException(status_code=400, detail={"code": "path_outside_root", "message": "路径越出配置根目录"}) from exc
    vault.rescan_all(conn)
    result = indexer.index_note(conn, path, force=True)
    conn.commit()
    if result["status"] == "missing":
        raise HTTPException(status_code=404, detail={"code": "note_not_found", "message": "笔记不存在"})
    broker.publish("index.updated", {"note_path": path, "status": result["status"], "chunks": result["chunks"]})
    return result


@router.delete("/{path:path}")
def delete_note(path: str, conn: Connection = Depends(get_db)) -> dict:
    """删除笔记（FR-NOTE-12）：移入 vault 根目录 `.trash/`，可从 Obsidian 还原，不做物理抹除。"""
    try:
        return vault.delete_note(conn, path)
    except Exception as exc:  # noqa: BLE001
        raise _map_common(exc) from exc


# 注意：GET 内容路由必须最后注册（path:path 会吞掉 /history 等子路径）
@router.get("/{path:path}")
def get_note(path: str, conn: Connection = Depends(get_db)) -> dict:
    """笔记内容：Markdown 原文 + 头部解析结果（head_ok、缺失字段清单，04 文档 3.5）。"""
    try:
        return vault.read_note(conn, path)
    except Exception as exc:  # noqa: BLE001
        raise _map_common(exc) from exc
