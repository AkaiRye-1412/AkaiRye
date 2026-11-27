"""备份（04 文档 3.11：/api/backups，OQ-10）。"""

from fastapi import APIRouter, Depends
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import backup as backup_service

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
def list_backups(conn: Connection = Depends(get_db)) -> list[dict]:
    rows = conn.execute(
        "SELECT id, kind, file_path, size, status, created_at FROM backup_run ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_backup(conn: Connection = Depends(get_db)) -> dict:
    result = backup_service.run_backup(conn)
    backup_service.rotate(conn, backup_service.keep_count(conn))
    return result
