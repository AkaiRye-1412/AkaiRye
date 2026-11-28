"""本地 Ollama 探测（设置页「本地自托管」用）。

只读能力：列出本机已下载的模型，并标注是否可用于向量化、向量维度是多少。

- 数据源：``GET /api/tags``（列表 + capabilities）+ ``POST /api/show``（读
  ``model_info["<arch>.embedding_length"]``）；
- 不可用（没装/没启动/端口被占）时返回 ``running=False`` + ``error``，**不抛 500**——
  设置页要能正常渲染并提示"未检测到本地服务"；
- 是否可用于向量化只看 capabilities 里有没有 ``embedding``：缺 ``pooling_type`` 元数据的
  第三方 GGUF（例如直接下载的 Qwen3-Embedding 转换版）会被 Ollama 当成对话模型，
  这时即使名字里有 embedding 也用不了，UI 需要据此给出提示。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import config_service, embedding

router = APIRouter(prefix="/api/ollama", tags=["ollama"])

TAGS_TIMEOUT = 3.0
SHOW_TIMEOUT = 5.0


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 仅本机回环
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _dim_of(model_info: dict) -> int:
    """从 model_info 里取向量维度（键名形如 qwen3.embedding_length）。"""
    for key, value in (model_info or {}).items():
        if key.endswith(".embedding_length") and isinstance(value, int) and value > 0:
            return value
    return 0


@router.get("/models")
def list_models(base_url: str = "", conn: Connection = Depends(get_db)) -> dict:
    """本机 Ollama 模型清单；``base_url`` 留空则用设置里的向量化地址。"""
    cfg = config_service.get_raw(conn, "embedding")
    url = base_url.strip() or embedding.base_url(cfg)
    root = embedding.ollama_root(url)

    try:
        tags = _get(f"{root}/api/tags", TAGS_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {
            "running": False,
            "base_url": f"{root}/v1",
            "error": f"未检测到本机 Ollama（{root}）：{getattr(e, 'reason', e)}",
            "models": [],
        }

    out: list[dict] = []
    for item in tags.get("models") or []:
        name = item.get("name") or ""
        caps = item.get("capabilities") or []
        can_embed = "embedding" in caps
        detail = item.get("details") or {}
        dim = 0
        if can_embed:
            try:
                info = _post(f"{root}/api/show", {"model": name}, SHOW_TIMEOUT)
                dim = _dim_of(info.get("model_info") or {})
            except (urllib.error.URLError, OSError, ValueError):
                dim = 0  # 维度读不到不影响列出，UI 会提示用「测试连接」实测
        out.append(
            {
                "name": name,
                "size": item.get("size") or 0,
                "family": detail.get("family") or "",
                "parameters": detail.get("parameter_size") or "",
                "quantization": detail.get("quantization_level") or "",
                "embedding": can_embed,
                "dim": dim,
            }
        )

    # 能向量化的排前面，其次按名字
    out.sort(key=lambda m: (not m["embedding"], m["name"]))
    return {"running": True, "base_url": f"{root}/v1", "error": "", "models": out}
