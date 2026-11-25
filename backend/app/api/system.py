"""系统状态（04 文档 3.0：GET /api/system/status，前端启动健康检查）。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlite3 import Connection

from app.api.deps import get_db
from app.core import paths

router = APIRouter(prefix="/api/system", tags=["system"])


class SystemStatus(BaseModel):
    db_ready: bool
    config_complete: bool        # 必备项：llm/embedding/paths.vault_root 是否已配置
    missing_config_keys: list[str]
    vault_root_valid: bool
    pdf_root_valid: bool
    counts: dict[str, int]       # 笔记/手册 ready 数等索引概况
    last_backup_at: str | None
    ws_available: bool


def _dir_valid(p: str) -> bool:
    from pathlib import Path

    return bool(p) and Path(p).is_dir()


@router.get("/status", response_model=SystemStatus)
def status(conn: Connection = Depends(get_db)) -> SystemStatus:
    from app.services.config_service import load_all

    cfg = load_all(conn)
    llm_ok = bool(cfg["llm"].get("api_key_set"))
    emb_ok = bool(cfg["embedding"].get("api_key_set"))
    vault_ok = _dir_valid(cfg["paths"].get("vault_root", ""))
    missing = []
    if not llm_ok:
        missing.append("llm")
    if not emb_ok:
        missing.append("embedding")
    if not vault_ok:
        missing.append("paths.vault_root")

    note_count = conn.execute("SELECT COUNT(*) c FROM note_file").fetchone()["c"]
    manual_ready = conn.execute("SELECT COUNT(*) c FROM manual WHERE status='ready'").fetchone()["c"]
    chunk_count = conn.execute("SELECT COUNT(*) c FROM chunk WHERE status='ready'").fetchone()["c"]

    last_backup_row = conn.execute(
        "SELECT created_at FROM backup_run WHERE status='ok' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    return SystemStatus(
        db_ready=True,
        config_complete=not missing,
        missing_config_keys=missing,
        vault_root_valid=vault_ok,
        pdf_root_valid=_dir_valid(cfg["paths"].get("pdf_root", "")),
        counts={"notes": note_count, "manuals_ready": manual_ready, "chunks_ready": chunk_count},
        last_backup_at=last_backup_row["created_at"] if last_backup_row else None,
        ws_available=True,
    )
