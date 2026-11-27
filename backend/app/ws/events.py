"""events WS：状态推送 + 上下文联动（04 文档 4 章 topic 契约）。

契约要点（api-rules 第 4 节）：
- 包络：{"topic": ..., "type": "update|snapshot", "payload": {...}}；
- 客户端仅允许上报 context（FR-RAG-18 自动感知），业务动作一律走 REST；
- 推送不作唯一数据来源：前端重连后一律 REST 全量对账。
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# 04 文档 4 章 topic 枚举（唯一事实来源）
TOPIC_NOTE_CHANGED = "note.changed"        # vault 文件变更（watchdog 防抖后）
TOPIC_INDEX_UPDATED = "index.updated"      # 笔记索引结果/进度
TOPIC_INGEST_PROGRESS = "ingest.progress"  # 入库队列进度（500ms 节流）
TOPIC_MANUAL_UPDATED = "manual.updated"    # 手册状态跃迁（即时）
TOPIC_RAG_STREAM = "rag.stream"            # 回答 token 流（Q-14：POST 提问 + WS 流式）
TOPIC_CONFIG_UPDATED = "config.updated"    # 配置热生效广播
TOPIC_BACKUP_DONE = "backup.done"          # 备份完成
TOPIC_REPO_CHANGED = "repo.changed"        # 代码仓库变更（watchdog 防抖后自动刷新）
TOPIC_CONTEXT_REPORT = "context.report"    # 客户端 → 后端上下文上报（仅此 topic 允许客户端发送）

VALID_TOPICS = {
    TOPIC_NOTE_CHANGED, TOPIC_INDEX_UPDATED, TOPIC_INGEST_PROGRESS,
    TOPIC_MANUAL_UPDATED, TOPIC_RAG_STREAM, TOPIC_CONFIG_UPDATED,
    TOPIC_BACKUP_DONE, TOPIC_REPO_CHANGED, TOPIC_CONTEXT_REPORT,
}


@dataclass
class EventBroker:
    """进程内事件总线：服务层 publish → WS 连接广播（单进程单用户，无需外部队列）。

    除 WS 广播外支持进程内订阅（subscribe）：服务层模块间解耦联动
    （如配置热生效 → 重挂 vault watchdog），回调在 publish 调用线程同步执行。
    """

    connections: list[WebSocket] = field(default_factory=list)
    _loop: asyncio.AbstractEventLoop | None = None
    _subscribers: dict[str, list] = field(default_factory=dict)

    def subscribe(self, topic: str, fn) -> None:
        """注册进程内回调（payload 单参数）；与 WS 广播相互独立。"""
        self._subscribers.setdefault(topic, []).append(fn)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """由 FastAPI 生命周期绑定主事件循环，服务层（含线程）可安全 publish。"""
        self._loop = loop

    async def broadcast(self, topic: str, payload: dict[str, Any], type_: str = "update") -> None:
        assert topic in VALID_TOPICS, f"未注册 topic: {topic}"
        frame = json.dumps({"topic": topic, "type": type_, "payload": payload}, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in self.connections:
            try:
                await ws.send_text(frame)
            except Exception:  # noqa: BLE001 - 单连接失败不影响其他订阅者
                dead.append(ws)
        for ws in dead:
            self.remove(ws)

    def publish(self, topic: str, payload: dict[str, Any], type_: str = "update") -> None:
        """线程安全入口：服务层后台线程/任务用它投递事件。"""
        for fn in self._subscribers.get(topic, []):
            try:
                fn(payload)
            except Exception:  # noqa: BLE001 - 订阅者失败不影响广播
                logger.exception("订阅者处理 %s 失败", topic)
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(topic, payload, type_), self._loop)

    def add(self, ws: WebSocket) -> None:
        self.connections.append(ws)

    def remove(self, ws: WebSocket) -> None:
        if ws in self.connections:
            self.connections.remove(ws)


broker = EventBroker()

router = APIRouter()


@router.websocket("/ws/events")
async def events_ws(ws: WebSocket) -> None:
    """状态推送共用连接；心跳由前端驱动（api-rules 4.3）。"""
    await ws.accept()
    broker.add(ws)
    logger.info("events WS 连接建立（当前 %d 个）", len(broker.connections))
    try:
        while True:
            # 客户端仅允许 context.report；其余消息忽略（业务动作走 REST）
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("topic") == TOPIC_CONTEXT_REPORT:
                from app.services import rag_service  # 局部导入避免环

                rag_service.update_context(msg.get("payload") or {})
    except WebSocketDisconnect:
        pass
    finally:
        broker.remove(ws)
        logger.info("events WS 连接断开（剩余 %d 个）", len(broker.connections))
