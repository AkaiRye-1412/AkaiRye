"""vault 服务（M2 · FR-NOTE）：vault 读写、扫描缓存、冲突检测、文件监听。

规则来源：
- FR-NOTE-01 直接读写 vault 同一份 .md，不建第二份数据副本；
- FR-NOTE-02/10 新建写入"客户售后问题"文件夹，自动带固定头部骨架与日期小节格式；
- FR-NOTE-03 头部字段：name（客户）/ project（项目名）/ status（进行中|已归档）/ start_date / updated；
- FR-NOTE-05 冲突三选：重新加载 / 用我的覆盖（force）/ 另存副本；禁止静默覆盖；
- FR-NOTE-06 watchdog 监听：防抖 + 内容哈希去重（R6）→ note_file 同步 + note.changed 推送；
- FR-NOTE-07 不重排用户现有 vault 目录；命名规范沿用原型：`{notes_folder}/{客户}-{项目名}.md`。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import threading
from datetime import date
from pathlib import Path

from app.services import config_service
from app.ws.events import broker

# ---- 常量 ----------------------------------------------------------------

STATUS_TO_YAML = {"active": "进行中", "archived": "已归档"}
YAML_TO_STATUS = {"进行中": "active", "已归档": "archived", "active": "active", "archived": "archived"}

REQUIRED_HEAD_FIELDS = ("name", "project", "status", "start_date", "updated")
# v2「兼容真实卡片」口径：真实 vault 的售后卡片只有 name 必填，其余为项目笔记专用字段。
# 判定见 required_head_fields()：有 project 或 start_date 视为项目笔记，否则按卡片处理。
CARD_REQUIRED_FIELDS = ("name",)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
_H1_RE = re.compile(r"^#\s+(.+)$")


class EditConflict(Exception):
    """FR-NOTE-05：外部修改且客户端未基于最新版本保存（未带 force）。"""


class PathOutsideRoot(PermissionError):
    """api-rules 第 6 节：目标路径越出配置根目录。"""

# 防抖窗口（FR-RAG-03 同源约定 8s；watchdog 场景同样 8s 防抖）
DEBOUNCE_SECONDS = 8.0


# ---- frontmatter 解析 / 序列化 -------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, str], str, bool, list[str]]:
    """解析 Markdown 头部。返回 (fields, body, head_ok, missing)。

    仅支持扁平 `key: value` 行 + YAML 列表（`tags:` 换行 `- a` 或 `tags: [a, b]`），
    售后笔记头部即此形态，无需引入 yaml 依赖。列表项以逗号拼进同名字段。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text, False, list(CARD_REQUIRED_FIELDS)
    fields: dict[str, str] = {}
    list_owner: str | None = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = _LIST_ITEM_RE.match(line)
        if item and list_owner:
            value = _clean_scalar(item.group(1))
            if value:
                fields[list_owner] = f"{fields[list_owner]}, {value}" if fields[list_owner] else value
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = _clean_scalar(value)
        fields[key] = value
        list_owner = key if not value else None
    missing = [f for f in required_head_fields(fields) if not fields.get(f)]
    return fields, text[m.end():], not missing, missing


def _clean_scalar(value: str) -> str:
    """去引号、拆内联列表（`[a, b]` → `a, b`）。"""
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
        parts = [_clean_scalar(x) for x in v.split(",")]
        return ", ".join(x for x in parts if x)
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def is_project_note(fields: dict[str, str]) -> bool:
    """项目笔记 vs 按日期卡片：真实卡片没有 project / start_date（只有 name/time/tags/description）。"""
    return bool(fields.get("project") or fields.get("start_date"))


def required_head_fields(fields: dict[str, str]) -> tuple[str, ...]:
    return REQUIRED_HEAD_FIELDS if is_project_note(fields) else CARD_REQUIRED_FIELDS


def tags_of(fields: dict[str, str]) -> list[str]:
    raw = fields.get("tags") or fields.get("tag") or ""
    out: list[str] = []
    for part in raw.split(","):
        t = part.strip().strip("#")
        if t and t not in out:
            out.append(t)
    return out


def extract_title(fields: dict[str, str], body: str, stem: str) -> str:
    """列表标题：正文首个 H1 → 头部 title（去掉纯日期占位）→ 文件名。"""
    for line in body.splitlines():
        m = _H1_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    raw_title = (fields.get("title") or "").strip()
    if raw_title and not _DATE_IN_NAME_RE.match(raw_title.replace("-", "")):
        return raw_title
    return stem


_SUMMARY_SKIP_RE = re.compile(r"^(?:#{1,6}\s|>|\||```|~~~|!\[|<|-{3,}|\*{3,}|[_*]{2,}\s*$)")
_LIST_LINE_RE = re.compile(r"^(?:[-*+]|\d+\.)\s+")
_EMBED_RE = re.compile(r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]|\[([^\]]*)\]\([^)]*\)")


def extract_summary(body: str, limit: int = 80) -> str:
    """列表摘要：正文第一段有效文本（跳过标题 / 表格 / 引用 / 图片 / 任务列表标记）。"""
    for raw in body.splitlines():
        line = raw.strip()
        if not line or _SUMMARY_SKIP_RE.match(line):
            continue
        text = _EMBED_RE.sub("", line)
        text = _LINK_RE.sub(lambda m: m.group(2) or m.group(1) or m.group(3) or "", text)
        text = _LIST_LINE_RE.sub("", text)
        text = re.sub(r"[*_`~]", "", text).strip()
        if not text:
            continue
        return text[:limit] + ("…" if len(text) > limit else "")
    return ""


def serialize_frontmatter(fields: dict[str, str], body: str) -> str:
    lines = ["---"]
    lines.extend(f"{k}: {v}" for k, v in fields.items())
    lines.append("---")
    return "\n".join(lines) + ("\n" + body if body else "\n")


def _status_of(fields: dict[str, str]) -> str:
    return YAML_TO_STATUS.get(fields.get("status", ""), "active")


_DATE_IN_NAME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _infer_start_date(fields: dict[str, str], stem: str, mtime_ts: int) -> str | None:
    """缺失 start_date 的推断链（用户真实 vault 适配）：

    头部 start_date → 文件名 YYYYMMDD（如 20260825.md 的记录日期习惯）→
    头部 time 字段前缀日期（如 `2026-08-25-周二 23:05`）→ 文件 mtime 日期。
    """
    raw = fields.get("start_date")
    if raw:
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            pass
    m = _DATE_IN_NAME_RE.match(stem)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    time_raw = fields.get("time")
    if time_raw:
        try:
            return date.fromisoformat(time_raw[:10]).isoformat()
        except ValueError:
            pass
    return date.fromtimestamp(mtime_ts).isoformat()


def touch_updated(text: str, today: str) -> str:
    """保存时刷新 frontmatter 的 updated 字段（保持其余内容逐字不变）。"""

    def _sub(m: re.Match) -> str:
        block = re.sub(r"(?m)^updated:.*$", f"updated: {today}", m.group(1))
        return f"---\n{block}\n---\n"

    return _FRONTMATTER_RE.sub(_sub, text, count=1) if _FRONTMATTER_RE.match(text) else text


def _apply_head_updates(text: str, updates: dict[str, str]) -> str:
    """按行改写 frontmatter 指定键（缺失则追加），正文与其余键逐字不变。

    仅用于标量键（name / project / status / start_date / updated）；不触碰 tags 这类
    YAML 列表字段（改写单行会留下孤立的 `- item` 行）。无头部时新建头部块。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        block = "\n".join(f"{k}: {v}" for k, v in updates.items())
        return f"---\n{block}\n---\n\n{text}"
    block = m.group(1)
    for key, value in updates.items():
        pattern = re.compile(rf"(?m)^{re.escape(key)}:.*$")
        if pattern.search(block):
            block = pattern.sub(f"{key}: {value}", block, count=1)
        else:
            block = block.rstrip("\n") + f"\n{key}: {value}"
    tail = text[m.end(1):m.end()]  # 闭合 --- 之后的换行（判断原文空行习惯）
    gap = "\n\n" if tail.endswith("\n\n") else "\n"
    return f"---\n{block}\n---{gap}{text[m.end():]}"


def _write_text(path: Path, text: str) -> None:
    """以字节写入（禁用 universal newline 翻译）：Windows 上 write_text 会把 \n 翻译成 \r\n，
    反复保存会造成 \r\r\n 累积损坏。"""
    path.write_bytes(text.encode("utf-8"))


def new_note_template(customer: str, project_name: str, start_date: str) -> str:
    """FR-NOTE-10：固定头部字段骨架 + 售后卡片一致的正文小节。

    正文必须与 vault 里既有的售后卡片（`# 标题` + `> 售后支持卡片 |` + `## 小节`）同构，
    否则预览里新建笔记会变成「**粗体标签** + --- 分隔线」的扁平表单样式，和老笔记观感割裂。
    改动此处请同步 frontend/src/views/notes/NotesView.vue 的 newContentPreview()。
    """
    fields = {
        "name": customer,
        "project": project_name,
        "status": "进行中",
        "start_date": start_date,
        "updated": start_date,
    }
    body = (
        f"# {project_name}\n\n"
        "> 售后支持卡片 |\n"
        "## 现象\n\n"
        "## 配置\n\n"
        "## 排查步骤\n\n"
        "## 注意：\n\n"
        "## 后续：\n"
    )
    return serialize_frontmatter(fields, body)


# ---- 路径与哈希 -----------------------------------------------------------

def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def notes_dir(conn) -> Path:
    paths_cfg = config_service.get_raw(conn, "paths")
    vault_root = Path(paths_cfg["vault_root"]).expanduser()
    folder = paths_cfg["notes_folder"] or "客户售后问题"
    return (vault_root / folder).resolve()


def vault_root(conn) -> Path:
    paths_cfg = config_service.get_raw(conn, "paths")
    return Path(paths_cfg["vault_root"]).expanduser().resolve()


def resolve_entry(conn, entry: str) -> tuple[str, Path]:
    """带配置上下文的条目归一化（kb API 与白名单匹配共用）。"""
    e = entry.strip().replace("\\", "/").rstrip("/")
    p = Path(e)
    if p.is_absolute():
        abs_dir = p.resolve()
    elif "/" in e:
        abs_dir = (vault_root(conn) / e).resolve()
    else:
        abs_dir = (notes_dir(conn) / e).resolve()
    return abs_dir.as_posix(), abs_dir


def whitelist_abs_dirs(conn) -> list[tuple[str, Path]]:
    """全部白名单目录（去重、仅保留存在的目录），返回 [(posix_key, abs_dir)]。"""
    folders = config_service.get_raw(conn, "index.whitelist").get("folders") or []
    out: dict[str, Path] = {}
    for f in folders:
        if not f.strip():
            continue
        key, abs_dir = resolve_entry(conn, f)
        if abs_dir.is_dir():
            out.setdefault(key, abs_dir)
    return list(out.items())


def external_whitelist_dirs(conn) -> list[tuple[str, Path]]:
    """位于售后笔记文件夹之外的白名单目录（需独立扫描/监听的范围）。"""
    base = notes_dir(conn)
    return [(k, d) for k, d in whitelist_abs_dirs(conn) if not d.is_relative_to(base)]


def resolve_note_abs(conn, rel: str) -> Path:
    """note_file.path → 绝对路径。外部知识库条目以绝对 posix 路径作为主键存储。"""
    p = Path(rel)
    if p.is_absolute():
        return p
    return notes_dir(conn) / rel


def safe_rel_path(conn, rel: str) -> Path:
    """路径越界防护（api-rules 第 6 节）：notes_dir 内，或白名单外部目录内。"""
    target = resolve_note_abs(conn, rel).resolve()
    if target.is_relative_to(notes_dir(conn)):
        return target
    for _key, abs_dir in external_whitelist_dirs(conn):
        if target.is_relative_to(abs_dir):
            return target
    raise PathOutsideRoot(rel)


# ---- 扫描同步（note_file 缓存，03 文档场景 1） ----------------------------

def scan_file(conn, abs_path: Path, rel_path: str, now_iso: str) -> dict:
    """扫描单个 .md → note_file upsert，返回行 dict。"""
    data = abs_path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    fields, body, head_ok, _missing = parse_frontmatter(text)
    stat = abs_path.stat()
    updated_date = fields.get("updated") or date.fromtimestamp(stat.st_mtime).isoformat()
    try:
        updated_date = date.fromisoformat(updated_date[:10]).isoformat()
    except ValueError:
        updated_date = date.fromtimestamp(stat.st_mtime).isoformat()
    tags = tags_of(fields)
    row = {
        "path": rel_path,
        "customer": fields.get("name") or None,
        # 项目名缺失时回退为文件名（日期命名的售后卡以文件名作为条目标识）
        "project_name": fields.get("project") or abs_path.stem,
        "status": _status_of(fields),
        "start_date": _infer_start_date(fields, abs_path.stem, int(stat.st_mtime)),
        "updated_date": updated_date,
        "file_mtime": int(stat.st_mtime),
        "content_hash": content_hash(data),
        "head_ok": 1 if head_ok else 0,
        "note_title": extract_title(fields, body, abs_path.stem),
        "summary": fields.get("description") or extract_summary(body),
        "tags": ", ".join(tags),
        "note_mode": "project" if is_project_note(fields) else "card",
        "scanned_at": now_iso,
    }
    conn.execute(
        """
        INSERT INTO note_file (path, customer, project_name, status, start_date, updated_date,
                               file_mtime, content_hash, head_ok, note_title, summary, tags,
                               note_mode, scanned_at)
        VALUES (:path, :customer, :project_name, :status, :start_date, :updated_date,
                :file_mtime, :content_hash, :head_ok, :note_title, :summary, :tags,
                :note_mode, :scanned_at)
        ON CONFLICT(path) DO UPDATE SET
            customer=excluded.customer, project_name=excluded.project_name,
            status=excluded.status, start_date=excluded.start_date,
            updated_date=excluded.updated_date, file_mtime=excluded.file_mtime,
            content_hash=excluded.content_hash, head_ok=excluded.head_ok,
            note_title=excluded.note_title, summary=excluded.summary, tags=excluded.tags,
            note_mode=excluded.note_mode, scanned_at=excluded.scanned_at
        """,
        row,
    )
    return row


def rescan_all(conn) -> int:
    """全量重扫 notes 目录 + 外部白名单目录 → note_file（NFR-DATA-01）。返回笔记数。

    note_file.path 主键：notes_dir 内为相对路径；外部白名单目录内为绝对 posix 路径
    （2026-11-26 扩展，售后看板/列表按 is_absolute 过滤，互不污染）。
    """
    from datetime import datetime, timezone

    base = notes_dir(conn)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seen: set[str] = set()
    count = 0

    jobs: list[tuple[Path, Path]] = []  # (abs_path, rel_key)
    if base.is_dir():
        for abs_path in sorted(base.rglob("*.md")):
            if abs_path.is_file():
                jobs.append((abs_path, abs_path.relative_to(base).as_posix()))
    for _key, ext_dir in external_whitelist_dirs(conn):
        for abs_path in sorted(ext_dir.rglob("*.md")):
            if abs_path.is_file():
                jobs.append((abs_path, abs_path.as_posix()))

    for abs_path, rel in jobs:
        if rel in seen:
            continue
        seen.add(rel)
        scan_file(conn, abs_path, rel, now_iso)
        count += 1

    # 清理已删除文件对应的缓存行（vault 唯一真相源：块三件套随笔记行一并删除）
    from app.services import indexer

    cached = {r["path"] for r in conn.execute("SELECT path FROM note_file").fetchall()}
    for stale in cached - seen:
        indexer._delete_chunks(conn, stale)
        conn.execute("DELETE FROM note_file WHERE path=?", (stale,))
    conn.commit()
    return count


# ---- 读写 / 新建 / 冲突 ---------------------------------------------------

def read_note(conn, rel: str) -> dict:
    target = safe_rel_path(conn, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    data = target.read_bytes()
    text = data.decode("utf-8", errors="replace")
    fields, body, head_ok, missing = parse_frontmatter(text)
    return {
        "path": rel,
        "content": text,
        "fields": fields,
        "head_ok": head_ok,
        "missing_fields": missing,
        "mtime": int(target.stat().st_mtime),
        "content_hash": content_hash(data),
        "note_title": extract_title(fields, body, target.stem),
        "summary": fields.get("description") or extract_summary(body),
        "tags": tags_of(fields),
        "note_mode": "project" if is_project_note(fields) else "card",
    }


def create_note(conn, customer: str, project_name: str, start_date: str) -> dict:
    """FR-NOTE-02/08/10：一项目一笔记，按命名规范落盘；重名 409 path_conflict（api-rules）。"""
    base = notes_dir(conn)
    if not base.is_dir():
        raise NotADirectoryError(str(base))
    rel = f"{customer}-{project_name}.md"
    target = base / rel
    if target.exists():
        raise FileExistsError(rel)
    text = new_note_template(customer, project_name, start_date)
    _write_text(target, text)
    from datetime import datetime, timezone

    row = scan_file(conn, target, rel, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    conn.commit()
    broker.publish("note.changed", {"path": rel, "change": "created",
                                    "customer": customer, "project": project_name})
    return row


def save_note(conn, rel: str, content: str, base_hash: str | None, force: bool) -> dict:
    """FR-NOTE-05 冲突语义：文件当前 hash ≠ 客户端加载时 hash 且未 force → 409 edit_conflict。

    - force=True = 「用我的覆盖」；
    - 落盘后刷新 note_file 缓存并广播 note.changed（M3 接入后由防抖触发增量索引）。
    """
    from datetime import datetime, timezone

    target = safe_rel_path(conn, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    current = content_hash(target.read_bytes())
    if base_hash is not None and current != base_hash and not force:
        raise EditConflict(rel)
    today = date.today().isoformat()
    text = touch_updated(content, today)
    _write_text(target, text)
    row = scan_file(conn, target, rel, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    conn.commit()
    broker.publish("note.changed", {"path": rel, "change": "modified",
                                    "customer": row["customer"], "project": row["project_name"]})
    return row


def save_as(conn, rel: str, content: str, suggested_rel: str | None = None) -> dict:
    """冲突三选之三：另存副本，不动原文件（FR-NOTE-05）。"""
    from datetime import datetime, timezone

    base = notes_dir(conn)
    safe_rel_path(conn, rel)  # 原文件也须在范围内
    candidate = suggested_rel or rel
    target = (base / candidate).resolve()
    if not target.is_relative_to(base):
        raise PathOutsideRoot(candidate)  # 越界防护：建议路径同样受限
    if target.suffix != ".md":
        raise ValueError("副本必须是 .md 文件")
    stem, suffix = target.stem, target.suffix or ".md"
    n = 1
    while target.exists():
        target = target.with_name(f"{stem}-副本{n}{suffix}")
        n += 1
    new_rel = target.relative_to(base).as_posix()
    text = touch_updated(content, date.today().isoformat())
    _write_text(target, text)
    row = scan_file(conn, target, new_rel, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    conn.commit()
    broker.publish("note.changed", {"path": new_rel, "change": "created",
                                    "customer": row["customer"], "project": row["project_name"]})
    return row


def set_status(conn, rel: str, status: str) -> dict:
    """FR-NOTE-09：归档/恢复 —— 仅改头部 status 字段，不触碰正文。

    v2：真实售后卡片没有 status 字段，归档时自动补写该键（不要求先跑存量整理）。
    """
    from datetime import datetime, timezone

    target = safe_rel_path(conn, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    if status not in STATUS_TO_YAML:
        raise ValueError("status 仅支持 active / archived")
    text = target.read_text(encoding="utf-8")
    text = _apply_head_updates(text, {"status": STATUS_TO_YAML[status]})
    _write_text(target, text)
    row = scan_file(conn, target, rel, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    conn.commit()
    broker.publish("note.changed", {"path": rel, "change": "modified",
                                    "customer": row["customer"], "project": row["project_name"]})
    return row


# 头部字段 → frontmatter 键（客户沿用 name，项目名沿用 project，FR-NOTE-11）
META_KEY_MAP = {"customer": "name", "project_name": "project", "status": "status", "start_date": "start_date"}


def update_meta(
    conn, rel: str, updates: dict[str, str | None], base_hash: str | None = None, force: bool = False
) -> dict:
    """更新头部字段（FR-NOTE-03/11）：只改 frontmatter，正文逐字保留。

    冲突语义与 save_note 一致：文件当前 hash ≠ base_hash 且未 force → EditConflict。
    """
    from datetime import datetime, timezone

    target = safe_rel_path(conn, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    data = target.read_bytes()
    if base_hash is not None and content_hash(data) != base_hash and not force:
        raise EditConflict(rel)
    text = data.decode("utf-8", errors="replace")

    patch: dict[str, str] = {}
    for key, raw in updates.items():
        if raw is None:
            continue
        if key not in META_KEY_MAP:
            raise ValueError(f"不支持的头部字段：{key}")
        value = str(raw).strip()
        if key == "status":
            if value not in STATUS_TO_YAML:
                raise ValueError("status 仅支持 active / archived")
            patch["status"] = STATUS_TO_YAML[value]
        elif key == "start_date":
            try:
                patch["start_date"] = date.fromisoformat(value[:10]).isoformat()
            except ValueError as exc:
                raise ValueError("start_date 需为 YYYY-MM-DD") from exc
        else:  # customer / project_name
            if not value:
                raise ValueError("客户与项目名不能为空")
            patch[META_KEY_MAP[key]] = value
    if not patch:
        raise ValueError("没有需要更新的字段")

    patch.setdefault("updated", date.today().isoformat())
    _write_text(target, _apply_head_updates(text, patch))
    row = scan_file(conn, target, rel, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    conn.commit()
    broker.publish("note.changed", {"path": rel, "change": "modified",
                                    "customer": row["customer"], "project": row["project_name"]})
    info = read_note(conn, rel)
    info["note"] = row
    return info


# ---- 删除（FR-NOTE-12：软删除，回收站可还原） -----------------------------

TRASH_DIR_NAME = ".trash"


def delete_note(conn, rel: str) -> dict:
    """删除笔记 → 移入 vault 根目录下的 `.trash/`（Obsidian 回收站同名目录）。

    不做不可恢复的物理抹除：文件改名 `YYYYMMDD-HHMMSS-原名.md` 后移动，
    在 Obsidian 或资源管理器里拖回原目录即可还原；`.trash` 不在 notes_dir 扫描范围，
    删除后不会重新出现在列表里。
    同步清理 note_file 缓存行与块三件套（RAG 引用不留脏数据）。
    """
    from datetime import datetime

    from app.services import indexer

    target = safe_rel_path(conn, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    if target.suffix.lower() != ".md":
        raise ValueError("仅支持删除 .md 笔记")

    root = vault_root(conn)
    trash_dir = root / TRASH_DIR_NAME
    trash_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{stamp}-{target.stem}"
    dest = trash_dir / f"{stem}.md"
    seq = 2
    while dest.exists():  # 同一秒删同名文件时不覆盖
        dest = trash_dir / f"{stem}-{seq}.md"
        seq += 1
    shutil.move(str(target), str(dest))

    indexer.delete_note_index(conn, rel)
    conn.execute("DELETE FROM note_file WHERE path=?", (rel,))
    conn.commit()

    trashed_to = dest.as_posix()
    try:
        trashed_to = dest.relative_to(root).as_posix()
    except ValueError:  # 外部白名单目录里的笔记可能不在 vault 根下
        pass
    broker.publish("note.deleted", {"path": rel, "trashed_to": trashed_to})
    return {
        "deleted": True,
        "path": rel,
        "trashed_to": trashed_to,
        "deleted_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---- watchdog 监听（FR-NOTE-06，R6 防抖 + hash 去重） ----------------------

class VaultService:
    """watchdog 观察者：防抖 8s 后重扫该文件并广播 note.changed，随后触发增量索引。"""

    def __init__(self) -> None:
        self._observer = None
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    # -- 事件处理 ---------------------------------------------------------
    def _schedule_sync(self, conn_factory, rel: str, change: str) -> None:
        with self._lock:
            old = self._timers.pop(rel, None)
            if old is not None:
                old.cancel()
            timer = threading.Timer(
                DEBOUNCE_SECONDS, self._sync_one, args=(conn_factory, rel, change)
            )
            timer.daemon = True
            self._timers[rel] = timer
            timer.start()

    def _sync_one(self, conn_factory, rel: str, change: str) -> None:
        from datetime import datetime, timezone

        with self._lock:
            self._timers.pop(rel, None)
        conn = conn_factory()
        try:
            abs_path = resolve_note_abs(conn, rel)
            if abs_path.is_file():
                data = abs_path.read_bytes()
                prev = conn.execute(
                    "SELECT content_hash FROM note_file WHERE path=?", (rel,)
                ).fetchone()
                new_hash = content_hash(data)
                if prev is not None and prev["content_hash"] == new_hash:
                    return  # R6：hash 去重，误触发（编辑器原子写等）不计变更
                scan_file(conn, abs_path, rel,
                          datetime.now(timezone.utc).isoformat(timespec="seconds"))
                conn.commit()
                fields, _, _, _ = parse_frontmatter(data.decode("utf-8", errors="replace"))
                broker.publish("note.changed", {
                    "path": rel, "change": change,
                    "customer": fields.get("name"), "project": fields.get("project"),
                })
                # M3 接入：防抖后增量索引（FR-RAG-03/OQ-11；失败不拖垮监听，见下方异常捕获）
                try:
                    from app.services import indexer

                    result = indexer.index_note(conn, rel)
                    logger = logging.getLogger("workbench.vault")
                    logger.info("增量索引 %s → %s（%s 块）", rel, result["status"], result["chunks"])
                except Exception:  # noqa: BLE001
                    logging.getLogger("workbench.vault").exception("增量索引失败: %s", rel)
            else:
                conn.execute("DELETE FROM note_file WHERE path=?", (rel,))
                conn.commit()
                try:
                    from app.services import indexer

                    indexer.delete_note_index(conn, rel)
                except Exception:  # noqa: BLE001
                    logging.getLogger("workbench.vault").exception("删除索引失败: %s", rel)
                broker.publish("note.changed", {"path": rel, "change": "deleted"})
        except Exception:  # noqa: BLE001 —— 监听线程内异常不能拖垮服务
            import logging

            logging.getLogger("workbench.vault").exception("watchdog 同步失败: %s", rel)
        finally:
            conn.close()

    # -- 生命周期 ---------------------------------------------------------
    def start_watch(self, conn_factory) -> bool:
        """启动 watchdog（售后文件夹 + 外部白名单目录）；目录不存在时返回 False（设置页校验提示）。"""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:  # pragma: no cover
            return False

        conn = conn_factory()
        try:
            base = notes_dir(conn)
            ext_dirs = [d for _k, d in external_whitelist_dirs(conn)]
        finally:
            conn.close()
        if not base.is_dir():
            return False

        ext_dirs = [d for d in ext_dirs if d.is_dir()]  # 监听前过滤不存在的目录

        service = self

        class Handler(FileSystemEventHandler):
            def _rel(self, path: str) -> str | None:
                p = Path(path)
                if p.suffix != ".md":
                    return None
                if p.is_relative_to(base):
                    return p.relative_to(base).as_posix()
                for _k, ext in ext_dirs:  # 外部知识库目录 → 绝对 posix 路径作主键
                    if p.is_relative_to(ext):
                        return p.as_posix()
                return None

            def on_created(self, event):
                rel = self._rel(event.src_path)
                if rel:
                    service._schedule_sync(conn_factory, rel, "created")

            def on_modified(self, event):
                rel = self._rel(event.src_path)
                if rel:
                    service._schedule_sync(conn_factory, rel, "modified")

            def on_deleted(self, event):
                rel = self._rel(event.src_path)
                if rel:
                    service._schedule_sync(conn_factory, rel, "deleted")

            def on_moved(self, event):
                rel = self._rel(event.dest_path)
                if rel:
                    service._schedule_sync(conn_factory, rel, "moved")

        self._observer = Observer(timeout=1.0)
        self._observer.schedule(Handler(), str(base), recursive=True)
        for ext in ext_dirs:
            self._observer.schedule(Handler(), str(ext), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        return True

    def stop_watch(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=3)
            self._observer = None
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()


vault_service = VaultService()
