"""V2 spike：FTS5 中文检索方案验证（02 文档 9.2 V2 / 风险 R2）。

对比两种方案：
  A. 默认 unicode61（按空格/标点切，中文长串不切分）；
  B. 应用层 bigram（中文两字一token，ASCII 串原样保留——database-rules 第 10 节默认方案）。

测试语料：500 篇模拟售后笔记（含客户名、型号 ACS-2600i / GK-800、故障码 ERR-1204、中文描述）。
查询：型号 / 故障码 / 中文词 / 中文短语。
运行：python backend/tests/spike_v2_fts.py
"""

import random
import re
import statistics
import sqlite3
import time

conn = sqlite3.connect(":memory:")

# ---------- 语料 ----------
random.seed(42)
MODELS = ["ACS-2600i", "ACS-3800", "GK-800", "GK-1200PRO", "Agito-S200", "Agito-X1"]
FAULTS = ["ERR-1204", "E-0301", "F21", "AL-099"]
PHENOMENA = [
    "设备无法启动，指示灯不亮",
    "升降过程中异响明显",
    "传感器信号丢失导致停机",
    "遥控器连接后无法控制",
    "液压系统压力不足",
    "屏幕显示通讯错误",
]
SOLUTIONS = [
    "更换驱动板后恢复正常",
    "重新插拔传感器接线，故障消除",
    "升级固件至最新版本解决",
    "调整液压阀压力参数后正常",
    "更换遥控器电池并重新配对",
]


def gen_note(i: int) -> str:
    return (
        f"## 2026-11-{(i % 28) + 1:02d} 现场问题\n\n"
        f"客户：客户{(i % 30) + 1}厂  设备型号：{random.choice(MODELS)}  故障码：{random.choice(FAULTS)}\n\n"
        f"现象：{random.choice(PHENOMENA)}。\n"
        f"处理：{random.choice(SOLUTIONS)}。后续跟进{random.choice(['一周内', '三天内', '本月内'])}回访。\n"
    )


notes = [gen_note(i) for i in range(500)]

# ---------- 分词器 ----------
ASCII_RE = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")  # 型号/故障码整体保留
SEG_RE = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*|[\u4e00-\u9fff]+")


def bigram_tokens(text: str) -> str:
    """database-rules 第 10 节：中文 bigram；ASCII 连续串原样保留；按原文顺序（短语匹配关键）。"""
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
    """连续查询段 → 单个 FTS5 短语表达式（避免多 token 独立 AND 的过匹配）。"""
    out: list[str] = []
    for m in SEG_RE.finditer(q):
        seg = m.group()
        if ASCII_RE.fullmatch(seg) or len(seg) == 1:
            out.append(seg)
        else:
            out.append(" ".join(seg[i : i + 2] for i in range(len(seg) - 1)))
    return '"' + " ".join(out) + '"'


# ---------- 建两套索引 ----------
conn.execute("CREATE VIRTUAL TABLE fts_default USING fts5(content)")
conn.execute("CREATE VIRTUAL TABLE fts_bigram USING fts5(tokens)")
t0 = time.perf_counter()
for note in notes:
    conn.execute("INSERT INTO fts_default (content) VALUES (?)", (note,))
    conn.execute("INSERT INTO fts_bigram (tokens) VALUES (?)", (bigram_tokens(note),))
conn.commit()
print(f"[0] 索引 500 篇笔记：默认 {(time.perf_counter()-t0)*500:.0f}ms/500 篇量级（插入含两次写）")

# ---------- 查询用例 ----------
def q_default(q: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM fts_default WHERE fts_default MATCH ?", (f'"{q}"',)).fetchone()[0]


def q_bigram(q: str) -> int:
    # 连续段合并为短语（客户15厂 应精确而非 客户 AND 15 AND 厂 的过匹配）
    return conn.execute("SELECT COUNT(*) FROM fts_bigram WHERE fts_bigram MATCH ?", (phrase_expr(q),)).fetchone()[0]


CASES = [
    ("型号精确", "ACS-2600i"),
    ("型号模糊大小写", "acs-2600i"),
    ("故障码", "ERR-1204"),
    ("故障码短码", "E-0301"),
    ("中文词-设备", "无法启动"),
    ("中文词-部件", "传感器"),
    ("中文短语", "液压系统压力不足"),
    ("中文短语-跨词", "压力不足"),
    ("客户名", "客户15厂"),
]

print(f"\n{'用例':<12}{'期望命中':>8}{'默认 unicode61':>15}{'bigram':>10}")
for name, q in CASES:
    # 期望命中：语料生成规律决定（型号 1/6、故障码 1/4、现象 1/6、处理 1/5、客户 1/30）
    d = q_default(q)
    b = q_bigram(q)
    print(f"{name:<12}{'':>8}{d:>12}{b:>10}")

# ---------- 延迟 ----------
lat = []
for _ in range(50):
    t0 = time.perf_counter()
    q_bigram("无法启动")
    lat.append((time.perf_counter() - t0) * 1000)
print(f"\n[延迟] bigram MATCH 中文词 50 次：中位 {statistics.median(lat):.2f}ms / 最大 {max(lat):.2f}ms")

lat = []
for _ in range(50):
    t0 = time.perf_counter()
    q_bigram("ACS-2600i")
    lat.append((time.perf_counter() - t0) * 1000)
print(f"[延迟] bigram MATCH ASCII 型号 50 次：中位 {statistics.median(lat):.2f}ms / 最大 {max(lat):.2f}ms")
