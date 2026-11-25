"""备份服务（OQ-10 / database-rules 第 7 节 / 04 文档 3.11）。

- 方式：VACUUM INTO（WAL 下禁止直接拷贝热库文件）；
- 每日备份：启动时检查 backup_run 最近成功记录，>24h 补跑（NFR-DATA-04）；
- 轮转：保留 backup.keep（默认 7）份。
"""

import logging
from datetime import datetime, timedelta, timezone

from app.core import paths

logger = logging.getLogger(__name__)

KEEP_DEFAULT = 7


def run_backup(conn, kind: str = "daily") -> dict:
    """执行一次备份并记录 backup_run。"""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    target = paths.BACKUP_DIR / f"workbench_{kind}_{stamp}.db"
    status, size, error = "ok", None, None
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
        size = target.stat().st_size
        logger.info("备份完成：%s（%d bytes）", target.name, size)
    except Exception as e:  # noqa: BLE001
        status, error = "failed", str(e)
        logger.error("备份失败：%s", e)

    conn.execute(
        "INSERT INTO backup_run (kind, file_path, size, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (kind, str(target), size, status, now.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    conn.commit()
    return {"kind": kind, "file_path": str(target), "size": size, "status": status, "error": error}


def rotate(conn, keep: int = KEEP_DEFAULT) -> int:
    """按 backup_run 记录轮转，仅删除 ok 记录对应的旧文件。"""
    rows = conn.execute(
        "SELECT id, file_path FROM backup_run WHERE kind='daily' AND status='ok' ORDER BY created_at DESC"
    ).fetchall()
    removed = 0
    for row in rows[keep:]:
        try:
            from pathlib import Path

            Path(row["file_path"]).unlink(missing_ok=True)
            conn.execute("DELETE FROM backup_run WHERE id = ?", (row["id"],))
            removed += 1
        except OSError as e:
            logger.warning("轮转删除失败 %s：%s", row["file_path"], e)
    conn.commit()
    return removed


def needs_daily_backup(conn) -> bool:
    row = conn.execute(
        "SELECT created_at FROM backup_run WHERE kind='daily' AND status='ok' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return True
    last = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - last > timedelta(hours=24)
