"""草稿箱（右侧 dock 的第二块面板）：/api/drafts/*。

定位：右侧窄轨「草稿箱」展开后的一张"大纸"，边写边自动保存。
- 文件位置见 `paths.draft_roots`（设置页「草稿箱索引位置」）；留空则落在
  「个人笔记索引位置」第 1 个目录下的 `草稿箱/` 子目录（vault.draft_dir）；
- 每条速记一个 .md，纯正文、不带 frontmatter（大纸风格，第一行即标题）；
- 文件名跟着纸上的**一级标题**走：`# 本地部署` → `本地部署2026-11-28.md`，每次
  自动保存后都会重新对齐（标题没写/被删就保持原名，不会退回到时间戳名字）；
- 刻意**不**写 note_file、不触发索引：草稿是写到一半的纸，不该混进个人笔记
  列表，也不该被知识助手检索到（rescan / watchdog 已同步跳过该目录）。
"""

import re
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_db
from app.services import vault

router = APIRouter(prefix="/api/drafts", tags=["drafts"])

# 首行取标题时剥掉的 Markdown 记号
_TITLE_STRIP = "#>*-+` \t"

# 一级标题行（`# 标题`，允许前后空格和收尾的 `#`）
_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$")
# 文件名里的日期（本地部署2026-11-28）
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# 文件名里的时分（撞名时补的 17-46）
_TIME_RE = re.compile(r"\b\d{2}-\d{2}\b")
# Windows 文件名非法字符 + 控制符
_BAD_STEM = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class SaveDraftBody(BaseModel):
    path: str
    content: str = ""


class CreateDraftBody(BaseModel):
    content: str = ""


def _draft_root(conn) -> Path:
    """草稿箱目录；vault 路径未配置时给明确提示（而不是 500）。"""
    try:
        return vault.draft_dir(conn)
    except (KeyError, OSError) as exc:  # 未配置 vault_root
        raise HTTPException(
            status_code=400,
            detail={"code": "path_invalid", "message": "vault 路径未配置，无法使用草稿箱"},
        ) from exc


def _safe_draft(conn, path: str) -> Path:
    """只允许草稿箱目录内的 .md（防目录穿越）。"""
    root = _draft_root(conn)
    p = Path(path)
    target = p.resolve() if p.is_absolute() else (root / path).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(
            status_code=400,
            detail={"code": "path_outside_root", "message": "路径越出草稿箱目录"},
        )
    if target.suffix.lower() != ".md":
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": "草稿必须是 .md 文件"},
        )
    return target


def _title_of(path: Path, text: str) -> str:
    """第一行非空内容即标题（剥掉 # / - 等记号），没有就退回文件名。"""
    for line in text.splitlines():
        s = line.strip().lstrip(_TITLE_STRIP).strip()
        if s:
            return s[:40]
    return path.stem


def _preview_of(text: str) -> str:
    """标题行之后的第二行非空内容，用于历史列表的第二行说明。"""
    seen_title = False
    for line in text.splitlines():
        s = line.strip().lstrip(_TITLE_STRIP).strip()
        if not s:
            continue
        if not seen_title:
            seen_title = True
            continue
        return s[:60]
    return ""


def _heading_of(text: str) -> str:
    """第一条一级标题（`# xxx`）的纯文本；没写一级标题返回空串。"""
    for line in text.splitlines():
        m = _H1_RE.match(line)
        if not m:
            continue
        title = m.group(1).strip().strip("*_`~ ").strip()
        if title:
            return title
    return ""


def _stem_from_heading(text: str, current: Path | None = None) -> str:
    """文件名主干 = 一级标题 + 日期（例：本地部署2026-11-28）；没标题返回空串。

    日期优先沿用这张纸文件名里原来的那一天——跨天接着写同一张草稿时，
    不该因为我保存得晚了一天就再改一次名。
    """
    title = _BAD_STEM.sub(" ", _heading_of(text))
    title = re.sub(r"\s+", " ", title).strip(" .")
    title = title[:40].strip(" .")
    if not title:
        return ""
    date = ""
    if current is not None:
        m = _DATE_RE.search(current.stem)
        date = m.group(0) if m else ""
    return f"{title}{date or datetime.now().strftime('%Y-%m-%d')}"


def _row(path: Path, text: str) -> dict:
    st = path.stat()
    return {
        "path": path.as_posix(),
        "title": _title_of(path, text),
        "preview": _preview_of(text),
        "updated_at": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
        "size": st.st_size,
    }


@router.get("")
def list_drafts(conn: Connection = Depends(get_db)) -> list[dict]:
    """草稿列表：按最后修改时间倒序（目录不存在 = 空列表，不报错）。"""
    root = _draft_root(conn)
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in root.glob("*.md"):
        try:
            text = p.read_bytes().decode("utf-8", errors="replace")
            out.append(_row(p, text))
        except OSError:
            continue  # 正在被其他程序占用/刚被删除：跳过这一条，不影响整张列表
    out.sort(key=lambda r: r["updated_at"], reverse=True)
    return out


@router.get("/content")
def read_draft(path: str = Query(...), conn: Connection = Depends(get_db)) -> dict:
    target = _safe_draft(conn, path)
    if not target.is_file():
        raise HTTPException(
            status_code=404, detail={"code": "draft_not_found", "message": "草稿不存在"}
        )
    text = target.read_bytes().decode("utf-8", errors="replace")
    return {**_row(target, text), "content": text}


def _unique_path(root: Path, stem: str, keep: Path | None = None) -> Path:
    """同名文件已存在就加序号；`keep` 是自己时不算冲突（改名场景）。"""
    target = root / f"{stem}.md"
    n = 2
    while target.exists() and target != keep:
        target = root / f"{stem} ({n}).md"
        n += 1
    return target


def _rename_by_heading(root: Path, target: Path, text: str) -> Path:
    """按一级标题把草稿改名（每次保存都对齐一次）；返回最终的文件路径。

    - 没写一级标题：保持现有名字（空白纸不会被改成一串奇怪的名字）；
    - 标题撞车（同一天两篇同名）：先补时分，再撞才加序号；
    - 改名失败（文件被编辑器/杀软占着）：先用旧名字，下次保存再试。
    """
    stem = _stem_from_heading(text, target)
    if not stem:
        return target
    want = root / f"{stem}.md"
    if want == target:
        return target
    if want.exists():
        # 原名里的时分（没有就用当前时间），避免和已有的同名草稿抢名字
        rest = _DATE_RE.sub("", target.stem, count=1)
        m = _TIME_RE.search(rest)
        hhmm = m.group(0) if m else datetime.now().strftime("%H-%M")
        want = _unique_path(root, f"{stem} {hhmm}", keep=target)
        if want == target:
            return target
    try:
        target.rename(want)
    except OSError:
        return target
    return want


@router.post("", status_code=201)
def create_draft(body: CreateDraftBody, conn: Connection = Depends(get_db)) -> dict:
    """新建一页：优先按一级标题命名，还没写标题时用「速记 + 时间戳」。"""
    root = _draft_root(conn)
    root.mkdir(parents=True, exist_ok=True)
    stem = _stem_from_heading(body.content) or f"速记 {datetime.now().strftime('%Y-%m-%d %H-%M')}"
    target = _unique_path(root, stem)
    target.write_bytes(body.content.encode("utf-8"))
    return {**_row(target, body.content), "content": body.content}


@router.put("")
def save_draft(body: SaveDraftBody, conn: Connection = Depends(get_db)) -> dict:
    """自动保存：直接覆盖写入；写完顺带按一级标题对齐文件名。

    草稿以面板内容为唯一真相，不做冲突三选；返回的 path 是（可能改名后的）
    最终路径，前端必须用它替换手里的旧路径。
    """
    target = _safe_draft(conn, body.path)
    if not target.is_file():
        raise HTTPException(
            status_code=404, detail={"code": "draft_not_found", "message": "草稿不存在"}
        )
    # 以字节写入，避免 Windows 的 \r\n 翻译在反复保存时累积成 \r\r\n
    target.write_bytes(body.content.encode("utf-8"))
    target = _rename_by_heading(target.parent, target, body.content)
    return _row(target, body.content)


@router.delete("")
def delete_draft(path: str = Query(...), conn: Connection = Depends(get_db)) -> dict:
    """删除草稿：直接删文件（草稿没有回收价值，不做 .trash 中转）。"""
    target = _safe_draft(conn, path)
    if not target.is_file():
        raise HTTPException(
            status_code=404, detail={"code": "draft_not_found", "message": "草稿不存在"}
        )
    target.unlink()
    return {"deleted": True, "path": target.as_posix()}
