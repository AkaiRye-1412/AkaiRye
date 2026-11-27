"""终端 PTY 字节流 WS（M5 / FR-TERM · 三期 · 04 文档第 4 章：/ws/terminal/{tab_id}）。

协议（JSON 帧，与 events WS 同源约定）：
- 客户端 → 服务端：
    {"type": "input",  "data": "<键盘输入>"}   键入（含 `/` 内置命令拦截）
    {"type": "resize", "cols": 80, "rows": 24}  窗口尺寸变化
    {"type": "ping"}                            心跳
- 服务端 → 客户端：
    {"type": "output", "data": "<PTY 原始输出文本>"}   shell 输出（含 ANSI 转义）
    {"type": "builtin", "data": "<内置命令回显>", "cwd": "..."}  内置命令结果（FR-TERM-02）
    {"type": "cwd",    "cwd": "..."}             当前工作目录（标签标题用）
    {"type": "pong"}
    {"type": "exit",   "code": 0}                PTY 进程退出

设计要点：
- PTY 进程随标签常驻：本 WS 断开不终止 PTY（FR-TERM-04），重连后 attach 回同一进程；
- 输出历史：进程常驻期间 PTY 输出缓冲由 ConPTY 维护，重连后前端先 REST 拿 cwd 再拉增量；
  （后端进程重启则输出丢失，符合 NFR-DATA-04「终端会话允许丢失，需提示」）
- 内置命令拦截（FR-TERM-02）：以 `/` 开头的整行在转发 shell 前先由 manager.resolve_builtin 处理。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db import database
from app.services import terminal as terminal_service

logger = logging.getLogger(__name__)

router = APIRouter()


async def _pump_pty_output(ws: WebSocket, pty, tab_id: str) -> None:
    """后台任务：从 PTY 输出队列取数据推送给 WS。

    PTY 读线程常驻（随进程），此处只做「队列 → WS」的转发，非阻塞轮询可被 cancel 干净打断。
    """
    while True:
        data = pty.read_available(timeout=0.05)
        if data is None:
            # PTY 已退出
            try:
                await ws.send_text(json.dumps({"type": "exit", "code": 0}, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                pass
            break
        if data:
            try:
                await ws.send_text(json.dumps({"type": "output", "data": data}, ensure_ascii=False))
            except Exception:  # noqa: BLE001 - WS 已断开
                break
        await asyncio.sleep(0.02)


@router.websocket("/ws/terminal/{tab_id}")
async def terminal_ws(ws: WebSocket, tab_id: str) -> None:
    await ws.accept()

    # 标签不存在则拒绝（前端应先用 REST 建标签）
    tab = terminal_service.manager.get_tab(tab_id)
    if tab is None:
        await ws.send_text(json.dumps({"type": "error", "data": "标签不存在，请刷新或重新创建"}, ensure_ascii=False))
        await ws.close(code=4404)
        return

    # 附着（必要时启动）PTY —— 进程随标签常驻，断开不终止
    try:
        pty, is_new = terminal_service.manager.attach(tab_id)
    except Exception as e:  # noqa: BLE001 - pywinpty 未安装/平台不支持
        await ws.send_text(
            json.dumps({"type": "error", "data": f"无法启动本机 shell：{e}"}, ensure_ascii=False)
        )
        await ws.close(code=1011)
        return

    # 首次附着回传 cwd（前端标签标题显示）
    await ws.send_text(json.dumps({"type": "cwd", "cwd": tab.cwd}, ensure_ascii=False))

    if not is_new:
        # 复用已有常驻 PTY（重连 / 页面刷新）：重放输出历史快照，还原 shell/TUI 已渲染界面。
        # 之前只补发空回车，对普通 shell 能唤醒提示符，但对 claude/vim 等全屏 TUI 无效（界面不会重绘），
        # 导致刷新后一片空白。现改为重放历史输出：前端 xterm 从空白按序执行历史里的全部 ANSI 序列
        # （含 TUI 的备用屏幕/清屏/光标定位），即可精确重建当前屏幕状态。
        # 用专用 replay 帧（而非 output），前端据此在重放期间屏蔽 onData 转发，
        # 避免 xterm 对历史里终端查询序列（DA1 \x1b[c / DSR \x1b[6n）的自动响应被写回 shell 造成污染。
        snapshot = pty.snapshot()
        if snapshot:
            await ws.send_text(json.dumps({"type": "replay", "data": snapshot}, ensure_ascii=False))

    reader_task = asyncio.create_task(_pump_pty_output(ws, pty, tab_id))

    # 内置命令处理需要 db 连接（/cd 持久化 cwd）
    conn = database.connect()

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")

            if mtype == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
                continue

            if mtype == "resize":
                # ConPTY 尺寸调整：pywinpty 支持 setwinsize；此处按需调用
                cols = int(msg.get("cols") or 0)
                rows = int(msg.get("rows") or 0)
                if cols > 0 and rows > 0:
                    logger.info("终端 resize（tab=%s）：cols=%d rows=%d", tab_id, cols, rows)
                    try:
                        pty._proc.setwinsize(rows, cols)  # noqa: SLF001 - pywinpty 私有句柄
                    except Exception:  # noqa: BLE001 - 尺寸调整失败不影响主流程
                        pass
                continue

            if mtype == "input":
                data = msg.get("data", "")
                # 内置命令拦截（FR-TERM-02）：整行以 / 开头且在输入末尾（回车前）
                if data.endswith("\r") or data.endswith("\n"):
                    line = data.rstrip("\r\n")
                    builtin = terminal_service.manager.resolve_builtin(conn, tab, line)
                    if builtin and builtin.get("handled"):
                        await ws.send_text(
                            json.dumps(
                                {"type": "builtin", "data": builtin["output"], "cwd": builtin.get("cwd", tab.cwd)},
                                ensure_ascii=False,
                            )
                        )
                        # 内置命令如需真正执行（如 /claude 启动 claude），把 exec 写回 shell
                        exec_cmd = builtin.get("exec")
                        if exec_cmd:
                            pty.write(exec_cmd)
                        continue
                pty.write(data)
                continue

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001 - 异常不终止常驻 PTY
        logger.warning("终端 WS 异常（tab=%s）：%s", tab_id, e)
    finally:
        conn.close()
        reader_task.cancel()
        # 关键：不 terminate PTY —— 进程随标签常驻（FR-TERM-04）
        logger.info("终端 WS 断开（tab=%s），PTY 保持运行", tab_id)
