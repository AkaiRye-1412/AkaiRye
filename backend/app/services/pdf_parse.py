"""PDF 解析服务（M9 · FR-RAG-05/06）：文本抽取 → 章节识别 → 页码感知切块。

设计取向（2026-11-27）：
- **引擎**：需求原定本地 MinerU，但 MinerU 需独立 venv + torch（数 GB），当前环境未部署。
  这里先用 PyMuPDF（已随环境安装、零额外依赖）完成文本型 PDF 的解析，作为默认引擎；
  MinerU 作为可选增强留在 `engine` 字段上，后续接入时只需替换 `_extract_lines`。
- **章节识别**：不依赖 PDF 书签（大量售后手册没有 TOC），用「字号相对正文的倍数 +
  加粗 + 编号/章节关键词」三路启发式；页眉页脚按「跨页重复文本」过滤。
- **切块**：按章节聚合，再按页码与字符预算贪心切分，每块保留 `title_path` 与起始页码，
  供 RAG 引用跳页（FR-RAG-24）；扫描件（无文本层）明确报错而不是入库空块。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 切块参数：手册正文信息密度高，块比笔记略小，命中后引用更精准
CHUNK_MAX_CHARS = 900
CHUNK_MIN_CHARS = 12
# 每页平均字符数低于此值按「扫描件」处理（文本层缺失/只有页码水印）
SCANNED_CHARS_PER_PAGE = 30
# 章节标题最长字符数（超过基本是正文段落）
HEAD_MAX_CHARS = 80
# 页眉页脚：同一文本在 >= 该页数出现即视为页眉页脚
REPEAT_PAGE_THRESHOLD = 3

# 「第 3 章」「Chapter 4」「附录 A」等
CHAPTER_RE = re.compile(
    r"^(?:第\s*[0-9一二三四五六七八九十百零]+\s*[章节篇部]|chapter\s+\d+|附录\s*[A-Za-z0-9一二三四五六七八九十])",
    re.IGNORECASE,
)
# 「3.1 参数说明」「2.3.4 接线」等编号标题
NUMBER_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、．.]\s*(\S.{0,60})$")
# 标题不应以句末标点结尾
SENTENCE_END = tuple("。；;，,、：:")


class PdfNoTextError(ValueError):
    """PDF 没有可抽取的文本层（扫描件/纯图片）。"""


@dataclass
class ParsedPdf:
    """解析结果：切块 + 目录 + 统计（不含数据库操作）。"""

    title: str
    pages: int
    chars: int
    engine: str
    scanned: bool
    chunks: list[dict] = field(default_factory=list)
    toc: list[dict] = field(default_factory=list)
    scanned_pages: list[int] = field(default_factory=list)


@dataclass
class _Line:
    page: int
    text: str
    size: float
    bold: bool


def _import_pymupdf():
    """PyMuPDF 2.x 用 `pymupdf`，老版本只有 `fitz`；两者都兼容。"""
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:  # pragma: no cover - 兼容旧环境
        import fitz  # type: ignore

        return fitz


def _clean(text: str) -> str:
    """压缩空白：PDF 抽取常带多余空格/软换行。"""
    return re.sub(r"[ \t\u00a0]+", " ", text).strip()


def _extract_lines(doc) -> tuple[list[_Line], list[int]]:
    """逐页抽取「行」：合并同一行内的 span，记录字号与加粗，便于标题判定。"""
    lines: list[_Line] = []
    empty_pages: list[int] = []
    for pno in range(doc.page_count):
        page = doc[pno]
        page_lines: list[_Line] = []
        try:
            data = page.get_text("dict")
        except Exception:  # noqa: BLE001 —— 单页坏数据不拖垮整本
            logger.warning("第 %d 页文本抽取失败，跳过", pno + 1)
            empty_pages.append(pno + 1)
            continue
        for block in data.get("blocks", []):
            if block.get("type") != 0:  # 图片块不参与（FR-RAG-14 一期不索引图片）
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if _clean(s.get("text", ""))]
                if not spans:
                    continue
                text = _clean("".join(s.get("text", "") for s in spans))
                if not text:
                    continue
                size = max(float(s.get("size", 0)) for s in spans)
                bold = any(int(s.get("flags", 0)) & 16 for s in spans)
                page_lines.append(_Line(page=pno + 1, text=text, size=size, bold=bold))
        if not page_lines:
            empty_pages.append(pno + 1)
        lines.extend(page_lines)
    return lines, empty_pages


def _body_size(lines: list[_Line]) -> float:
    """正文基准字号：按字符数加权的众数（页眉页脚/表格噪声占比小）。"""
    weight: dict[float, int] = {}
    for ln in lines:
        key = round(ln.size, 1)
        weight[key] = weight.get(key, 0) + len(ln.text)
    if not weight:
        return 0.0
    return max(weight.items(), key=lambda kv: kv[1])[0]


def _heading_level(ln: _Line, body: float) -> int | None:
    """判定标题层级（1 最高）：章节关键词 > 编号深度 > 字号倍数 > 加粗短行。"""
    text = ln.text
    if len(text) > HEAD_MAX_CHARS:
        return None
    # 页眉页脚常见的纯数字/页码行
    if re.fullmatch(r"[-—\s]*\d{1,4}[-—\s]*", text):
        return None
    stripped = text.rstrip()
    looks_like_sentence = stripped.endswith(SENTENCE_END) and len(text) > 16

    level: int | None = None
    if CHAPTER_RE.match(text):
        level = 1
    else:
        m = NUMBER_RE.match(text)
        if m:
            depth = m.group(1).count(".") + 1
            # 「3 概述」→2（章级）；「3.1」→2（章下第一节）；「3.1.1」→3
            level = min(max(depth, 2), 4)
    if level is None and body > 0:
        ratio = ln.size / body
        if ratio >= 1.55:
            level = 1
        elif ratio >= 1.28:
            level = 2
        elif ratio >= 1.12:
            level = 3
    if level is None and ln.bold and len(text) <= 30 and not looks_like_sentence:
        level = 3
    if level is not None and looks_like_sentence:
        return None
    return level


def _repeated_lines(lines: list[_Line]) -> set[str]:
    """跨页重复的文本（页眉/页脚/水印）：同一行在 >=3 页出现即剔除。"""
    pages_of: dict[str, set[int]] = {}
    for ln in lines:
        if len(ln.text) > 60:
            continue
        key = re.sub(r"\d+", "#", ln.text)  # 页码数字归一化，识别「第 # 页」
        pages_of.setdefault(key, set()).add(ln.page)
    return {k for k, pages in pages_of.items() if len(pages) >= REPEAT_PAGE_THRESHOLD}


def _norm_key(text: str) -> str:
    return re.sub(r"\d+", "#", text)


def _split_lines(lines: list[tuple[int, str]], max_chars: int = CHUNK_MAX_CHARS) -> list[tuple[int, list[str]]]:
    """把一节正文按字符预算切段：优先在句末断，避免把一句话劈开。

    返回 [(起始页, 行列表)]；行列表已保证长度 <= max_chars（超长单行按标点硬切）。
    """
    out: list[tuple[int, list[str]]] = []
    buf: list[str] = []
    size = 0
    start_page = lines[0][0] if lines else 1

    def flush() -> None:
        nonlocal buf, size, start_page
        if buf:
            out.append((start_page, buf))
        buf, size = [], 0

    for page, text in lines:
        # 超长单行（表格/无换行段落）先按标点硬切
        while len(text) > max_chars:
            cut = max(text.rfind(ch, 0, max_chars) for ch in "。！？；.!?;，,") + 1
            if cut <= 0:
                cut = max_chars
            piece, text = text[:cut], text[cut:]
            if buf:
                flush()
            out.append((page, [piece]))
        if buf and size + len(text) > max_chars:
            flush()
        if not buf:
            start_page = page
        buf.append(text)
        size += len(text)
    flush()
    return out


def parse_pdf(path: str | Path, *, max_pages: int = 4000) -> ParsedPdf:
    """解析 PDF → 章节感知的切块列表。

    异常：文件不存在抛 FileNotFoundError；加密/损坏抛 ValueError；
    无文本层（扫描件）抛 PdfNoTextError——由调用方把 manual 置 failed 并给出可读原因。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"PDF 不存在：{p}")

    pymupdf = _import_pymupdf()
    try:
        doc = pymupdf.open(p)
    except Exception as e:  # noqa: BLE001 —— 损坏/加密统一转成业务可读错误
        raise ValueError(f"PDF 无法打开（可能损坏或加密）：{e}") from e

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("PDF 已加密，请先解除密码保护再入库")
        if doc.page_count > max_pages:
            raise ValueError(f"PDF 页数过多（{doc.page_count} > {max_pages}），请拆分后再入库")

        lines, empty_pages = _extract_lines(doc)
        total_chars = sum(len(ln.text) for ln in lines)
        pages = max(doc.page_count, 1)
        avg_chars = total_chars / pages
        scanned = avg_chars < SCANNED_CHARS_PER_PAGE
        # 扫描件判据用「每页平均字符」而不是总量：几页的短文档也可能真的只有几十字，
        # 但整本平均下来几乎没有文本，才是缺文本层（图片版）。
        if scanned or total_chars < 40:
            raise PdfNoTextError(
                f"PDF 没有可抽取的文本层（{pages} 页仅 {total_chars} 字符，"
                f"平均 {avg_chars:.0f} 字符/页），可能是扫描件/图片版；"
                "当前版本先支持文本型 PDF"
            )

        meta_title = _clean(str((doc.metadata or {}).get("title") or ""))
        body = _body_size(lines)
        repeated = _repeated_lines(lines)

        # ---- 按标题切节（标题栈维护层级路径） ----
        sections: list[dict] = []
        stack: list[tuple[int, str]] = []
        current = {"title_path": "", "page": lines[0].page if lines else 1, "lines": []}
        toc: list[dict] = []

        for ln in lines:
            if len(ln.text) <= 60 and _norm_key(ln.text) in repeated:
                continue  # 页眉页脚
            level = _heading_level(ln, body)
            if level is not None:
                # 收束上一节
                if current["lines"] or current["title_path"]:
                    sections.append(current)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, ln.text))
                title_path = " / ".join(t for _, t in stack)
                toc.append({"title": ln.text, "level": level, "page": ln.page, "path": title_path})
                current = {"title_path": title_path, "page": ln.page, "lines": [(ln.page, ln.text)]}
            else:
                current["lines"].append((ln.page, ln.text))
        if current["lines"] or current["title_path"]:
            sections.append(current)

        # ---- 节内切块 ----
        chunks: list[dict] = []
        for sec in sections:
            title_path = sec["title_path"]
            # 每节第一行就是该节标题（构建 section 时写入），保留在正文里让块自解释
            for page, part_lines in _split_lines(sec["lines"]):
                content = "\n".join(part_lines).strip()
                if len(content) < CHUNK_MIN_CHARS:
                    continue
                chunks.append(
                    {"seq": len(chunks), "title_path": title_path or None, "page": page, "content": content}
                )

        return ParsedPdf(
            title=meta_title or p.stem,
            pages=doc.page_count,
            chars=total_chars,
            engine="pymupdf",
            scanned=scanned,
            chunks=chunks,
            toc=toc,
            scanned_pages=empty_pages[:50],
        )
    finally:
        doc.close()


def probe_pdf(path: str | Path) -> dict:
    """轻量探测（不切块）：页数、字符数、是否扫描件、是否加密。

    用于目录扫描时给出页数与健康度提示，避免扫描阶段就对每本 PDF 做完整解析。
    """
    p = Path(path)
    pymupdf = _import_pymupdf()
    try:
        doc = pymupdf.open(p)
    except Exception as e:  # noqa: BLE001
        return {"pages": None, "chars": 0, "scanned": False, "error": f"无法打开：{e}"[:200]}
    try:
        if doc.is_encrypted and not doc.authenticate(""):
            return {"pages": doc.page_count, "chars": 0, "scanned": False, "error": "已加密"}
        chars = 0
        for pno in range(min(doc.page_count, 8)):  # 抽样前 8 页估算
            chars += len(_clean(doc[pno].get_text("text")))
        est = chars / max(min(doc.page_count, 8), 1) * doc.page_count
        return {
            "pages": doc.page_count,
            "chars": int(est),
            "scanned": doc.page_count > 0 and est / max(doc.page_count, 1) < SCANNED_CHARS_PER_PAGE,
            "error": "",
        }
    finally:
        doc.close()
