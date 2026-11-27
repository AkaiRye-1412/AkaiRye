"""存量笔记整理（M7 · FR-CLEAN-01~06）。

职责边界（需求 4.8 / 设计 6.5）：
- 只写 frontmatter：name / project / status / start_date / updated，正文逐字不动（FR-CLEAN-04）；
- 推断口径：客户读现有 `name`；项目按 `project` → 文件名 → 正文 H1；识别不到留空等用户补；
- 默认状态「已归档」（FR-CLEAN-06）：缺 status 的存量笔记不冒充进行中；
- 执行前备份：受影响原文打进 zip（backup_run kind=cleanup_rollback）+ 每篇 `before_content`
  存库，回滚按字节还原（FR-CLEAN-05 / NFR-DATA-02）。

为什么不用 `updated: 今天`：存量整理会一次扫到几十篇历史笔记，逐篇刷成今天会把
「最近更新 / 待跟进」全部顶到最新，看板立刻失真。缺失 updated 时按推断出的
start_date 补，保留这批笔记原本的时间位置。
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

from app.services import backup as backup_service
from app.services import vault
from app.ws.events import broker

logger = logging.getLogger(__name__)

# 整理目标字段（按写入优先级；`updated` 只在缺失时补）
CLEAN_KEYS = ("name", "project", "status", "start_date", "updated")

# 纯日期文件名 / 标题：不能拿来当项目名
_DATE_RE = re.compile(r"^\d{4}[-_./]?\d{2}[-_./]?\d{2}$")
_DATE_PREFIX_RE = re.compile(r"^\d{4}[-_./]?\d{2}[-_./]?\d{2}[-_\s]*")

# 通用占位标题：H1 是这些时说明它不承载项目信息
_GENERIC_TITLES = {
    "售后支持卡片",
    "售后卡片",
    "售后记录",
    "售后",
    "记录",
    "未命名",
    "untitled",
}

_H1_RE = re.compile(r"^#\s+(.+)$")


class CleanupState(Exception):
    """批次状态不允许当前操作（已执行 / 已回滚）。"""


class CleanupUnresolved(ValueError):
    """有未跳过的条目仍缺客户或项目名（FR-CLEAN-03：必须补全或跳过）。"""

    def __init__(self, paths: list[str]) -> None:
        self.paths = paths
        super().__init__(f"还有 {len(paths)} 篇缺少客户或项目名，请补全或跳过")


class CleanupBackupFailed(RuntimeError):
    """执行前备份失败：宁可不写，也不能没有回滚依据（FR-CLEAN-05）。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- 字段推断 --------------------------------------------------------------


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        m = _H1_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    return ""


def _meaningful(text: str) -> bool:
    v = text.strip().strip("#").strip()
    return bool(v) and not _DATE_RE.match(v) and v.lower() not in _GENERIC_TITLES


def _known_customers(conn) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT customer FROM note_file WHERE customer IS NOT NULL AND customer != ''"
    ).fetchall()
    return {r["customer"].strip() for r in rows if r["customer"].strip()}


def _infer_customer(fields: dict[str, str], stem: str, known: set[str]) -> tuple[str, str]:
    """客户 = 现有 name（FR-CLEAN-02）；name 缺失时只认「文件名前缀正好是已知客户」。"""
    raw = (fields.get("name") or "").strip()
    if raw:
        return raw, "头部 name"
    if "-" in stem:
        head = stem.split("-", 1)[0].strip()
        if head in known:
            return head, "文件名前缀"
    return "", "未识别"


def _infer_project(
    fields: dict[str, str], body: str, stem: str, customer: str
) -> tuple[str, str]:
    """项目名：project 字段 → 文件名（`客户-项目.md`）→ 正文 H1 → 留空。"""
    raw = (fields.get("project") or "").strip()
    if raw:
        return raw, "头部 project"

    rest = stem
    if customer and rest.startswith(f"{customer}-"):
        rest = rest[len(customer) + 1 :]
    rest = _DATE_PREFIX_RE.sub("", rest).strip(" -_")
    if _meaningful(rest):
        return rest[:60], "文件名"

    h1 = _first_h1(body)
    if _meaningful(h1):
        return h1[:60], "正文标题"
    return "", "未识别"


def _infer_start_date(
    fields: dict[str, str], stem: str, mtime_ts: int
) -> tuple[str, str]:
    """开始日期：start_date → 文件名 YYYYMMDD → time 字段前缀 → 文件修改时间。"""
    raw = (fields.get("start_date") or "").strip()
    if raw:
        try:
            return date.fromisoformat(raw[:10]).isoformat(), "头部 start_date"
        except ValueError:
            pass

    if _DATE_RE.match(stem):
        digits = re.sub(r"\D", "", stem)
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])).isoformat(), "文件名日期"
        except ValueError:
            pass

    time_raw = (fields.get("time") or "").strip()
    if time_raw:
        try:
            return date.fromisoformat(time_raw[:10]).isoformat(), "time 字段"
        except ValueError:
            pass

    return date.fromtimestamp(mtime_ts).isoformat(), "文件修改时间"


def _infer_status(fields: dict[str, str]) -> tuple[str, str]:
    """缺 status 的存量笔记默认「已归档」（FR-CLEAN-06），已有的保持原样。"""
    current = vault.YAML_TO_STATUS.get((fields.get("status") or "").strip())
    if current:
        return current, "沿用现有"
    return "archived", "默认已归档"


def _missing_keys(fields: dict[str, str]) -> list[str]:
    return [k for k in CLEAN_KEYS if not (fields.get(k) or "").strip()]


def _build_patch(fields: dict[str, str], item: dict) -> dict[str, str]:
    """要写入的 frontmatter 行（只含真正变化的键）。执行前会按磁盘现状重算一次。"""
    patch: dict[str, str] = {}

    customer = (item.get("customer") or "").strip()
    if customer and customer != (fields.get("name") or "").strip():
        patch["name"] = customer

    project = (item.get("project_name") or "").strip()
    if project and project != (fields.get("project") or "").strip():
        patch["project"] = project

    status = item.get("status") or "archived"
    if status not in vault.STATUS_TO_YAML:
        raise ValueError("状态仅支持「已归档 / 进行中」")
    if (fields.get("status") or "").strip() != vault.STATUS_TO_YAML[status]:
        patch["status"] = vault.STATUS_TO_YAML[status]

    start = (item.get("start_date") or "").strip()
    if start:
        try:
            start = date.fromisoformat(start[:10]).isoformat()
        except ValueError as exc:
            raise ValueError(f"开始日期需为 YYYY-MM-DD：{start}") from exc
        if (fields.get("start_date") or "").strip() != start:
            patch["start_date"] = start

    # updated 只在缺失时补，且不刷新成今天（避免把历史笔记顶到「最近更新」）
    if not (fields.get("updated") or "").strip() and start:
        patch["updated"] = start

    return patch


# ---- 扫描 ------------------------------------------------------------------


def _scan_entries(conn, scope: str = "missing") -> tuple[list[dict], list[str]]:
    """扫描 notes 目录，返回 (候选条目, 已知客户列表)。

    只看售后笔记文件夹内部（外部白名单目录的绝对路径主键不属于售后笔记，与
    GET /api/notes 口径一致）。候选口径是「五个头部字段缺任意一个」，而不是
    note_file.head_ok —— head_ok 对按日卡片只要求 name，会把缺 project / status
    的卡片判为完整，正好漏掉整理最该处理的那批。
    """
    base = vault.notes_dir(conn)
    known = _known_customers(conn)
    entries: list[dict] = []

    for abs_path in sorted(base.rglob("*.md")):
        if not abs_path.is_file():
            continue
        rel_parts = abs_path.relative_to(base).parts
        if any(p.startswith(".") for p in rel_parts):
            continue  # .trash / .obsidian 之类不参与整理

        text = abs_path.read_bytes().decode("utf-8", errors="replace")
        fields, body, _head_ok, _required = vault.parse_frontmatter(text)
        missing = _missing_keys(fields)
        if scope == "missing" and not missing:
            continue

        stem = abs_path.stem
        customer, customer_src = _infer_customer(fields, stem, known)
        project, project_src = _infer_project(fields, body, stem, customer)
        status, status_src = _infer_status(fields)
        start, start_src = _infer_start_date(fields, stem, int(abs_path.stat().st_mtime))

        item = {
            "path": abs_path.relative_to(base).as_posix(),
            "customer": customer,
            "project_name": project,
            "status": status,
            "start_date": start,
            "missing": missing,
            "sources": {
                "customer": customer_src,
                "project": project_src,
                "status": status_src,
                "start_date": start_src,
            },
        }
        item["changes"] = list(_build_patch(fields, item).keys())
        entries.append(item)

    return entries, sorted(known)


# ---- 批次读写 --------------------------------------------------------------


def _item_dict(row) -> dict:
    return {
        "id": row["id"],
        "path": row["path"],
        "file_name": Path(row["path"]).name,
        "customer": row["customer"] or "",
        "project_name": row["project_name"] or "",
        "status": row["status"] or "archived",
        "start_date": row["start_date"] or "",
        "missing": json.loads(row["missing"] or "[]"),
        "sources": json.loads(row["sources"] or "{}"),
        "changes": json.loads(row["changes"] or "[]"),
        "skip": bool(row["skip"]),
        "applied": bool(row["applied"]),
        "rolled_back": bool(row["rolled_back"]),
        "error": row["error"],
    }


def _batch_dict(row) -> dict:
    keys = row.keys()
    out = {
        "id": row["id"],
        "status": row["status"],
        "scope": row["scope"],
        "total": row["total"],
        "applied": row["applied"],
        "skipped": row["skipped"],
        "failed": row["failed"],
        "restored": 0,
        "backup_path": row["backup_path"],
        "note": row["note"],
        "created_at": row["created_at"],
        "executed_at": row["executed_at"],
        "rolled_back_at": row["rolled_back_at"],
    }
    # 列表查询带聚合列（草稿还没执行，存量为 0，用实时聚合更准）
    for src, dst in (
        ("item_total", "total"),
        ("item_applied", "applied"),
        ("item_skipped", "skipped"),
        ("item_failed", "failed"),
        ("item_restored", "restored"),
    ):
        if src in keys and row[src] is not None:
            out[dst] = row[src]
    return out


def get_batch(conn, batch_id: int) -> dict:
    row = conn.execute("SELECT * FROM cleanup_batch WHERE id=?", (batch_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"批次 #{batch_id} 不存在")
    items = conn.execute(
        "SELECT * FROM cleanup_item WHERE batch_id=? ORDER BY path", (batch_id,)
    ).fetchall()
    batch = _batch_dict(row)
    batch["items"] = [_item_dict(r) for r in items]
    # 计数以条目实况为准：回滚后 applied 归零，不能还挂着执行时的 41
    batch["total"] = len(items)
    batch["applied"] = sum(1 for r in items if r["applied"])
    batch["skipped"] = sum(1 for r in items if r["skip"])
    batch["failed"] = sum(1 for r in items if r["error"] and not r["applied"])
    batch["restored"] = sum(1 for r in items if r["rolled_back"])
    return batch


def list_batches(conn, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """
        SELECT b.*,
               (SELECT COUNT(*) FROM cleanup_item i WHERE i.batch_id=b.id) AS item_total,
               (SELECT COUNT(*) FROM cleanup_item i WHERE i.batch_id=b.id AND i.applied=1) AS item_applied,
               (SELECT COUNT(*) FROM cleanup_item i WHERE i.batch_id=b.id AND i.skip=1) AS item_skipped,
               (SELECT COUNT(*) FROM cleanup_item i
                 WHERE i.batch_id=b.id AND i.applied=0 AND i.error IS NOT NULL) AS item_failed,
               (SELECT COUNT(*) FROM cleanup_item i WHERE i.batch_id=b.id AND i.rolled_back=1) AS item_restored
        FROM cleanup_batch b
        ORDER BY b.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_batch_dict(r) for r in rows]


def summary(conn) -> dict:
    """页面头部数字：总数 / 待整理数 / 已知客户（补全时给 datalist）。"""
    vault.rescan_all(conn)
    rows = conn.execute("SELECT path FROM note_file").fetchall()
    local = [r for r in rows if not Path(r["path"]).is_absolute()]
    last = conn.execute(
        "SELECT * FROM cleanup_batch ORDER BY id DESC LIMIT 1"
    ).fetchone()
    customers: list[str] = []
    head_missing = 0
    try:
        entries, customers = _scan_entries(conn, "missing")
        head_missing = len(entries)
    except NotADirectoryError:
        customers = sorted(_known_customers(conn))
    return {
        "notes_total": len(local),
        "head_missing": head_missing,
        "customers": customers,
        "last_batch": _batch_dict(last) if last is not None else None,
        "notes_folder": (vault.notes_dir(conn).name or ""),
    }


def create_batch(conn, scope: str = "missing") -> dict:
    """扫描并落一条草稿批次（预览表的可编辑状态从这里来）。

    未执行的旧草稿直接丢弃：再次扫描的语义就是「以磁盘现状重来」；已执行批次保留，
    用于回滚与审计。
    """
    if scope not in ("missing", "all"):
        raise ValueError("scope 仅支持 missing / all")

    base = vault.notes_dir(conn)
    if not base.is_dir():
        raise NotADirectoryError(str(base))

    for r in conn.execute("SELECT id FROM cleanup_batch WHERE status='draft'").fetchall():
        conn.execute("DELETE FROM cleanup_item WHERE batch_id=?", (r["id"],))
        conn.execute("DELETE FROM cleanup_batch WHERE id=?", (r["id"],))

    entries, _customers = _scan_entries(conn, scope)
    now = _utcnow()
    cur = conn.execute(
        "INSERT INTO cleanup_batch (status, scope, total, created_at) VALUES ('draft', ?, ?, ?)",
        (scope, len(entries), now),
    )
    batch_id = int(cur.lastrowid)
    for e in entries:
        conn.execute(
            """
            INSERT INTO cleanup_item
                (batch_id, path, customer, project_name, status, start_date,
                 missing, sources, changes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                e["path"],
                e["customer"],
                e["project_name"],
                e["status"],
                e["start_date"],
                json.dumps(e["missing"], ensure_ascii=False),
                json.dumps(e["sources"], ensure_ascii=False),
                json.dumps(e["changes"], ensure_ascii=False),
            ),
        )
    conn.commit()
    return get_batch(conn, batch_id)


def _require_draft(conn, batch_id: int):
    row = conn.execute("SELECT * FROM cleanup_batch WHERE id=?", (batch_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"批次 #{batch_id} 不存在")
    if row["status"] != "draft":
        raise CleanupState("该批次已执行或已回滚，请重新扫描后再整理")
    return row


def update_items(conn, batch_id: int, items: list[dict]) -> dict:
    """保存预览表的逐条修改（客户 / 项目名 / 状态 / 开始日期 / 跳过）。"""
    _require_draft(conn, batch_id)
    existing = {
        r["path"]: r
        for r in conn.execute("SELECT * FROM cleanup_item WHERE batch_id=?", (batch_id,)).fetchall()
    }
    for raw in items:
        path = str(raw.get("path") or "").strip()
        if path not in existing:
            continue
        sets: dict[str, object] = {}
        for key in ("customer", "project_name", "start_date"):
            if key in raw and raw[key] is not None:
                sets[key] = str(raw[key]).strip()
        if "status" in raw and raw["status"] is not None:
            if raw["status"] not in ("active", "archived"):
                raise ValueError("状态仅支持 active / archived")
            sets["status"] = raw["status"]
        if "skip" in raw and raw["skip"] is not None:
            sets["skip"] = 1 if raw["skip"] else 0
        if not sets:
            continue
        cols = ", ".join(f"{k}=?" for k in sets)
        conn.execute(
            f"UPDATE cleanup_item SET {cols} WHERE batch_id=? AND path=?",
            (*sets.values(), batch_id, path),
        )
    conn.commit()
    return get_batch(conn, batch_id)


# ---- 执行 ------------------------------------------------------------------


def _snapshot(conn, batch_id: int, plans: list[tuple]) -> str:
    """受影响原文打包 zip → backup_run(kind=cleanup_rollback)。失败即中止写入。"""
    target_dir = backup_service.backup_dir(conn)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"cleanup_{batch_id}_{stamp}.zip"
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for item, _abs_path, data in plans:
                zf.writestr(item["path"], data)
        size = target.stat().st_size
    except OSError as exc:  # 目录无权限 / 磁盘满 / 文件被占用
        target.unlink(missing_ok=True)
        logger.error("存量整理备份失败：%s", exc)
        raise CleanupBackupFailed(f"执行前备份失败，已中止：{exc}") from exc

    conn.execute(
        "INSERT INTO backup_run (kind, file_path, size, status, created_at)"
        " VALUES ('cleanup_rollback', ?, ?, 'ok', ?)",
        (str(target), size, _utcnow()),
    )
    conn.commit()
    return str(target)


def execute(conn, batch_id: int, overrides: list[dict] | None = None) -> dict:
    """写入头部字段（FR-CLEAN-04）：先备份、再逐篇只改 frontmatter、单篇失败不拖垮整批。

    overrides = 预览表里用户改动后的最终值（前端一次提交，无需先单独保存草稿）。
    """
    if overrides is not None:
        update_items(conn, batch_id, overrides)

    _require_draft(conn, batch_id)
    rows = conn.execute(
        "SELECT * FROM cleanup_item WHERE batch_id=? ORDER BY path", (batch_id,)
    ).fetchall()
    total_items = len(rows)
    if overrides is not None:
        # 提交上来的就是「这次要写的集合」：没提交的行不执行，避免残留旧行被顺带写入
        submitted = {str(o.get("path") or "") for o in overrides}
        rows = [r for r in rows if r["path"] in submitted]
    items = [_item_dict(r) for r in rows]
    if not items:
        raise ValueError("该批次没有待整理的笔记")

    unresolved = [
        i["path"] for i in items if not i["skip"] and (not i["customer"] or not i["project_name"])
    ]
    if unresolved:
        raise CleanupUnresolved(unresolved)

    # 计划：按磁盘现状算 patch；头部已经完整的标为跳过（scope=all 常见）
    plans: list[tuple[dict, Path, bytes, dict]] = []
    skipped = 0
    for item in items:
        if item["skip"]:
            skipped += 1
            continue
        try:
            abs_path = vault.safe_rel_path(conn, item["path"])
        except vault.PathOutsideRoot:
            item["error"] = "路径越出配置根目录"
            skipped += 1
            continue
        if not abs_path.is_file():
            conn.execute(
                "UPDATE cleanup_item SET skip=1, error=? WHERE id=?", ("文件已不存在", item["id"])
            )
            skipped += 1
            continue
        data = abs_path.read_bytes()
        fields, _body, _ok, _missing = vault.parse_frontmatter(
            data.decode("utf-8", errors="replace")
        )
        patch = _build_patch(fields, item)
        if not patch:
            conn.execute("UPDATE cleanup_item SET skip=1, error=NULL WHERE id=?", (item["id"],))
            skipped += 1
            continue
        plans.append((item, abs_path, data, patch))

    if not plans:
        conn.commit()
        raise ValueError("所选笔记的头部已完整，没有需要写入的内容")

    backup_path = _snapshot(conn, batch_id, [(i, p, d) for i, p, d, _ in plans])

    applied = failed = 0
    errors: list[dict] = []
    now_iso = _now_iso()
    for item, abs_path, _snapshot_data, _patch in plans:
        try:
            # 备份到写入之间文件可能被外部改动：以磁盘现状为准重算，避免覆盖用户新值
            current = abs_path.read_bytes()
            text = current.decode("utf-8", errors="replace")
            fields, _body, _ok, _missing = vault.parse_frontmatter(text)
            patch = _build_patch(fields, item)
            if not patch:
                conn.execute("UPDATE cleanup_item SET skip=1 WHERE id=?", (item["id"],))
                skipped += 1
                continue
            vault._write_text(abs_path, vault._apply_head_updates(text, patch))
            row = vault.scan_file(conn, abs_path, item["path"], now_iso)
            conn.execute(
                """
                UPDATE cleanup_item
                   SET applied=1, skip=0, error=NULL, before_content=?, before_hash=?,
                       after_hash=?, changes=?
                 WHERE id=?
                """,
                (
                    text,
                    vault.content_hash(current),
                    row["content_hash"],
                    json.dumps(sorted(patch.keys()), ensure_ascii=False),
                    item["id"],
                ),
            )
            applied += 1
        except Exception as exc:  # noqa: BLE001 —— 单篇失败要继续处理其余笔记
            logger.exception("存量整理写入失败：%s", item["path"])
            failed += 1
            message = str(exc) or exc.__class__.__name__
            conn.execute(
                "UPDATE cleanup_item SET applied=0, error=? WHERE id=?", (message, item["id"])
            )
            errors.append({"path": item["path"], "error": message})
        conn.commit()

    status = "done" if applied else "failed"
    conn.execute(
        """
        UPDATE cleanup_batch
           SET status=?, total=?, applied=?, skipped=?, failed=?, backup_path=?,
               executed_at=?, note=?
         WHERE id=?
        """,
        (
            status,
            total_items,
            applied,
            skipped,
            failed,
            backup_path,
            _utcnow(),
            f"{applied} 篇写入，{skipped} 篇跳过，{failed} 篇失败",
            batch_id,
        ),
    )
    conn.commit()

    broker.publish(
        "cleanup.done",
        {"batch_id": batch_id, "status": status, "applied": applied, "skipped": skipped,
         "failed": failed, "backup_path": backup_path},
    )
    logger.info("存量整理 #%s 完成：写入 %d，跳过 %d，失败 %d", batch_id, applied, skipped, failed)
    return {"batch": get_batch(conn, batch_id), "errors": errors}


# ---- 回滚 ------------------------------------------------------------------


def rollback(conn, batch_id: int, force: bool = False) -> dict:
    """按执行前快照还原（FR-CLEAN-05）。

    整理后又被外部编辑过的笔记默认跳过（不覆盖用户新内容）；force=True 才强写。
    只恢复 applied=1 的条目，跳过项本来就没动过。
    """
    row = conn.execute("SELECT * FROM cleanup_batch WHERE id=?", (batch_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"批次 #{batch_id} 不存在")
    if row["status"] not in ("done", "failed"):
        raise CleanupState("该批次没有可回滚的写入记录")

    items = conn.execute(
        "SELECT * FROM cleanup_item WHERE batch_id=? AND applied=1 ORDER BY path", (batch_id,)
    ).fetchall()
    if not items:
        raise CleanupState("该批次没有已写入的条目，无需回滚")

    restored = 0
    skipped: list[dict] = []
    errors: list[dict] = []
    now_iso = _now_iso()
    for r in items:
        item = _item_dict(r)
        try:
            abs_path = vault.safe_rel_path(conn, item["path"])
            if not abs_path.is_file():
                skipped.append({"path": item["path"], "reason": "文件已不存在"})
                continue
            if not force and r["after_hash"]:
                if vault.content_hash(abs_path.read_bytes()) != r["after_hash"]:
                    skipped.append({"path": item["path"], "reason": "整理后已被外部修改"})
                    continue
            if r["before_content"] is None:
                skipped.append({"path": item["path"], "reason": "缺少执行前快照"})
                continue
            vault._write_text(abs_path, r["before_content"])
            vault.scan_file(conn, abs_path, item["path"], now_iso)
            conn.execute(
                "UPDATE cleanup_item SET applied=0, rolled_back=1, error=NULL WHERE id=?",
                (r["id"],),
            )
            restored += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("存量整理回滚失败：%s", item["path"])
            message = str(exc) or exc.__class__.__name__
            conn.execute("UPDATE cleanup_item SET error=? WHERE id=?", (message, r["id"]))
            errors.append({"path": item["path"], "error": message})
        conn.commit()

    if restored == 0:
        raise CleanupState(
            skipped[0]["reason"] if skipped else "没有可还原的条目"
        )

    status = "rolled_back" if not skipped and not errors else "done"
    conn.execute(
        "UPDATE cleanup_batch SET status=?, rolled_back_at=?, applied="
        "(SELECT COUNT(*) FROM cleanup_item WHERE batch_id=? AND applied=1), note=? WHERE id=?",
        (
            status,
            _utcnow(),
            batch_id,
            f"回滚 {restored} 篇" + (f"，跳过 {len(skipped)} 篇" if skipped else ""),
            batch_id,
        ),
    )
    conn.commit()
    broker.publish(
        "cleanup.rolled_back",
        {"batch_id": batch_id, "restored": restored, "skipped": len(skipped)},
    )
    logger.info("存量整理 #%s 回滚：还原 %d，跳过 %d", batch_id, restored, len(skipped))
    return {
        "batch": get_batch(conn, batch_id),
        "restored": restored,
        "skipped": skipped,
        "errors": errors,
        "partial": bool(skipped or errors),
    }


def delete_draft(conn, batch_id: int) -> None:
    """取消预览：只允许删草稿，已执行批次是回滚依据，不能删。"""
    _require_draft(conn, batch_id)
    _drop_batch(conn, batch_id)
    conn.commit()


def _drop_batch(conn, batch_id: int) -> None:
    conn.execute("DELETE FROM cleanup_item WHERE batch_id=?", (batch_id,))
    conn.execute("DELETE FROM cleanup_batch WHERE id=?", (batch_id,))


def delete_batch(conn, batch_id: int, allow_finished: bool = False) -> dict:
    """删除一条整理记录。

    草稿直接丢；已执行 / 已回滚的批次是回滚依据，必须 allow_finished（前端二次确认后
    显式 force）才删，否则 409。备份 zip 留在磁盘上——删记录不等于删证据，需要时仍可
    手动从 data/backups 里取回原文。已写入的笔记本身不会被动。
    """
    row = conn.execute(
        "SELECT id, status FROM cleanup_batch WHERE id=?", (batch_id,)
    ).fetchone()
    if row is None:
        raise FileNotFoundError(batch_id)
    if row["status"] != "draft" and not allow_finished:
        raise CleanupState("已执行的批次是回滚依据，删除需要显式确认")
    _drop_batch(conn, batch_id)
    conn.commit()
    logger.info("存量整理 #%s 记录已删除（原状态 %s）", batch_id, row["status"])
    return {"batch_id": batch_id, "status": row["status"]}


def clear_batches(conn, keep_draft: bool = True) -> int:
    """清空整理记录。默认保留草稿——正在编辑的预览不能被顺手清掉。"""
    sql = "SELECT id FROM cleanup_batch"
    if keep_draft:
        sql += " WHERE status != 'draft'"
    ids = [r["id"] for r in conn.execute(sql).fetchall()]
    for bid in ids:
        _drop_batch(conn, bid)
    conn.commit()
    logger.info("存量整理记录已清空 %d 条（保留草稿=%s）", len(ids), keep_draft)
    return len(ids)
