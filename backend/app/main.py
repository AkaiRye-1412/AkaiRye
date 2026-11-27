"""应用入口：路由注册、events WS、静态托管、生命周期（02 文档 5 章）。

生命周期顺序：运行目录 → 数据库迁移 → 启动备份补跑（>24h）→ 绑定事件循环。
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from app.api import (
    backups,
    cleanup,
    config,
    dashboard,
    kb,
    manuals,
    notes,
    pdf,
    projects,
    rag,
    repos,
    system,
    terminal,
    translate,
)
from app.core import paths
from app.core.config import runtime
from app.core.logging import setup_logging
from app.db import database, migrations
from app.services import backup as backup_service
from app.services import git_service
from app.services import manual_index
from app.services import rag_service
from app.services import terminal as terminal_service
from app.services import vault as vault_service_module
from app.ws import events, terminal as terminal_ws

logger = logging.getLogger(__name__)


def config_ready_for_index() -> bool:
    """vault 路径已配置且目录存在才补齐索引（否则白跑一遍扫描）。"""
    try:
        with database.get_conn() as conn:
            return vault_service_module.notes_dir(conn).is_dir()
    except Exception:  # noqa: BLE001 —— 配置缺失/路径非法都按未就绪处理
        logger.warning("索引自检失败：vault 路径未配置或不可读")
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(getattr(logging, runtime.log_level.upper(), logging.INFO))
    paths.ensure_runtime_dirs()
    migrations.init_db()
    events.broker.bind_loop(asyncio.get_running_loop())
    with database.get_conn() as tconn:
        terminal_service.manager.load_from_db(tconn)

    # 启动备份补跑（OQ-10 / NFR-DATA-04）：>24h 补一次每日备份
    with database.get_conn() as conn:
        if backup_service.needs_daily_backup(conn):
            logger.info("距上次备份超过 24h，启动时补跑每日备份")
            backup_service.run_backup(conn, kind="daily")
            backup_service.rotate(conn, backup_service.keep_count(conn))

        # 旧会话标题补齐：仍是「新会话」但有历史的，用第一条提问命名（幂等）
        renamed = rag_service.backfill_default_titles(conn)
        if renamed:
            logger.info("会话标题补齐：%d 个默认标题会话已按首条提问命名", renamed)

        # PDF 入库队列恢复（NFR-DATA-04）：上次进程退出时未跑完的任务退回排队
        recovered = manual_index.recover_on_startup(conn)
        if recovered:
            logger.info("PDF 入库队列恢复：%d 个中断任务已重新排队", recovered)
        queued_ingest = conn.execute(
            "SELECT COUNT(*) c FROM ingest_task WHERE status='queued'"
        ).fetchone()["c"]

    # vault 文件监听（FR-NOTE-06）：目录未配置/不存在时跳过（设置页会校验提示）
    if vault_service_module.vault_service.start_watch(database.connect):
        logger.info("vault watchdog 已启动")
    else:
        logger.warning("vault watchdog 未启动：vault_root/notes_folder 尚未配置或目录不存在")

    # 自动索引补齐（FR-RAG-01「无需手动触发」）：历史笔记此前只在文件被保存时才索引，
    # 已存在的笔记会永远停在未索引。延迟 5s 让服务先对外可用，再在后台线程补齐。
    from app.services import indexer as indexer_service

    if config_ready_for_index():
        started = indexer_service.start_task(
            database.connect, mode="autofill", paths=None, force=False, delay=5.0
        )
        if started.get("started"):
            logger.info("已排入自动索引补齐任务（后台执行）")

    # 上次未完成的 PDF 入库任务：等索引补齐跑起来后再接上，避免两个 embedding 任务抢限频
    if queued_ingest:
        manual_index.start(database.connect)
        logger.info("已恢复 PDF 入库队列（%d 个任务）", queued_ingest)

    # 配置热生效联动：路径配置变更后重挂 watchdog（无需重启服务）
    def _on_config_updated(payload: dict) -> None:
        if "paths" not in payload.get("keys", []):
            return
        vs = vault_service_module.vault_service
        vs.stop_watch()
        if vs.start_watch(database.connect):
            logger.info("路径配置变更：vault watchdog 已重新挂载")
        else:
            logger.warning("路径配置变更：vault watchdog 未启动（目录无效）")
        # 注意：这里不自动排索引任务。路径变更后由前端在设置页保存成功后显式调
        # POST /api/kb/autofill —— 隐式后台任务会与用户随后的操作（单篇重建/白名单调整）
        # 并发写同一个库，SQLite 锁冲突会让同步索引莫名的少索引几篇。

    # 代码仓库定期刷新（FR-GIT-03）：启动即刷一次，此后每 30 分钟自动刷新
    git_service.start_periodic_refresh(database.connect)

    events.broker.subscribe("config.updated", _on_config_updated)
    yield
    vault_service_module.vault_service.stop_watch()
    git_service.stop_periodic_refresh()


def create_app() -> FastAPI:
    app = FastAPI(title="AkaiRye 售后工作台", version="0.1.0", lifespan=lifespan)

    # 错误包络对齐 api-rules：{error: {code, message}}（HTTPException detail 透传）
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(_request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code, msg = detail.get("code", "error"), detail.get("message", "")
        else:
            code, msg = "error", str(detail)
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": msg}})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "bad_request", "message": f"参数校验失败：{exc.errors()[0].get('msg', '')}"}},
        )

    # 同源部署（生产由 FastAPI 托管前端构建产物）；开发模式允许 Vite 端口跨域
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system.router)
    app.include_router(config.router)
    app.include_router(dashboard.router)
    app.include_router(projects.router)
    app.include_router(notes.router)
    app.include_router(kb.router)
    app.include_router(manuals.router)
    app.include_router(pdf.router)
    app.include_router(rag.router)
    app.include_router(translate.router)
    app.include_router(cleanup.router)
    app.include_router(backups.router)
    app.include_router(terminal.router)
    app.include_router(repos.router)
    app.include_router(events.router)   # /ws/events
    app.include_router(terminal_ws.router)  # /ws/terminal/{tab_id}

    if paths.FRONTEND_DIST.is_dir():
        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            """SPA fallback：存在文件则返回文件，否则回 index.html（vue-router history 模式）。"""
            file = paths.FRONTEND_DIST / full_path
            if full_path and ".." not in full_path and file.is_file():
                return FileResponse(file)
            return FileResponse(paths.FRONTEND_DIST / "index.html")

        logger.info("已托管前端构建产物：%s", paths.FRONTEND_DIST)

    return app


app = create_app()
