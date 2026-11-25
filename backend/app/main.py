"""应用入口：路由注册、events WS、静态托管、生命周期（02 文档 5 章）。

生命周期顺序：运行目录 → 数据库迁移 → 启动备份补跑（>24h）→ 绑定事件循环。
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from app.api import backups, config, dashboard, kb, manuals, notes, pdf, projects, rag, system
from app.core import paths
from app.core.config import runtime
from app.core.logging import setup_logging
from app.db import database, migrations
from app.services import backup as backup_service
from app.services import vault as vault_service_module
from app.ws import events

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(getattr(logging, runtime.log_level.upper(), logging.INFO))
    paths.ensure_runtime_dirs()
    migrations.init_db()
    events.broker.bind_loop(asyncio.get_running_loop())

    # 启动备份补跑（OQ-10 / NFR-DATA-04）：>24h 补一次每日备份
    with database.get_conn() as conn:
        if backup_service.needs_daily_backup(conn):
            logger.info("距上次备份超过 24h，启动时补跑每日备份")
            backup_service.run_backup(conn, kind="daily")

    # vault 文件监听（FR-NOTE-06）：目录未配置/不存在时跳过（设置页会校验提示）
    if vault_service_module.vault_service.start_watch(database.connect):
        logger.info("vault watchdog 已启动")
    else:
        logger.warning("vault watchdog 未启动：vault_root/notes_folder 尚未配置或目录不存在")

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

    events.broker.subscribe("config.updated", _on_config_updated)
    yield
    vault_service_module.vault_service.stop_watch()


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
    app.include_router(backups.router)
    app.include_router(events.router)  # /ws/events

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
