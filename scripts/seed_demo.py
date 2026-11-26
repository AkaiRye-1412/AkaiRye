#!/usr/bin/env python
"""把仓库内的演示数据接入工作台（新环境 clone 后跑一次即可）。

作用：只修改 config 表的 `paths` 与 `index.whitelist`，把 vault_root 指向仓库内的
data/demo_vault（5 篇虚构的售后笔记），启动后笔记页/首页立刻有内容可看。

安全边界（重要）：
1. 绝不读取、绝不写入 llm / embedding 的 api_key —— 密钥始终只存在本机
   data/workbench.db，而该文件已被 .gitignore 排除，永远不进仓库；
2. 若检测到已经配置过别的 vault_root（比如你真实的 D:\\Notes），默认中止并退出码 2，
   避免误把工作配置指向演示目录；确认要覆盖时显式加 --yes；
3. 其余配置项（pdf_root、code_roots、dashboard、backup…）原样保留，不做改动。

用法：
    python scripts/seed_demo.py           # 首次接入
    python scripts/seed_demo.py --yes     # 已配置过其它 vault_root 时强制改到演示目录
    WORKBENCH_DATA_DIR=/tmp/x python scripts/seed_demo.py   # 写到临时库，便于测试
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DEMO_VAULT = ROOT / "data" / "demo_vault"
NOTES_FOLDER = "客户售后问题"

# 让脚本可以直接用后端的迁移与路径常量，而不需要先 pip install
sys.path.insert(0, str(BACKEND))

from app.core import paths as core_paths  # noqa: E402


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_config(conn: sqlite3.Connection) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in conn.execute("SELECT key, value_json FROM config"):
        try:
            value = json.loads(row["value_json"])
        except (json.JSONDecodeError, TypeError):
            value = {}
        out[row["key"]] = value if isinstance(value, dict) else {}
    return out


def _upsert(conn: sqlite3.Connection, key: str, value: dict, now: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value_json, updated_at) VALUES (?, ?, ?)",
        (key, json.dumps(value, ensure_ascii=False), now),
    )


def _secret_configured(conn: sqlite3.Connection, key: str) -> bool | None:
    """判断某个配置块是否已填 api_key —— 只在 SQL 侧比较是否为空。

    api_key 的明文值不会进入 Python 内存，更不会出现在输出或任何文件里。
    返回 None 表示当前 SQLite 不支持 json_extract，无法判断（不猜测）。
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM config WHERE key = ? "
            "AND IFNULL(json_extract(value_json, '$.api_key'), '') <> '' LIMIT 1",
            (key,),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return None


def _describe_secret(flag: bool | None, hint: str) -> str:
    if flag is None:
        return f"无法判断（当前 SQLite 不支持 JSON 函数），请到设置页确认；{hint}"
    return "已配置，保持原样" if flag else f"未配置（{hint}）"



def main() -> int:
    parser = argparse.ArgumentParser(description="把 data/demo_vault 接入为当前 vault")
    parser.add_argument("--yes", action="store_true", help="即使已配置其它 vault_root 也覆盖")
    args = parser.parse_args()

    if not DEMO_VAULT.is_dir():
        print(f"[x] 找不到演示数据目录：{DEMO_VAULT}")
        return 1

    core_paths.ensure_runtime_dirs()
    from app.db import migrations

    migrations.init_db()

    db_path = core_paths.DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        current = _read_config(conn)
        old_root = str((current.get("paths") or {}).get("vault_root") or "")
        if old_root and Path(old_root).resolve() != DEMO_VAULT.resolve() and not args.yes:
            print("[!] 当前 vault_root 已指向其它目录，未做任何修改：")
            print(f"    {old_root}")
            print("    若确认要改成演示目录，请加 --yes 重跑。")
            return 2

        now = _utcnow()

        # 只动 vault_root / notes_folder，其余字段原样保留
        paths_val = dict(current.get("paths") or {})
        paths_val.update({"vault_root": str(DEMO_VAULT), "notes_folder": NOTES_FOLDER})
        paths_val.setdefault("pdf_root", "")
        paths_val.setdefault("code_roots", [])
        _upsert(conn, "paths", paths_val, now)

        # 白名单里补上演示目录，避免被当成外部库排除
        wl = dict(current.get("index.whitelist") or {})
        folders = [str(f) for f in (wl.get("folders") or [])]
        if NOTES_FOLDER not in folders:
            folders.insert(0, NOTES_FOLDER)
        wl["folders"] = folders
        _upsert(conn, "index.whitelist", wl, now)

        conn.commit()

        # 主动扫一遍 demo vault，写入 note_file，这样首页看板立刻有数据
        # （否则要等前端第一次打开笔记页触发 rescan）
        from app.services import vault as vault_service

        try:
            vault_service.rescan_all(conn)
            conn.commit()
            scanned = conn.execute("SELECT COUNT(*) AS c FROM note_file").fetchone()["c"]
        except Exception as exc:  # noqa: BLE001 - 扫描失败不应让整个接入流程失败
            scanned = None
            print(f"[!] 初次扫描失败（不影响配置写入）：{exc}")

        demo_notes = sorted((DEMO_VAULT / NOTES_FOLDER).glob("*.md"))
        llm_state = _secret_configured(conn, "llm")
        emb_state = _secret_configured(conn, "embedding")

        print("演示数据已接入")
        print(f"  数据库        : {db_path}")
        print(f"  vault_root    : {DEMO_VAULT}")
        print(f"  notes_folder  : {NOTES_FOLDER}")
        print(f"  演示笔记      : {len(demo_notes)} 篇" + (f"（已入库 {scanned} 条）" if scanned is not None else ""))
        for p in demo_notes:
            print(f"    - {p.name}")
        print()
        print("下一步：")
        print("  cd backend && python run.py          # 后端 127.0.0.1:8686")
        print("  cd frontend && npm run dev           # 前端 5173（或用 scripts/dev.bat）")
        print("  → 打开「售后笔记」应能看到上面几篇")
        print()
        print("未改动（安全边界）：")
        print(f"  llm 配置      : {_describe_secret(llm_state, '需要 AI 问答时到设置页填写')}")
        print(f"  embedding 配置: {_describe_secret(emb_state, '语义检索需要它，填好后点「重建索引」')}")
        print("  api_key 明文全程未进入本脚本内存，也未写入任何文件。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
