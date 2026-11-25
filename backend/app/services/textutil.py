"""文本工具（M3/M9 共用）：bigram 分词、FTS 短语表达式、Markdown 切块。

分词实现来自 V2 spike 定案（backend/tests/spike_v2_fts.py / database-rules 第 10 节）：
- 中文按两字一 token（bigram），ASCII 连续串（型号/故障码）整体保留；
- **分词必须保持原文顺序**（否则 FTS5 短语邻接失效）；
- 查询侧每段各自成短语（段内 bigram 邻接），多段 OR 连接，token 加双引号（"-" 是 MATCH 特殊字符）。
"""

from __future__ import annotations

import hashlib
import re

# 型号/故障码整体保留：字母数字加 -_. 连接（ACS-2600i / ERR-1204 / v1.2）
ASCII_RE = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")
# 分段：ASCII 连续串 | 中文连续段
SEG_RE = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*|[\u4e00-\u9fff]+")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# 切块参数（规模内优先简单性：单块上限，超过按段落拆分）
MAX_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 4  # 仅过滤空节/纯分隔噪声，避免误伤短小节


def bigram_tokens(text: str) -> str:
    """索引侧：中文 bigram + ASCII 原样，按原文顺序，空格连接。"""
    tokens: list[str] = []
    for m in SEG_RE.finditer(text):
        seg = m.group()
        if ASCII_RE.fullmatch(seg):
            tokens.append(seg)
        elif len(seg) == 1:
            tokens.append(seg)
        else:
            tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
    return " ".join(tokens)


def phrase_expr(q: str) -> str:
    """查询侧：bigram 逐词 OR 表达式。

    演进说明：
    - V1 spike：所有段合并为单个短语——混合查询必 0 命中，废弃；
    - V2：分段短语 OR——仅按中文/英文边界分段，"介绍下集合"这类纯中文查询仍是
      一个整段短语（要求"介绍 绍下 下集 集合"四个 bigram 在原文紧邻），
      而笔记里没有该原句，实测 0 命中（2026-11-26 用户反馈）；
    - V3（当前）：每个 bigram 独立成短语后 OR。索引侧 bigram 本就是独立 token，
      查询逐词 OR 与之对称；跨词噪声 bigram（绍下/下集）索引中通常不存在，
      不会引入误命中；相关性排序由 FTS5 bm25（多词命中优先）保证。
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in SEG_RE.finditer(q):
        seg = m.group()
        if ASCII_RE.fullmatch(seg) or len(seg) == 1:
            cand = [seg]
        else:
            cand = [seg[i : i + 2] for i in range(len(seg) - 1)]
        for t in cand:
            if t not in seen:
                seen.add(t)
                out.append(f'"{t}"')
    if not out:
        return '""'
    return " OR ".join(out)


def _split_long(text: str, max_chars: int) -> list[str]:
    """超长块按空行段落累积拆分。"""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for para in re.split(r"\n\s*\n", text):
        if buf and size + len(para) > max_chars:
            parts.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += len(para) + 2  # 段落分隔符计入，避免累积长度失真
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def content_hash_text(text: str) -> str:
    """块级内容哈希（增量索引比对用）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_frontmatter(text: str) -> str:
    m = _FRONTMATTER_RE.match(text)
    return text[m.end() :] if m else text


def chunk_note_markdown(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[dict]:
    """售后笔记切块：按标题分节（title_path 记录标题层级路径），超长按段落拆。

    售后笔记形态为 `# 标题` + `## 日期小节`（FR-NOTE-08），按 `## ` 分节
    正好对应一次现场处理记录，是天然检索单元；无标题正文整文为一块。
    返回 [{seq, title_path, content}]（content 含所属标题行，保证切块自含语义）。
    """
    body = strip_frontmatter(text).strip()
    if not body:
        return []

    # 标题栈：支持 h1..h6，title_path = 各级标题拼接
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = []  # (title_path, 行列表)
    in_fence = False
    fence_marker = ""
    for line in body.splitlines():
        stripped = line.lstrip()
        # 代码围栏跟踪（``` / ~~~）：围栏内的 # 是代码注释而非标题
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            if not sections:
                sections.append(("", []))
            sections[-1][1].append(line)
            continue
        h = None if in_fence else re.match(r"^(#{1,6})\s+(.*)$", line)
        if h:
            level = len(h.group(1))
            title_text = h.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title_text))
            title = " / ".join(t for _, t in stack)
            sections.append((title, [line]))
        else:
            if not sections:
                sections.append(("", []))
            sections[-1][1].append(line)

    chunks: list[dict] = []
    for title, lines in sections:
        content = "\n".join(lines).strip()
        if not content:
            continue
        for piece in _split_long(content, max_chars):
            piece = piece.strip()
            if len(piece) < MIN_CHUNK_CHARS:
                continue  # 过滤纯分隔线等噪声
            chunks.append({"seq": len(chunks), "title_path": title or None, "content": piece})
    return chunks
