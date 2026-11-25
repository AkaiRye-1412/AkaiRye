"""V2 附加验证：FTS5 短语（phrase）匹配对混合 CJK+ASCII 查询的精确性。"""

import re
import sqlite3

conn = sqlite3.connect(":memory:")
ASCII_RE = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
SEG_RE = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*|[\u4e00-\u9fff]+")


def bigram_tokens(text: str) -> str:
    """按原文顺序切分：ASCII 连续段整体保留，CJK 段 bigram（顺序是短语匹配的关键）。"""
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
    """连续查询段 → 单个 FTS5 短语表达式："tok1 tok2 ..."（FTS5 对短语文本按空格切分）。"""
    out: list[str] = []
    for m in SEG_RE.finditer(q):
        seg = m.group()
        if ASCII_RE.fullmatch(seg):
            out.append(seg)
        elif len(seg) == 1:
            out.append(seg)
        else:
            out.append(" ".join(seg[i : i + 2] for i in range(len(seg) - 1)))
    return '"' + " ".join(out) + '"'


corpus = [f"客户：客户{(i % 30) + 1}厂 设备 ACS-2600i 故障 ERR-1204 无法启动 传感器 信号" for i in range(500)]
conn.execute("CREATE VIRTUAL TABLE fts USING fts5(tokens)")
for n in corpus:
    conn.execute("INSERT INTO fts (tokens) VALUES (?)", (bigram_tokens(n),))


def q_and(q: str) -> int:
    expr = " ".join(f'"{t}"' for t in bigram_tokens(q).split())
    return conn.execute("SELECT COUNT(*) FROM fts WHERE fts MATCH ?", (expr,)).fetchone()[0]


def q_phrase(q: str) -> int:
    expr = phrase_expr(q)
    n = conn.execute("SELECT COUNT(*) FROM fts WHERE fts MATCH ?", (expr,)).fetchone()[0]
    print(f"    expr={expr!r} -> {n}")
    return n


print("用例 1：客户15厂（AND vs 短语；语料中 500/30≈17）")
print("  AND  :", q_and("客户15厂"))
print("  短语 :", q_phrase("客户15厂"))

print("用例 2：ACS-2600i（全部命中=500 为正确）")
print("  AND  :", q_and("ACS-2600i"))
print("  短语 :", q_phrase("ACS-2600i"))

print("用例 3：无法启动（全部命中=500 为正确）")
print("  AND  :", q_and("无法启动"))
print("  短语 :", q_phrase("无法启动"))

# 抽查存储 token 流，确认邻接
row = conn.execute("SELECT tokens FROM fts LIMIT 1").fetchone()[0]
print("\n样例存储 token 流：", row[:60])
