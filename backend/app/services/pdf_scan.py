"""PDF 根目录扫描服务（M9 · FR-RAG-04/09/12/13，04 文档 3.8）。

**两类目录，职责不同**（2026-11-28 定稿）：
- `paths.pdf_roots`（PDF 手册索引位置）→ 只用于手册页浏览/预览，**不作为知识库来源**；
- `paths.db_roots`（数据库索引位置）→ 知识库要解析的资料目录，其中的 PDF 才允许入库；
- 扫描范围仍是两者并集（便于统一登记 manual 表），但**展示严格分开**：
  手册页只列 `pdf_roots`，知识库页只列 `db_roots`。

职责：把扫描范围的磁盘现状同步进 manual 表（**引用不复制**，FR-RAG-09）——
- 新文件 → status='discovered'（Q-15：扫描到但未入库，必须用户手动触发入库）；
- mtime/size 变化 → 已入库的置 'update'（旧块保留可检索，等用户决定重建）；
- 文件消失 → 已入库置 'invalid'（保留块与路径，供「重新定位」），无块的直接清理行；
- 品牌：路径/文件名命中枚举即自动打标（FR-RAG-12），识别不了留空（未知）。

扫描只读文件元信息 + 对新文件做一次轻量探测（页数/是否扫描件），不做正文解析——
解析是入库队列的活（manual_index），避免打开 PDF 目录就卡住。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from app.services import config_service

logger = logging.getLogger(__name__)

# 品牌自动识别（FR-RAG-12/13）：路径或文件名包含关键词即归属
BRAND_PATTERNS: dict[str, tuple[str, ...]] = {
    "高创": ("高创", "gaochuang", "servo drive", "gt-"),
    "ACS": ("acs", "acs-"),
    "Agito": ("agito",),
}
DEFAULT_BRANDS = ("高创", "ACS", "Agito")


class PdfRootError(ValueError):
    """pdf_root 未配置或不可用。"""


def pdf_roots(conn) -> list[Path]:
    """全部 PDF 手册索引位置（最多 3 个，第 1 个为主位置）。

    优先读新配置 paths.pdf_roots；为空时回落旧单值 paths.pdf_root。
    只返回存在的目录（不存在的跳过，避免整批误标 invalid）。
    """
    cfg = config_service.get_raw(conn, "paths")
    raw: list[str] = []
    for x in cfg.get("pdf_roots") or []:
        s = str(x).strip()
        if s and s not in raw:
            raw.append(s)
    if not raw:
        legacy = (cfg.get("pdf_root") or "").strip()
        if legacy:
            raw = [legacy]
    roots: list[Path] = []
    for s in raw[:3]:
        p = Path(s).expanduser()
        if p.is_dir():
            roots.append(p)
    return roots


def pdf_root(conn) -> Path:
    """主 PDF 根目录（第 1 个）；未配置/不存在/不是目录 → PdfRootError。"""
    cfg = config_service.get_raw(conn, "paths")
    raw = (cfg.get("pdf_root") or "").strip()
    roots = pdf_roots(conn)
    if roots:
        return roots[0]
    if not raw:
        raise PdfRootError("尚未配置 PDF 根目录：请到设置页填写「PDF 手册索引位置」")
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise PdfRootError(f"PDF 根目录不存在或不是文件夹：{raw}")
    return root


def kb_roots(conn) -> list[Path]:
    """知识库资料目录（「数据库索引位置」，最多 3 个）：其中的 PDF 才允许入库。

    只返回存在的目录——配置了但盘还没挂上时不参与判定，避免整批误标。
    """
    cfg = config_service.get_raw(conn, "paths")
    out: list[Path] = []
    for x in cfg.get("db_roots") or []:
        s = str(x).strip()
        if not s:
            continue
        p = Path(s).expanduser()
        if p.is_dir() and all(p.resolve() != q.resolve() for q in out):
            out.append(p)
    return out[:3]


def scan_roots(conn) -> list[Path]:
    """扫描范围 = 手册浏览位置 ∪ 知识库资料目录（去重保序）。"""
    out = pdf_roots(conn)
    for p in kb_roots(conn):
        try:
            if all(p.resolve() != q.resolve() for q in out):
                out.append(p)
        except OSError:
            continue
    return out


def _in_roots(path: Path, roots: list[Path]) -> bool:
    try:
        pr = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            if pr.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def in_pdf_roots(conn, path: Path) -> bool:
    """路径是否落在任一 PDF 手册位置内（仅浏览范围）。"""
    return _in_roots(path, pdf_roots(conn))


def in_kb_roots(conn, path: Path) -> bool:
    """路径是否落在「数据库索引位置」内 —— 只有这里的 PDF 能进知识库。"""
    return _in_roots(path, kb_roots(conn))


def in_scan_roots(conn, path: Path) -> bool:
    """路径是否落在扫描范围内（浏览位置 ∪ 知识库资料目录）。"""
    return _in_roots(path, scan_roots(conn))


def scope_of(conn, path: Path) -> str:
    """PDF 归属：'kb'（可入库，数据库索引位置内）/ 'manual'（仅浏览）。"""
    return "kb" if in_kb_roots(conn, path) else "manual"


def root_of(conn, path: Path) -> Path | None:
    """路径所属的根（用于计算相对路径）；不在任何根内返回 None。"""
    try:
        pr = path.resolve()
    except OSError:
        return None
    for root in scan_roots(conn):
        try:
            if pr.is_relative_to(root.resolve()):
                return root
        except OSError:
            continue
    return None


def brands(conn) -> list[str]:
    """品牌枚举（FR-RAG-13）：以 brand 表为可配置源，空表回落到内置三项。"""
    rows = conn.execute("SELECT name FROM brand WHERE enabled=1 ORDER BY sort, name").fetchall()
    return [r["name"] for r in rows] or list(DEFAULT_BRANDS)


def detect_brand(rel_path: str, known: list[str]) -> str | None:
    """从相对路径 + 文件名识别品牌；已知品牌名直接命中优先。"""
    text = rel_path.lower()
    for name in known:
        if name and name.lower() in text:
            return name
    for name, patterns in BRAND_PATTERNS.items():
        if name not in known:
            continue
        if any(p in text for p in patterns):
            return name
    return None


def iter_pdfs(root: Path):
    """遍历 pdf_root 下全部 PDF（跳过隐藏目录/文件、Office 临时文件）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__MACOSX"]
        for fn in filenames:
            if fn.startswith(".") or fn.startswith("~$"):
                continue
            if Path(fn).suffix.lower() != ".pdf":
                continue
            yield Path(dirpath) / fn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scan(conn, *, probe_new: bool = True) -> dict:
    """全量镜像同步（幂等）。返回差异摘要，调用方负责 commit。"""
    from app.services.pdf_parse import probe_pdf

    roots = scan_roots(conn)
    if not roots:
        # 统一抛 PdfRootError（未配置 / 不存在），调用方语义不变
        pdf_root(conn)
    known = brands(conn)
    existing = {r["path"]: dict(r) for r in conn.execute("SELECT * FROM manual").fetchall()}

    added = updated = invalid = removed = 0
    seen: set[str] = set()

    for abs_path, root in [(p, r) for r in roots for p in iter_pdfs(r)]:
        key = abs_path.resolve().as_posix()
        if key in seen:
            continue  # 目录嵌套（如知识库目录在手册目录内）时只登记一次
        seen.add(key)
        try:
            st = abs_path.stat()
        except OSError:
            continue
        mtime, size = int(st.st_mtime), st.st_size
        try:
            rel = abs_path.relative_to(root).as_posix()
        except ValueError:
            rel = abs_path.name
        brand = detect_brand(rel, known)
        name = abs_path.stem

        row = existing.get(key)
        if row is None:
            pages = None
            if probe_new:
                try:
                    pages = probe_pdf(abs_path).get("pages")
                except Exception:  # noqa: BLE001 —— 探测失败不影响登记
                    pages = None
            conn.execute(
                "INSERT INTO manual (path, name, brand, pages, size, status, progress, file_mtime,"
                " content_hash, model_id, dim, last_parsed_at, error, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'discovered', 0, ?, NULL, NULL, NULL, NULL, NULL, ?)",
                (key, name, brand, pages, size, mtime, _now()),
            )
            added += 1
            continue

        # 品牌/名称/大小变化都顺手同步（路径是主键，改名视为新文件 + 旧文件消失）
        if row["brand"] != brand or row["name"] != name or row["size"] != size:
            conn.execute(
                "UPDATE manual SET brand=?, name=?, size=?, updated_at=? WHERE id=?",
                (brand, name, size, _now(), row["id"]),
            )

        if row["file_mtime"] != mtime or row["size"] != size:
            status = row["status"]
            if status in ("discovered", "failed"):
                next_status = status  # 未入库/失败：等用户触发，不必标「有更新」
            elif status == "invalid":
                # 文件回来了（改回原名/恢复挂载）：有块就是 ready，没块回到待入库
                has_chunks = conn.execute(
                    "SELECT 1 FROM chunk WHERE manual_id=? LIMIT 1", (row["id"],)
                ).fetchone()
                next_status = "ready" if has_chunks else "discovered"
            elif status in ("queue", "parsing", "rebuilding"):
                next_status = status  # 队列中，由入库流程接管
            else:
                next_status = "update"
            conn.execute(
                "UPDATE manual SET status=?, file_mtime=?, updated_at=? WHERE id=?",
                (next_status, mtime, _now(), row["id"]),
            )
            if next_status == "update":
                updated += 1

    # 磁盘上已消失的
    for key, row in existing.items():
        if key in seen:
            continue
        if row["status"] in ("queue", "parsing", "rebuilding"):
            continue  # 正在跑的任务自行收尾
        has_chunks = conn.execute(
            "SELECT 1 FROM chunk WHERE manual_id=? LIMIT 1", (row["id"],)
        ).fetchone()
        if row["status"] == "discovered" or (
            row["status"] == "failed" and not has_chunks
        ):
            conn.execute("DELETE FROM ingest_task WHERE manual_id=?", (row["id"],))
            conn.execute("DELETE FROM manual WHERE id=?", (row["id"],))
            removed += 1
        elif row["status"] != "invalid":
            conn.execute(
                "UPDATE manual SET status='invalid', updated_at=? WHERE id=?", (_now(), row["id"])
            )
            invalid += 1

    total = conn.execute("SELECT COUNT(*) c FROM manual").fetchone()["c"]
    logger.info("PDF 扫描完成：新增 %d，更新 %d，失效 %d，清理 %d", added, updated, invalid, removed)
    return {
        "root": roots[0].as_posix(),
        "roots": [r.as_posix() for r in roots],
        "added": added,
        "updated": updated,
        "invalid": invalid,
        "removed": removed,
        "total": total,
    }


def tree(conn, scope: str = "manual") -> dict:
    """目录树：按一级子文件夹分组（高创/ACS/Agito/其他），每项带入库状态与归属。

    两类目录各自独立展示（2026-11-28 定稿）：
    - scope="manual"（默认，手册页用）：只列「PDF 手册索引位置」内的 PDF；
    - scope="kb"（知识库页用）：只列「数据库索引位置」内的 PDF（可入库）。

    每次调用都读当前配置重新判定归属：把目录从某个位置移走后，
    对应页面上的 PDF 会立刻消失，不用等重新入库。
    """
    roots = kb_roots(conn) if scope == "kb" else pdf_roots(conn)
    rows = conn.execute(
        "SELECT m.*, (SELECT COUNT(*) FROM chunk c WHERE c.manual_id=m.id) AS chunks"
        " FROM manual m ORDER BY m.path"
    ).fetchall()

    folders: dict[str, list[dict]] = {}
    seen = 0
    for r in rows:
        if not _in_roots(Path(r["path"]), roots):
            continue
        item = dict(r)
        item_scope = "kb" if scope == "kb" else scope_of(conn, Path(r["path"]))
        own_root = root_of(conn, Path(r["path"]))
        try:
            rel = (
                Path(r["path"]).resolve().relative_to(own_root).as_posix()
                if own_root
                else Path(r["path"]).name
            )
        except (ValueError, OSError):
            rel = Path(r["path"]).name
        first = rel.split("/")[0] if "/" in rel else "未分类"
        item["rel_path"] = rel
        item["folder"] = first
        item["scope"] = item_scope
        item["title_path"] = None
        item["updated_label"] = (r["last_parsed_at"] or r["updated_at"] or "")[:10]
        folders.setdefault(first, []).append(item)
        seen += 1

    order = {"高创": 0, "ACS": 1, "Agito": 2, "未分类": 90}
    groups = [
        {"name": name, "items": sorted(items, key=lambda x: x["rel_path"].lower())}
        for name, items in sorted(folders.items(), key=lambda kv: (order.get(kv[0], 50), kv[0]))
    ]
    counts: dict[str, int] = {}
    for items in folders.values():
        for it in items:
            counts[it["status"]] = counts.get(it["status"], 0) + 1

    return {
        "root": roots[0].as_posix() if roots else "",
        "roots": [r.as_posix() for r in roots],
        "groups": groups,
        "total": seen,
        "brands": brands(conn),
        "counts": counts,
        "scope": scope,
    }
