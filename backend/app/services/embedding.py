"""向量化服务的提供方适配（云端 OpenAI 兼容 / 本地 Ollama）。

整条链路统一走 OpenAI 兼容协议（openai SDK），本地 Ollama 也提供 /v1/embeddings，
所以差异只有三点，全部收敛在这里：

1. 地址口径：Ollama 的兼容前缀是 /v1，用户只填 ``http://127.0.0.1:11434`` 也接受；
2. 鉴权：本机服务不校验 Key，但 SDK 要求非空串，这里给占位符；
3. 节流：云端按 429 限频做批次间隔，本机则相反——要跑满速。

维度的最终一致性仍由 indexer 的 model_id 标记 + ``_ensure_vec_dim`` 兜底（R4）。
"""

from __future__ import annotations

from urllib.parse import urlparse

# Ollama 默认监听 127.0.0.1:11434；OpenAI 兼容前缀 /v1（原生 /api/embed 不是 OpenAI 协议）
OLLAMA_DEFAULT_BASE = "http://127.0.0.1:11434/v1"
PLACEHOLDER_KEY = "sk-local"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def base_url(cfg: dict) -> str:
    """配置里的服务地址：兼容历史键名 ``provider``，并补全 Ollama 的 /v1 前缀。"""
    raw = (cfg.get("base_url") or cfg.get("provider") or "").strip().rstrip("/")
    if not raw:
        return OLLAMA_DEFAULT_BASE if cfg.get("mode") == "ollama" else ""
    if cfg.get("mode") == "ollama" and not raw.endswith("/v1"):
        return f"{raw}/v1"
    return raw


def api_key(cfg: dict) -> str:
    """云端用真实 Key；本机服务用占位符（SDK 只要求非空）。"""
    return (cfg.get("api_key") or "").strip() or PLACEHOLDER_KEY


def is_local(url: str) -> bool:
    """是否本机地址（本机不节流、不算"未配置密钥"）。"""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS


def is_configured(cfg: dict) -> bool:
    """是否具备向量化条件：有地址 + 有模型名。

    本机模式不要求 API Key（这是「本地自托管」能开箱即用的前提）；
    云端模式仍要求 Key —— 但历史数据里 key 可能只存在 api_key 字段，统一交给调用方判断。
    """
    return bool(base_url(cfg) and (cfg.get("model") or "").strip())


def ollama_root(url: str = "") -> str:
    """Ollama 原生 API 根（去掉 /v1），供 /api/tags、/api/show 使用。"""
    root = (url or OLLAMA_DEFAULT_BASE).strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    return root
