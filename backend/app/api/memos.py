"""随手笔记（「1」顶栏入口，M2 扩展）：/api/memos/*。

与售后笔记（/api/notes/*）分离：
- 随手笔记写到电脑任意绝对路径（不受 vault_root 限制），简单格式（标题 + 正文）；
- 复用 note_file 表，以 note_type='memo' 区分；读写复用 vault 的 path-based 接口
  （绝对路径主键 + 白名单目录机制）。
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import vault
from app.api.notes import _map_common  # 复用售后笔记的异常映射

router = APIRouter(prefix="/api/memos", tags=["memos"])


@router.get("")
def list_memos(
    keyword: str | None = Query(None),
    conn: Connection = Depends(get_db),
) -> list[dict]:
    """随手笔记列表：仅 note_type='memo' 的记录（绝对路径主键）。

    草稿箱（右侧 dock 的速记）虽然物理上可能位于个人笔记目录下，但那是写到一半的纸，
    不建 note_file 行；这里再挡两道（路径里的 草稿箱/ + 实际草稿目录判定），
    避免历史残留行混进个人笔记列表。
    """
    vault.rescan_all(conn)
    sql = "SELECT * FROM note_file WHERE note_type = 'memo' AND path NOT LIKE ?"
    params: list = [f"%/{vault.DRAFT_DIR_NAME}/%"]
    if keyword:
        like = f"%{keyword}%"
        sql += " AND (note_title LIKE ? OR summary LIKE ? OR path LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY updated_date DESC, path"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return [r for r in rows if not vault.is_draft_path(conn, r["path"])]


class CreateMemoBody(BaseModel):
    title: str = Field(min_length=1)
    body: str = ""
    # 留空 = 写「随手笔记索引位置」的第 1 个（主位置）
    folder: str = ""


@router.post("", status_code=201)
def create_memo(body: CreateMemoBody, conn: Connection = Depends(get_db)) -> dict:
    """创建随手笔记：写到任意绝对路径目录，简单格式。"""
    try:
        return vault.create_memo(conn, body.title.strip(), body.body, body.folder.strip())
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail={"code": "path_conflict", "message": "同名笔记已存在"}) from exc
    except (NotADirectoryError, FileNotFoundError) as exc:
        raise _map_common(exc, "path_invalid") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "bad_request", "message": str(exc)}) from exc
