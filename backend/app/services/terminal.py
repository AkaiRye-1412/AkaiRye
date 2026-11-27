"""终端会话管理（M5 / FR-TERM · 三期）：ConPTY 多标签、进程随标签常驻、恢复策略。

设计要点（02 文档 3.3(4) / FR-TERM-01~08）：
- 每个标签一个 ConPTY 进程，独立工作目录（FR-TERM-05）；
- PTY 进程随标签常驻：WS 断开 / 页面刷新不杀进程，重连后重新附着同一 PTY
  （FR-TERM-04「后台持久」，输出历史完整保留）；
- 标签元数据（tab_id / cwd / 标题 / 排序）持久化到 terminal_tab 表；
- 关闭运行中标签需二次确认（FR-TERM-07）：本层返回 alive 状态，由前端确认后调 terminate；
- 内置命令（FR-TERM-02）：输入以 `/` 开头走工作台内置命令（/help、/cd），其余交给 shell；
- 本机限制（FR-TERM-08）：能力由 main.py 仅监听 127.0.0.1 保证，此处不做额外网络判断。

PTY 封装：pywinpty（ConPTY）。非 Windows 平台无法使用 ConPTY，spawn 会抛异常，
由上层捕获并按「本机限制/未安装」处理（需求场景为 Windows 10+）。
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class TerminalTab:
    """一个终端标签（含会话运行时状态）。"""

    tab_id: str
    cwd: str
    title: str
    sort: int
    created_at: str
    # —— 运行时（不落库，进程内维护）——
    pty: "PtyHandle | None" = None

    @property
    def alive(self) -> bool:
        return self.pty is not None and self.pty.isalive()


class PtyHandle:
    """ConPTY 句柄封装：延迟导入 pywinpty，未安装时给出清晰错误。

    输出采用「持久读线程 + 队列」：读线程随 PTY 常驻（直到终止），
    与 WS 连接生命周期解耦——重连不会产生多个读线程，也不会因旧读线程阻塞 executor。
    """

    def __init__(self, cwd: str):
        from winpty import PtyProcess  # pywinpty（FR-TERM，pyproject pty 可选组）

        # ConPTY 用 cmd.exe 承载（Windows 默认 shell）
        # 注入环境变量，屏蔽 Claude Code 对 NODE_EXTRA_CA_CERTS 里老根证书的红字警告：
        # - CLAUDE_CODE_DISABLE_SELF_NAMED_CERT_DROP（v2.1.315~324 用）
        # - CLAUDE_CODE_DISABLE_SELF_NAMED_CERT_ORP（更新版本用）
        # 两者都设，覆盖各版本；只影响本终端 shell 会话，不影响系统/WorkBuddy 本身。
        env = dict(os.environ)
        env["CLAUDE_CODE_DISABLE_SELF_NAMED_CERT_DROP"] = "1"
        env["CLAUDE_CODE_DISABLE_SELF_NAMED_CERT_ORP"] = "1"
        self._proc = PtyProcess.spawn("cmd.exe", cwd=cwd, env=env)
        self.pid = self._proc.pid
        # 写缓冲：PtyProcess 非线程安全，写用锁串行化
        self._write_lock = threading.Lock()
        # 输出队列：读线程 → WS 消费（元素为 str，pywinpty 3.x read 已返回解码文本）
        self._out_queue: "queue.Queue[str | None]" = queue.Queue(maxsize=4096)
        self._reader = threading.Thread(target=self._read_loop, name=f"pty-reader-{self.pid}", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        """读线程：阻塞读 PTY，数据入队；EOF/异常时结束（并向队列发退出信号）。"""
        try:
            while True:
                data = self._proc.read(8192)
                if data:
                    try:
                        self._out_queue.put_nowait(data)
                    except queue.Full:
                        # 队列满：丢弃最旧块，保证新输出优先（本地回环，极端情况罕见）
                        try:
                            self._out_queue.get_nowait()
                            self._out_queue.put_nowait(data)
                        except queue.Empty:
                            pass
        except EOFError:
            pass  # 进程退出
        except Exception:  # noqa: BLE001 - 句柄失效等
            pass
        finally:
            # 结束信号：None 标记退出（WS 消费到 None 即向客户端发 exit 帧）
            try:
                self._out_queue.put_nowait(None)
            except queue.Full:
                pass

    def write(self, data: str) -> None:
        with self._write_lock:
            self._proc.write(data)

    def read_available(self, timeout: float = 0.05) -> str | None:
        """非阻塞取一块输出（str）；None 表示 PTY 已退出，b"" 已不存在——空串表示暂无数据。

        约定：返回 None = 退出；返回 "" = 暂无新数据；返回非空 str = 有新输出。
        """
        try:
            item = self._out_queue.get(timeout=timeout)
        except queue.Empty:
            return ""
        return item

    def isalive(self) -> bool:
        try:
            return self._proc.isalive()
        except Exception:  # noqa: BLE001 - 进程已回收等
            return False

    def terminate(self, force: bool = True) -> None:
        try:
            self._proc.terminate(force=force)
        except Exception as e:  # noqa: BLE001 - 关闭失败不阻断标签删除
            logger.warning("PTY 进程终止失败（pid=%s）：%s", self.pid, e)


class TerminalManager:
    """进程内终端会话注册表：tab_id → TerminalTab。

    单用户本机工具，直接内存字典 + SQLite 元数据即可，无需队列/锁池
    （会话 CRUD 与 PTY 读写由 FastAPI 单事件循环串行；PTY 底层自带线程缓冲）。
    """

    def __init__(self) -> None:
        self._tabs: dict[str, TerminalTab] = {}
        self._lock = threading.RLock()

    # ---------- 元数据落库 ----------

    def load_from_db(self, conn) -> None:
        """启动时把持久化标签读入内存（不启动 PTY，PTY 按需在 WS 附着时启动）。"""
        with self._lock:
            for row in conn.execute(
                "SELECT tab_id, cwd, title, sort, created_at FROM terminal_tab ORDER BY sort"
            ).fetchall():
                self._tabs[row["tab_id"]] = TerminalTab(
                    tab_id=row["tab_id"],
                    cwd=row["cwd"],
                    title=row["title"],
                    sort=row["sort"],
                    created_at=row["created_at"],
                    pty=None,
                )

    def _persist(self, conn, tab: TerminalTab) -> None:
        conn.execute(
            "INSERT INTO terminal_tab (tab_id, cwd, title, sort, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tab_id) DO UPDATE SET cwd=excluded.cwd, title=excluded.title, "
            "sort=excluded.sort",
            (tab.tab_id, tab.cwd, tab.title, tab.sort, tab.created_at),
        )
        conn.commit()

    # ---------- 会话 CRUD（供 REST 层调用） ----------

    def list_tabs(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "tab_id": t.tab_id,
                    "cwd": t.cwd,
                    "title": t.title,
                    "sort": t.sort,
                    "alive": t.alive,
                    "created_at": t.created_at,
                }
                for t in sorted(self._tabs.values(), key=lambda x: x.sort)
            ]

    def create_tab(self, conn, cwd: str | None = None, title: str | None = None) -> TerminalTab:
        """新建标签：cwd 默认取配置的 vault_root（或用户 HOME），标题取目录名。"""
        import uuid

        tab_id = uuid.uuid4().hex[:12]
        base = self._resolve_default_cwd(conn, cwd)
        tab = TerminalTab(
            tab_id=tab_id,
            cwd=base,
            title=title or self._default_title(base),
            sort=self._next_sort(),
            created_at=_utcnow(),
            pty=None,
        )
        with self._lock:
            self._tabs[tab_id] = tab
        self._persist(conn, tab)
        logger.info("终端标签已创建：%s（cwd=%s）", tab_id, base)
        return tab

    def get_tab(self, tab_id: str) -> TerminalTab | None:
        with self._lock:
            return self._tabs.get(tab_id)

    def rename_tab(self, conn, tab_id: str, title: str) -> TerminalTab | None:
        tab = self.get_tab(tab_id)
        if tab is None:
            return None
        tab.title = title
        self._persist(conn, tab)
        return tab

    def close_tab(self, conn, tab_id: str, force: bool = False) -> dict:
        """关闭标签：终止 PTY + 删除记录。force 由前端二次确认后传入。"""
        tab = self.get_tab(tab_id)
        if tab is None:
            return {"closed": False, "reason": "not_found"}
        if tab.alive and not force:
            return {"closed": False, "reason": "alive", "pid": tab.pty.pid if tab.pty else None}
        if tab.pty is not None:
            tab.pty.terminate(force=True)
        with self._lock:
            self._tabs.pop(tab_id, None)
        conn.execute("DELETE FROM terminal_tab WHERE tab_id = ?", (tab_id,))
        conn.commit()
        logger.info("终端标签已关闭：%s", tab_id)
        return {"closed": True}

    # ---------- PTY 附着（供 WS 层调用） ----------

    def attach(self, tab_id: str) -> tuple[PtyHandle, bool]:
        """取得（必要时启动）标签的 PTY：进程随标签常驻，断开不终止。

        返回 (pty, is_new)：is_new=True 表示本次新建了 PTY（首次附着），
        False 表示复用已有常驻 PTY（重连）。WS 层据此决定是否补发提示符唤醒。
        """
        tab = self.get_tab(tab_id)
        if tab is None:
            raise KeyError(tab_id)
        is_new = False
        if tab.pty is None or not tab.pty.isalive():
            tab.pty = PtyHandle(tab.cwd)
            is_new = True
            logger.info("终端标签 %s 已启动 PTY（pid=%s）", tab_id, tab.pty.pid)
        return tab.pty, is_new

    def alive(self, tab_id: str) -> bool:
        tab = self.get_tab(tab_id)
        return tab.alive if tab else False

    # ---------- 内置命令（FR-TERM-02） ----------

    def resolve_builtin(self, conn, tab: TerminalTab, line: str) -> dict | None:
        """以 `/` 开头按内置命令处理；返回结果 dict（含是否已处理 + 输出/新 cwd）。"""
        stripped = line.strip()
        if not stripped.startswith("/"):
            return None
        parts = stripped.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/help":
            return {
                "handled": True,
                "output": (
                    "AkaiRye 工作台内置命令：\r\n"
                    "  /help            显示本帮助\r\n"
                    "  /cd <目录>       切换当前标签工作目录\r\n"
                    "  /claude          启动 Claude Code\r\n"
                    "其余输入将交给本机 shell 执行。\r\n"
                ),
                "cwd": tab.cwd,
            }
        if cmd == "/claude":
            # 环境变量已在 shell 启动时注入（见 PtyHandle），此处直接启动 claude 即可
            return {
                "handled": True,
                "output": "启动 Claude Code…\r\n",
                "exec": "claude\r",
                "cwd": tab.cwd,
            }
        if cmd == "/cd":
            target = self._resolve_cd_target(tab.cwd, arg)
            if target is None or not Path(target).is_dir():
                return {"handled": True, "output": f"目录不存在或不可访问：{arg or '(空)'}\r\n", "cwd": tab.cwd}
            tab.cwd = target
            self._persist(conn, tab)
            # cwd 变更对已在运行的 shell 无效；提示用户（新标签生效）
            return {
                "handled": True,
                "output": f"已切换工作目录：{target}\r\n（对当前已启动的 shell 进程不生效，新建标签时生效）\r\n",
                "cwd": tab.cwd,
            }
        return {"handled": True, "output": f"未知内置命令：{cmd}（输入 /help 查看）\r\n", "cwd": tab.cwd}

    # ---------- 内部工具 ----------

    def _next_sort(self) -> int:
        with self._lock:
            if not self._tabs:
                return 0
            return max(t.sort for t in self._tabs.values()) + 1

    def _resolve_default_cwd(self, conn, cwd: str | None) -> str:
        if cwd and Path(cwd).is_dir():
            return cwd
        # 默认取用户主目录（HOME），对齐系统 cmd 默认行为；HOME 无效时回退 vault_root
        home = str(Path.home())
        if home and Path(home).is_dir():
            return home
        try:
            row = conn.execute(
                "SELECT value_json FROM config WHERE key='paths'"
            ).fetchone()
            if row:
                import json

                vault = json.loads(row["value_json"]).get("vault_root")
                if vault and Path(vault).is_dir():
                    return vault
        except Exception:  # noqa: BLE001 - 配置缺失回退 HOME
            pass
        return home

    def _default_title(self, cwd: str) -> str:
        return Path(cwd).name or cwd

    def _resolve_cd_target(self, current: str, arg: str) -> str | None:
        if not arg:
            return None
        p = Path(arg)
        if not p.is_absolute():
            p = Path(current) / arg
        return str(p.resolve())


# 进程级单例
manager = TerminalManager()
