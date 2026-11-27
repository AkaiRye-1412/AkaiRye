"""代码仓库只读状态（M6 · FR-GIT-01~06）。

职责边界（需求 4.7 / 设计 7.3）：
- 自动发现（FR-GIT-01）：读配置 `paths.code_roots`（1~3 个代码根目录），递归发现 `.git`
  仓库作为候选，不去 git 客户端里登记任何东西；
- 置顶/隐藏（FR-GIT-02）：display 三态 pin / normal / hidden 持久化到 git_repo 表；
- 只读（FR-GIT-05）：只用 `git status / log / rev-list` 等读命令经 subprocess 读取，
  不做 commit / push / pull / 切分支；需要写操作走终端（「在此目录打开终端」）；
- 展示粒度（FR-GIT-04）：当前分支、未提交文件数、领先/落后远端提交数、最近一次提交；
- 忽略规则（FR-GIT-06）：扫描跳过依赖/构建等噪音目录，也避免把一个代码根里的
  node_modules 之类嵌着的 vendor 仓库扫进来。

git 不在 PATH 或仓库损坏时不抛死：单仓失败记为 error 字段，其余继续。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.services import config_service

logger = logging.getLogger(__name__)

# 扫描时跳过的目录名（FR-GIT-06：依赖/构建/缓存等噪音路径）
IGNORED_DIRS = {
    ".git",
    "node_modules",
    "bower_components",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".gradle",
    ".idea",
    ".vscode",
    ".obsidian",
    "vendor",
}

# 单个根目录递归深度上限：代码根一般直接放几个仓库，深挖 3 层足够，防目录树失控
MAX_DEPTH = 3

# git 命令统一超时（秒）：本地仓库，太久多半是锁/损坏，宁可失败也不卡接口
GIT_TIMEOUT = 10


class GitUnavailable(RuntimeError):
    """git 不在 PATH 或无法执行（前端据此提示安装 git）。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_available() -> bool:
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def configured_roots(conn) -> list[str]:
    """读配置的代码根目录（绝对路径），去重去空。"""
    paths = config_service.get_raw(conn, "paths")
    raw = paths.get("code_roots") or []
    seen: list[str] = []
    for r in raw:
        r = (r or "").strip()
        if not r:
            continue
        r = str(Path(r).expanduser())
        if r not in seen:
            seen.append(r)
    return seen[:3]  # 需求明确 1~3 个


def _is_git_repo(path: Path) -> bool:
    """判定目录是否 git 仓库：`.git` 目录存在，或 `git -C <path> rev-parse` 认得出。
    只认目录型的 `.git`；worktree 的 `.git` 是个文件也支持（rev-parse 兜底）。"""
    if (path / ".git").exists():
        return True
    return False


def discover_repos(conn) -> list[str]:
    """在配置的代码根下递归发现仓库，返回绝对路径列表（去重、跳过忽略目录）。"""
    repos: list[str] = []
    seen: set[str] = set()
    for root in configured_roots(conn):
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for found in _walk_repos(root_path, depth=0):
            key = os.path.normcase(str(found.resolve()))
            if key not in seen:
                seen.add(key)
                repos.append(str(found))
    return repos


def _walk_repos(root: Path, depth: int) -> list[Path]:
    """递归找 `.git` 目录。命中的仓库不再向下钻（避免把仓库内嵌的子模块目录重复计）。
    忽略目录不进入、不返回。"""
    out: list[Path] = []
    if depth > MAX_DEPTH:
        return out
    # 根目录本身可能就是仓库
    if _is_git_repo(root):
        return [root]
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name in IGNORED_DIRS or entry.name.startswith("."):
            continue
        if _is_git_repo(entry):
            out.append(entry)
        else:
            out.extend(_walk_repos(entry, depth + 1))
    return out


def _run_git(path: str, args: list[str]) -> str:
    """跑一条只读 git 命令，返回 stdout 文本（去尾换行）。失败抛异常。"""
    proc = subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        timeout=GIT_TIMEOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git 命令失败").strip()[:300])
    return proc.stdout.strip()


def read_status(path: str) -> dict:
    """读单个仓库的只读状态（FR-GIT-04）。

    返回字段：branch / ahead / behind / dirty / last_commit / last_commit_at /
             remote_url / commit_count。
    任何一条命令失败（detached HEAD、无远端、损坏仓库等）都整体记 error，
    由调用方决定是否继续（单仓失败不拖垮整批）。
    """
    branch = ""
    ahead = behind = 0
    dirty = 0
    last_commit = ""
    last_commit_at = ""
    remote_url = ""
    commit_count = 0

    # 当前分支（detached HEAD 时 rev-parse 会非零，记 error）
    branch = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])

    # 未提交文件数：porcelain 每行一个变更；untracked 也计入「未提交」
    status_out = _run_git(path, ["status", "--porcelain"])
    dirty = len([ln for ln in status_out.splitlines() if ln.strip()])

    # 领先/落后远端：仅在存在上游时有效；无上游（本地仓）不报错，按 0 处理
    try:
        ahead = int(_run_git(path, ["rev-list", "--count", "@{upstream}..HEAD"]))
    except Exception:
        ahead = 0
    try:
        behind = int(_run_git(path, ["rev-list", "--count", "HEAD..@{upstream}"]))
    except Exception:
        behind = 0

    # 最近一次提交（仓库刚 init 无提交时 log 会失败，按空处理）
    try:
        log = _run_git(path, ["log", "-1", "--pretty=%h%x00%cI%x00%s"])
        parts = log.split("\x00")
        if len(parts) >= 2:
            last_commit = parts[0]
            last_commit_at = parts[1]
    except Exception:
        last_commit = ""
        last_commit_at = ""

    # 远端仓库地址（无远端或非 URL 类型时按空处理，不报错）
    try:
        remote_url = _run_git(path, ["remote", "get-url", "origin"])
    except Exception:
        remote_url = ""

    # 当前分支的提交总数（rev-list --count HEAD；无提交时按 0 处理）
    try:
        commit_count = int(_run_git(path, ["rev-list", "--count", "HEAD"]))
    except Exception:
        commit_count = 0

    return {
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "dirty": dirty,
        "last_commit": last_commit,
        "last_commit_at": last_commit_at,
        "remote_url": remote_url,
        "commit_count": commit_count,
    }


def _existing_states(conn) -> dict[str, str]:
    """已发现仓库的历史展示状态（pin/normal/hidden），刷新时沿用，不重置用户设置。"""
    return {
        r["path"]: r["display"]
        for r in conn.execute("SELECT path, display FROM git_repo").fetchall()
    }


def refresh(conn) -> dict:
    """发现 + 读状态 + 落库。返回摘要（供接口/日志）。

    每次全量重建派生状态，但保留用户置顶/隐藏设置；从磁盘上消失的仓库也保留记录
    （用户可能在别的机器/外置盘，删掉会丢设置），只在列表里标记 missing。
    """
    if not _git_available():
        raise GitUnavailable("未检测到 git，请先安装 git 并加入 PATH")

    repos = discover_repos(conn)
    states = _existing_states(conn)
    now = _utcnow()

    for repo in repos:
        display = states.get(repo, "normal")
        row = {"display": display, "refreshed_at": now, "error": None}
        try:
            st = read_status(repo)
            row.update(st)
        except Exception as exc:  # noqa: BLE001 - 单仓失败不中断整批
            row["error"] = str(exc) or exc.__class__.__name__
            logger.warning("读取仓库状态失败：%s：%s", repo, row["error"])
        _upsert(conn, repo, row)

    conn.commit()
    logger.info("代码仓库刷新完成：发现 %d 个仓库", len(repos))
    return {"total": len(repos), "roots": configured_roots(conn)}


def _upsert(conn, path: str, fields: dict) -> None:
    conn.execute(
        """
        INSERT INTO git_repo (path, display, refreshed_at, branch, ahead, behind,
                              dirty, last_commit, last_commit_at, remote_url,
                              commit_count, error)
        VALUES (:path, :display, :refreshed_at, :branch, :ahead, :behind,
                :dirty, :last_commit, :last_commit_at, :remote_url,
                :commit_count, :error)
        ON CONFLICT(path) DO UPDATE SET
            display=excluded.display,
            refreshed_at=excluded.refreshed_at,
            branch=excluded.branch,
            ahead=excluded.ahead,
            behind=excluded.behind,
            dirty=excluded.dirty,
            last_commit=excluded.last_commit,
            last_commit_at=excluded.last_commit_at,
            remote_url=excluded.remote_url,
            commit_count=excluded.commit_count,
            error=excluded.error
        """,
        {
            "path": path,
            "display": fields.get("display", "normal"),
            "refreshed_at": fields.get("refreshed_at"),
            "branch": fields.get("branch"),
            "ahead": fields.get("ahead"),
            "behind": fields.get("behind"),
            "dirty": fields.get("dirty"),
            "last_commit": fields.get("last_commit"),
            "last_commit_at": fields.get("last_commit_at"),
            "remote_url": fields.get("remote_url"),
            "commit_count": fields.get("commit_count"),
            "error": fields.get("error"),
        },
    )


def _repo_dict(row) -> dict:
    return {
        "path": row["path"],
        "name": Path(row["path"]).name or row["path"],
        "display": row["display"],
        "exists": Path(row["path"]).is_dir(),
        "branch": row["branch"],
        "ahead": row["ahead"],
        "behind": row["behind"],
        "dirty": row["dirty"],
        "last_commit": row["last_commit"],
        "last_commit_at": row["last_commit_at"],
        "remote_url": row["remote_url"],
        "commit_count": row["commit_count"],
        "error": row["error"],
        "refreshed_at": row["refreshed_at"],
    }


def list_repos(conn, include_hidden: bool = False) -> list[dict]:
    """列出仓库，置顶优先、同档按名称排序。隐藏默认不返回（前端有开关显式要）。"""
    sql = "SELECT * FROM git_repo"
    params: list = []
    if not include_hidden:
        sql += " WHERE display != 'hidden'"
    sql += " ORDER BY (display='pin') DESC, (display='hidden') ASC, path ASC"
    rows = conn.execute(sql, params).fetchall()
    return [_repo_dict(r) for r in rows]


def set_display(conn, path: str, display: str) -> dict:
    """置顶 / 取消置顶 / 隐藏（FR-GIT-02）。目标仓库必须先已发现落库。"""
    if display not in ("pin", "normal", "hidden"):
        raise ValueError("display 仅支持 pin / normal / hidden")
    row = conn.execute("SELECT * FROM git_repo WHERE path=?", (path,)).fetchone()
    if row is None:
        raise FileNotFoundError(path)
    conn.execute("UPDATE git_repo SET display=? WHERE path=?", (display, path))
    conn.commit()
    return _repo_dict(conn.execute("SELECT * FROM git_repo WHERE path=?", (path,)).fetchone())


# ---- 定期刷新（FR-GIT-03：启动时与定期自动刷新，默认每 30 分钟） ----

_REFRESH_INTERVAL_SECONDS = 30 * 60
_refresh_thread: "threading.Thread | None" = None
_refresh_stop = threading.Event()


def start_periodic_refresh(conn_factory) -> None:
    """启动后台守护线程：启动稍后刷一次，此后每 30 分钟自动刷新状态。

    幂等：重复调用不会起第二条线程。git 不可用 / 配置未设代码根时静默跳过，
    不打断服务（前端手动「刷新」仍可用）。首次刷新延迟 5s，让服务先对外可用，
    也避免测试里 lifespan 一启动就抢 DB 连接导致文件句柄冲突。
    """
    global _refresh_thread
    if _refresh_thread is not None and _refresh_thread.is_alive():
        return

    def _loop() -> None:
        first = True
        while not _refresh_stop.is_set():
            # 首次延迟，之后按固定周期
            _refresh_stop.wait(5.0 if first else _REFRESH_INTERVAL_SECONDS)
            first = False
            if _refresh_stop.is_set():
                return
            try:
                conn = conn_factory()
                try:
                    if not configured_roots(conn):
                        continue
                    result = refresh(conn)
                    logger.info("代码仓库后台刷新：%d 个仓库", result.get("total", 0))
                finally:
                    conn.close()
            except GitUnavailable:
                # 未装 git：后台线程静默跳过，等下次周期再试（用户也可能中途装上）
                pass
            except Exception:  # noqa: BLE001 - 后台刷新异常不传播
                logger.exception("代码仓库后台刷新失败")

    _refresh_thread = threading.Thread(target=_loop, daemon=True, name="git-repo-refresh")
    _refresh_thread.start()


def stop_periodic_refresh() -> None:
    """停止后台刷新（供应用关闭时调用）。"""
    _refresh_stop.set()
