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
    # embedding 顶层字段是「当前生效」的一套（mode 决定 provider/model/dim/api_key 的归属），
    # cloud / ollama 两个子对象各自缓存另一套的 provider/model/dim/api_key，
    # 切换运行方式时前端据此自动恢复，互不覆盖（见 _split_embedding / _merge_embedding）。
    "embedding": {
        "mode": "cloud",
        "provider": "",
        "api_key": "",
        "model": "",
        "dim": 1024,
        "cloud": {"provider": "", "api_key": "", "model": "", "dim": 1024},
        "ollama": {"provider": "", "api_key": "", "model": "", "dim": 0},
    },
    # 索引位置（售后/个人/PDF/数据库/项目开发各最多 3 个，第 1 个为主位置；留空则回落旧单值字段）
    # after_sales_roots 售后笔记 / memo_roots 个人笔记
    # pdf_roots PDF 手册（仅手册界面浏览/预览，不作为知识库索引来源）
    # db_roots 知识库资料目录（其中的 Markdown 会被解析切块进索引）/ code_roots 代码仓库
    # draft_roots 草稿箱（右侧 dock 速记，单目录生效；留空 = 个人笔记第 1 个位置下的 草稿箱/）
    # 注意：程序自身的 SQLite 库文件位置不在这里，见 data/instance.json 的 db_file_dir
    "paths": {
        "vault_root": "",
        "notes_folder": "客户售后问题",
        "pdf_root": "",
        "code_roots": [],
        "after_sales_roots": [],
        "memo_roots": [],
        "pdf_roots": [],
        "db_roots": [],
        "draft_roots": [],
    },
    "index.whitelist": {"folders": ["客户售后问题"], "excluded": []},
    # 检索与切分参数（设置页可调；改切分参数只对之后的索引/重建生效）
    "retrieval": {
        "top_k": 8,            # 进 prompt 的片段数
        "min_similarity": 0.0,  # 余弦相似度下限，0 = 不设阈值
        "chunk_size": 1200,     # 笔记切块上限（字符）
        "pdf_chunk_size": 900,  # 手册 PDF 切块上限（字符）
        "chunk_overlap": 0,     # 相邻块重叠字符数
        "history_turns": 10,    # 追问携带的历史消息条数（0 = 不带）
        "embed_max_chars": 700,  # 送进嵌入模型的正文截断长度
        # 本地 reranker（BGE cross-encoder，FlagEmbedding 推理）：先召回一批，再精排取前 top_k
        "rerank_enabled": False,     # 是否启用精排
        "rerank_model_path": "",     # 权重目录（如 D:/models/bge-reranker-v2-m3）
        "rerank_top_n": 30,          # 召回给 reranker 的候选条数（越大越慢，个人规模 30 够用）
        "rerank_keep": 8,            # 精排后保留进 prompt 的条数（通常 = top_k）
    },
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
    _migrate_embedding(merged.get("embedding") or {})
    _mask_secrets(merged)
    return merged


def get_raw(conn, key: str) -> dict[str, Any]:
    """内部读取单个配置的原始值（含 api_key 明文）——仅限后端内部使用（如测试连接）。"""
    row = conn.execute("SELECT value_json FROM config WHERE key = ?", (key,)).fetchone()
    merged = dict(DEFAULT_CONFIG.get(key, {}))
    if row is not None:
        merged.update(json.loads(row["value_json"]))
    if key == "embedding":
        _migrate_embedding(merged)
    return merged


def _migrate_embedding(emb: dict[str, Any]) -> None:
    """把旧的单套 embedding 配置迁移成 cloud/ollama 双套（幂等）。

    旧数据只有顶层 provider/model/api_key/dim，没有 cloud/ollama 子对象；
    此时把顶层这套复制到两边的子对象里作为初始值，保证切换时两边都有记忆。
    """
    for mode in ("cloud", "ollama"):
        sub = emb.get(mode)
        if not isinstance(sub, dict):
            sub = {}
            emb[mode] = sub
        for key in ("provider", "api_key", "model", "dim"):
            # 子对象缺的字段用顶层当前值补齐；顶层缺则用 DEFAULT 兜底
            if key not in sub:
                default = DEFAULT_CONFIG["embedding"][mode][key]
                sub[key] = emb.get(key, default)
        # 本地 Ollama 免 Key：迁移时别把云端密钥带进 ollama 子对象
        if mode == "ollama":
            sub["api_key"] = ""


def _split_embedding(patch_emb: dict[str, Any]) -> dict[str, Any]:
    """保存 embedding 时：把顶层当前值同步进 mode 对应的子对象。

    前端提交的 payload 里 mode 即当前选中的运行方式，顶层 provider/model/dim/api_key
    是这套的值；把它们写回 emb[mode]，另一套的缓存保持不变。
    """
    out = dict(patch_emb)
    mode = out.get("mode") or "cloud"
    sub = dict(out.get(mode) or {})
    for key in ("provider", "model", "dim"):
        if key in out:
            sub[key] = out[key]
    # api_key：空/掩码 = 保留子对象里已存的密钥（与顶层 api_key 保留逻辑一致）
    ak = out.get("api_key")
    if ak and ak != API_KEY_MASK:
        sub["api_key"] = ak
    out[mode] = sub
    return out


def save_many(conn, patch: dict[str, Any]) -> list[str]:
    """局部更新配置并广播热生效事件（04 文档：api_key 传空/掩码 = 不修改）。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved: list[str] = []
    current = {r["key"]: json.loads(r["value_json"]) for r in conn.execute("SELECT key, value_json FROM config")}
    emb_model_changed: tuple[str, str] | None = None

    for key, value in patch.items():
        if key in ("llm", "embedding") and isinstance(value, dict):
            old = current.get(key, {})
            if not value.get("api_key") or value.get("api_key") == API_KEY_MASK:
                # 掩码/空值 = 保留原密钥，密钥永不被前端写回
                value["api_key"] = old.get("api_key", "")
        if key == "embedding" and isinstance(value, dict):
            # 记录向量模型是否变化：变了就要清空旧向量、全部笔记/手册标待索引（R4 维度一致性）
            old_model = (current.get("embedding") or {}).get("model") or ""
            new_model = (value.get("model") or "").strip()
            if new_model and new_model != old_model:
                emb_model_changed = (old_model, new_model)
            # 把顶层当前值同步进 mode 对应子对象，保证 cloud/ollama 双套各自记忆
            value = _split_embedding(value)
        merged = {**current.get(key, {}), **value} if isinstance(value, dict) else value
        # 检索/切分参数写库前先钳：前端已钳过一遍，这里兜住直接打 API 的调用方
        if key == "retrieval" and isinstance(merged, dict):
            merged = _clamp_retrieval(merged)
        conn.execute(
            "INSERT INTO config (key, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, json.dumps(merged, ensure_ascii=False), now),
        )
        saved.append(key)

    conn.commit()
    event: dict[str, Any] = {"keys": saved}
    if emb_model_changed is not None:
        event["embedding_model_changed"] = {"old": emb_model_changed[0], "new": emb_model_changed[1]}
    broker.publish("config.updated", event)
    return saved


# 检索/切分参数的合法区间（设置页可填任意值，越界在这里钳，避免脏配置把检索搞挂）
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "top_k": (1, 30),
    "min_similarity": (0.0, 1.0),
    "chunk_size": (200, 4000),
    "pdf_chunk_size": (300, 4000),
    "chunk_overlap": (0, 500),
    "history_turns": (0, 50),
    "embed_max_chars": (100, 8000),
    "rerank_top_n": (5, 100),
    "rerank_keep": (1, 30),
}

# 数值型键（其它是字符串/布尔，不进 _num 的钳制）
_INT_KEYS = ("top_k", "chunk_size", "pdf_chunk_size", "chunk_overlap", "history_turns",
             "embed_max_chars", "rerank_top_n", "rerank_keep")


def _num(value: Any, default: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if n == n else default  # NaN 兜底


def retrieval_params(conn) -> dict[str, Any]:
    """检索与切分参数（含钳制）：检索线程与索引线程都从这里取，不各自读常量。

    重叠还要额外压到「分块大小的一半」以内，否则切块会在原地打转（每块都塞满上一块）。
    """
    return _clamp_retrieval(get_raw(conn, "retrieval"))


def _clamp_retrieval(raw: dict[str, Any]) -> dict[str, Any]:
    """把一组 retrieval 参数钳进合法区间（写库与读库都走这里，库里不会留下脏值）。"""
    out: dict[str, Any] = {}
    for key, (lo, hi) in _PARAM_BOUNDS.items():
        default = float(DEFAULT_CONFIG["retrieval"][key])
        out[key] = max(lo, min(hi, _num(raw.get(key), default)))
    for key in _INT_KEYS:
        out[key] = int(round(out[key]))
    cap = max(0, min(out["chunk_size"], out["pdf_chunk_size"]) // 2)
    out["chunk_overlap"] = min(out["chunk_overlap"], cap)
    # reranker 布尔：显式传入字符串 "false"/"0" 也算关，避免前端序列化把布尔写歪
    out["rerank_enabled"] = bool(raw.get("rerank_enabled", False)) and str(
        raw.get("rerank_enabled")
    ).strip().lower() not in ("false", "0", "off", "")
    # reranker 模型路径：字符串，不去数字钳制
    out["rerank_model_path"] = str(raw.get("rerank_model_path") or "").strip()
    return out


def _mask_secrets(cfg: dict[str, Any]) -> None:
    for key in ("llm", "embedding"):
        section = cfg.get(key, {})
        api_key = section.get("api_key") or ""
        section["api_key_masked"] = API_KEY_MASK if api_key else ""
        section["api_key_set"] = bool(api_key)
        section.pop("api_key", None)  # 明文密钥不返回前端
        # embedding 的 cloud/ollama 子对象里也各存了一份密钥，同样脱敏
        if key == "embedding":
            for mode in ("cloud", "ollama"):
                sub = section.get(mode)
                if not isinstance(sub, dict):
                    continue
                sub_api = sub.get("api_key") or ""
                sub["api_key_masked"] = API_KEY_MASK if sub_api else ""
                sub["api_key_set"] = bool(sub_api)
                sub.pop("api_key", None)
