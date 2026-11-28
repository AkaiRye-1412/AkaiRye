"""系统状态（04 文档 3.0：GET /api/system/status，前端启动健康检查）。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlite3 import Connection

from app.api.deps import get_db
from app.core import paths
from app.services import embedding as embedding_service

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
    # 向量化是否"已配置"：地址 + 模型名就算（本地 Ollama 不需要 Key）
    emb_cfg = dict(cfg["embedding"])
    emb_url = embedding_service.base_url(
        {**emb_cfg, "base_url": emb_cfg.get("base_url") or emb_cfg.get("provider") or ""}
    )
    emb_ok = bool(emb_url and emb_cfg.get("model")) and (
        emb_cfg.get("mode") == "ollama"
        or embedding_service.is_local(emb_url)
        or bool(emb_cfg.get("api_key_set"))
    )
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
        pdf_root_valid=_pdf_ok(cfg),
        counts={"notes": note_count, "manuals_ready": manual_ready, "chunks_ready": chunk_count},
        last_backup_at=last_backup_row["created_at"] if last_backup_row else None,
        ws_available=True,
    )


def _pdf_ok(cfg: dict) -> bool:
    """PDF 索引位置：旧单值或新多值任一生效即为有效。"""
    paths_cfg = cfg.get("paths") or {}
    if _dir_valid(paths_cfg.get("pdf_root", "")):
        return True
    return any(_dir_valid(str(x)) for x in (paths_cfg.get("pdf_roots") or []))


# ---- 原生「选择文件夹」对话框 --------------------------------------------------
# 浏览器拿不到绝对路径（file input 只给相对路径、File System Access 只给句柄），
# 所以由后端在本机弹系统对话框，前端把返回的绝对路径填进索引位置槽位。


@router.post("/pick-folder")
def pick_folder() -> dict:
    """弹出本机文件夹选择框；用户取消时返回空路径（cancelled=true）。"""
    from app.services import folder_picker

    if not folder_picker.available():
        raise HTTPException(
            status_code=501,
            detail={"code": "picker_unavailable", "message": "当前系统没有可用的文件夹选择框，请手动填写路径"},
        )
    try:
        picked = folder_picker.pick_folder()
    except folder_picker.PickerBusy as exc:
        raise HTTPException(
            status_code=409, detail={"code": "picker_busy", "message": str(exc)}
        ) from exc
    except folder_picker.PickerUnavailable as exc:
        raise HTTPException(
            status_code=501, detail={"code": "picker_unavailable", "message": str(exc)}
        ) from exc
    return {"path": picked, "cancelled": not picked}


# ---- 程序数据库文件位置（workbench.db，与知识库资料目录无关） ------------------


class DbLocationBody(BaseModel):
    # 单个目录（workbench.db 存放处）；roots 仅为兼容旧前端，取第 1 个
    dir: str | None = None
    roots: list[str] = Field(default_factory=list)


@router.get("/db-location")
def get_db_location() -> dict:
    cur = paths.db_path()
    return {
        "dir": str(cur.parent),
        "active_dir": str(cur.parent),
        "db_file": str(cur),
        "default_dir": str(paths.DATA_DIR),
        "restart_required": False,
    }


@router.put("/db-location")
def set_db_location(body: DbLocationBody, conn: Connection = Depends(get_db)) -> dict:
    """保存程序数据库（workbench.db）存放目录；切换时自动把现有库复制过去（需重启）。

    配置不能存在数据库里（改位置前还连在旧库上），因此单独落 data/instance.json
    的 `db_file_dir`；与设置页「数据库索引位置」（知识库要解析的资料目录）互不影响。
    """
    import shutil
    from pathlib import Path

    raw = (body.dir or "").strip()
    if not raw:
        raw = next((str(x).strip() for x in body.roots if str(x).strip()), "")
    if not raw:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": "数据库文件位置不能为空"},
        )

    p = Path(raw).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "path_invalid", "message": f"目录不可用：{raw}"},
        ) from exc

    # 统一存规范绝对路径（Windows 8.3 短名会被展开，避免界面显示与实际不一致）
    target_dir = p.resolve()
    target_db = target_dir / "workbench.db"
    current_db = paths.db_path()
    restart = target_dir != current_db.parent

    if restart and not target_db.exists() and current_db.is_file():
        # 先把 WAL 合并进主库再复制，避免新库丢掉最近的写入
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        except Exception:  # noqa: BLE001 —— checkpoint 失败不阻断（复制主库即可）
            pass
        shutil.copy2(current_db, target_db)

    inst = {k: v for k, v in paths.load_instance().items() if k != "db_roots"}
    inst["db_file_dir"] = str(target_dir)
    paths.save_instance(inst)
    return {
        "ok": True,
        "dir": str(target_dir),
        "active_dir": str(target_dir),
        "db_file": str(target_db),
        "restart_required": restart,
    }
