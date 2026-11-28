"""给第三方 GGUF 补 ``<arch>.pooling_type`` 元数据，让 Ollama 认得它是向量模型。

背景
----
Ollama 判断一个模型能不能做 embedding，看的是 GGUF 里 ``<arch>.pooling_type``。
从 HuggingFace / ModelScope 直接下载、或用通用脚本转换出来的 Qwen3-Embedding
往往缺这一条，于是 ``/api/embed`` 返回::

    501 {"error":"This server does not support embeddings. Start it with `--embeddings`"}

那句 ``--embeddings`` 是误导（ollama serve 根本没有这个开关），真正原因是模型
没被识别成 embedding-capable。补上这条 KV 后 ``ollama create`` 出来的模型
``capabilities`` 里就会有 ``embedding``。

用法
----
    python scripts/patch_gguf_pooling.py \
        --src "D:/models/Qwen3-Embedding-4B-Q5_K_M.gguf" \
        --dst "D:/models/local/qwen3-embed-4b-pool3.gguf" \
        --arch qwen3 --pooling last

    # 然后
    echo FROM D:/models/local/qwen3-embed-4b-pool3.gguf > Modelfile
    ollama create qwen3-embed-4b -f Modelfile

实现
----
GGUF 结构：magic/version/tensor_count/kv_count → KV 列表 → tensor info 列表 →
（按 alignment 对齐）→ tensor 数据区。tensor offset 是相对数据区起点的，
所以只要原样搬运头部、多写一条 KV、再重新对齐后整段复制数据区即可，
张量本体不重新编码、不重新量化。
"""

from __future__ import annotations

import argparse
import os
import struct

POOLING = {"none": 0, "mean": 1, "cls": 2, "last": 3, "rank": 4}
GGUF_TYPE_UINT32 = 4


def _skip_val(f, t: int) -> None:
    if t in (0, 1, 7):
        f.read(1)
    elif t in (2, 3):
        f.read(2)
    elif t in (4, 5, 6):
        f.read(4)
    elif t in (10, 11, 12):
        f.read(8)
    elif t == 8:
        n = struct.unpack("<Q", f.read(8))[0]
        f.read(n)
    elif t == 9:
        sub = struct.unpack("<I", f.read(4))[0]
        ln = struct.unpack("<Q", f.read(8))[0]
        for _ in range(ln):
            _skip_val(f, sub)
    else:
        raise ValueError(f"未知 GGUF 类型 {t}")


def main() -> int:
    ap = argparse.ArgumentParser(description="给 GGUF 补 pooling_type（embedding 能力标记）")
    ap.add_argument("--src", required=True, help="原 GGUF 路径（不会被修改）")
    ap.add_argument("--dst", required=True, help="新 GGUF 输出路径")
    ap.add_argument("--arch", required=True, help='模型架构，如 qwen3 / bert；KV 键为 "<arch>.pooling_type"')
    ap.add_argument("--pooling", default="last", choices=sorted(POOLING), help="池化方式，默认 last")
    args = ap.parse_args()

    if os.path.abspath(args.src) == os.path.abspath(args.dst):
        raise SystemExit("src 与 dst 不能是同一个文件（不覆盖原模型）")

    f = open(args.src, "rb")
    magic = f.read(4)
    version = struct.unpack("<I", f.read(4))[0]
    tensor_count = struct.unpack("<Q", f.read(8))[0]
    kv_count = struct.unpack("<Q", f.read(8))[0]
    if magic != b"GGUF" or version != 3:
        raise SystemExit(f"仅支持 GGUF v3，实际 magic={magic!r} version={version}")

    kv_spans: list[tuple[str, int, int]] = []
    align = 32
    for _ in range(kv_count):
        start = f.tell()
        n = struct.unpack("<Q", f.read(8))[0]
        key = f.read(n).decode("utf-8", "replace")
        t = struct.unpack("<I", f.read(4))[0]
        if key == "general.alignment" and t == GGUF_TYPE_UINT32:
            align = struct.unpack("<I", f.read(4))[0]
            kv_spans.append((key, start, f.tell()))
            continue
        _skip_val(f, t)
        kv_spans.append((key, start, f.tell()))

    pool_key = f"{args.arch}.pooling_type"
    if any(k == pool_key for k, _, _ in kv_spans):
        raise SystemExit(f"源文件里已有 {pool_key}，无需补丁")

    ti_start = f.tell()
    for _ in range(tensor_count):
        n = struct.unpack("<Q", f.read(8))[0]
        f.read(n)
        nd = struct.unpack("<I", f.read(4))[0]
        f.read(8 * nd + 4 + 8)
    ti_end = f.tell()

    data_start = (ti_end + align - 1) // align * align
    src_size = os.path.getsize(args.src)
    print(f"对齐 {align} / KV {kv_count} / 张量 {tensor_count} / 数据区 {(src_size - data_start) / 1e9:.2f} GB")

    out = open(args.dst, "wb")
    out.write(b"GGUF")
    out.write(struct.pack("<I", version))
    out.write(struct.pack("<Q", tensor_count))
    out.write(struct.pack("<Q", kv_count + 1))

    for _key, s, e in kv_spans:
        f.seek(s)
        out.write(f.read(e - s))

    raw_key = pool_key.encode("utf-8")
    out.write(struct.pack("<Q", len(raw_key)))
    out.write(raw_key)
    out.write(struct.pack("<I", GGUF_TYPE_UINT32))
    out.write(struct.pack("<I", POOLING[args.pooling]))

    f.seek(ti_start)
    out.write(f.read(ti_end - ti_start))

    # 头部变长 → 数据区整体后移；offset 相对数据区，不受影响
    pos = out.tell()
    new_start = (pos + align - 1) // align * align
    out.write(b"\x00" * (new_start - pos))

    f.seek(data_start)
    copied = 0
    while True:
        buf = f.read(8 * 1024 * 1024)
        if not buf:
            break
        out.write(buf)
        copied += len(buf)
    out.close()
    f.close()
    print(f"完成：{pool_key}={args.pooling}({POOLING[args.pooling]})，复制 {copied / 1e9:.2f} GB → {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
