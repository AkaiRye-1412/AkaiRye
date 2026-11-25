"""配置管理服务（M8，FR-CFG）：config 表读写、脱敏、热生效事件。

依据：04 文档 3.1（GET/PUT /api/config、test-llm、test-embedding）、api-rules 第 8 节。
"""

import json
import logging
from typing import Any

from app.ws.events import broker

logger = logging.getLogger(__name__)

API_KEY_MASK = "******"

# 预置键及默认值（03 文档 3.1；value_json 为 JSON 对象）
DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "llm": {"provider": "", "base_url": "", "api_key": "", "model": "", "temperature": 0.7},
    "embedding": {"mode": "cloud", "provider": "", "api_key": "", "model": "", "dim": 1024},
    "paths": {"vault_root": "", "notes_folder": "客户售后问题", "pdf_root": "", "code_roots": []},
    "index.whitelist": {"folders": ["客户售后问题"]},
    "dashboard": {"follow_up_days": 7, "default_range": "month"},
    "backup": {"dir": "", "keep": 7},
    "shortcuts": {"global_ask": "Ctrl+K", "new_note": "Ctrl+N", "terminal": "Ctrl+T", "send": "Enter"},
}


def load_all(conn) -> dict[str, Any]:
    """读取全部配置；api_key 脱敏（NFR-SEC-03：密钥永不出后端）。"""
    rows = {r["key"]: json.loads(r["value_json"]) for r in conn.execute("SELECT key, value_json FROM config")}
    merged = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    for k, v in rows.items():
        merged.setdefault(k, {})
        if isinstance(v, dict) and isinstance(merged[k], dict):
            merged[k].update(v)
        else:
            merged[k] = v
    _mask_secrets(merged)
    return merged


def get_raw(conn, key: str) -> dict[str, Any]:
    """内部读取单个配置的原始值（含 api_key 明文）——仅限后端内部使用（如测试连接）。"""
    row = conn.execute("SELECT value_json FROM config WHERE key = ?", (key,)).fetchone()
    merged = dict(DEFAULT_CONFIG.get(key, {}))
    if row is not None:
        merged.update(json.loads(row["value_json"]))
    return merged


def save_many(conn, patch: dict[str, Any]) -> list[str]:
    """局部更新配置并广播热生效事件（04 文档：api_key 传空/掩码 = 不修改）。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved: list[str] = []
    current = {r["key"]: json.loads(r["value_json"]) for r in conn.execute("SELECT key, value_json FROM config")}

    for key, value in patch.items():
        if key in ("llm", "embedding") and isinstance(value, dict):
            old = current.get(key, {})
            if not value.get("api_key") or value.get("api_key") == API_KEY_MASK:
                # 掩码/空值 = 保留原密钥，密钥永不被前端写回
                value["api_key"] = old.get("api_key", "")
        merged = {**current.get(key, {}), **value} if isinstance(value, dict) else value
        conn.execute(
            "INSERT INTO config (key, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, json.dumps(merged, ensure_ascii=False), now),
        )
        saved.append(key)

    conn.commit()
    broker.publish("config.updated", {"keys": saved})
    return saved


def _mask_secrets(cfg: dict[str, Any]) -> None:
    for key in ("llm", "embedding"):
        section = cfg.get(key, {})
        api_key = section.get("api_key") or ""
        section["api_key_masked"] = API_KEY_MASK if api_key else ""
        section["api_key_set"] = bool(api_key)
        section.pop("api_key", None)  # 明文密钥不返回前端
