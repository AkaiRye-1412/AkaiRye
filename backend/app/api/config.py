"""配置与品牌（04 文档 3.1：/api/config、test-llm、test-embedding、/api/brands）。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import config_service, embedding

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
def get_config(conn: Connection = Depends(get_db)) -> dict:
    """读取全部配置（api_key 脱敏为 api_key_set/api_key_masked，NFR-SEC-03）。"""
    return config_service.load_all(conn)


@router.put("/config")
def put_config(patch: dict[str, dict], conn: Connection = Depends(get_db)) -> dict:
    """批量保存配置；保存后广播 config.updated（热生效，不重启）。"""
    saved = config_service.save_many(conn, patch)
    return {"saved": saved}


class LlmTestIn(BaseModel):
    base_url: str = ""
    api_key: str = ""       # 空/掩码 = 使用已保存密钥
    model: str = ""
    temperature: float | None = None


class EmbeddingTestIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    mode: str = ""      # cloud / ollama；留空沿用已存配置


def _resolve(base: str, key: str, stored: dict) -> tuple[str, str]:
    """空值/掩码回退到已存配置（密钥永不出后端，与前端掩码语义一致）。"""
    if not base:
        base = stored.get("base_url") or stored.get("provider") or ""
    if not key or key == config_service.API_KEY_MASK:
        key = stored.get("api_key") or ""
    return base, key


@router.post("/config/test-llm")
def test_llm(body: LlmTestIn, conn: Connection = Depends(get_db)) -> dict:
    """测试 LLM 连通（不落库，04 文档 3.1）。失败返回 {ok:false, error}（HTTP 200）。"""
    import time as _time

    stored = config_service.get_raw(conn, "llm")
    base_url, api_key = _resolve(body.base_url, body.api_key, stored)
    model = body.model or stored.get("model") or ""
    if not (base_url and api_key and model):
        return {"ok": False, "error": "base_url / api_key / model 未配置完整"}

    try:
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key, timeout=15)
        t0 = _time.perf_counter()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping，仅用于连通性测试，回复'pong'即可"}],
            max_tokens=8,
            temperature=body.temperature if body.temperature is not None else stored.get("temperature", 0.7),
        )
        latency = int((_time.perf_counter() - t0) * 1000)
        return {"ok": True, "latency_ms": latency, "model": model, "reply": (resp.choices[0].message.content or "")[:20]}
    except Exception as e:  # noqa: BLE001 - 网络/鉴权等任何失败都原样呈现给用户
        return {"ok": False, "model": model, "error": str(e)[:300]}


@router.post("/config/test-embedding")
def test_embedding(body: EmbeddingTestIn, conn: Connection = Depends(get_db)) -> dict:
    """测试 Embedding 连通，成功返回实际维度 dim（供 R4 校验比对）。

    本地 Ollama 不需要 API Key（地址缺 /v1 时自动补），云端仍要求密钥。
    """
    import time as _time

    stored = config_service.get_raw(conn, "embedding")
    mode = body.mode or stored.get("mode") or "cloud"
    raw_base, api_key = _resolve(body.base_url, body.api_key, stored)
    model = body.model or stored.get("model") or ""
    base_url = embedding.base_url({"mode": mode, "base_url": raw_base})
    local = embedding.is_local(base_url)
    if not (base_url and model) or (not local and not api_key):
        return {"ok": False, "error": "服务地址 / 模型名未配置完整（云端还需 API Key）"}

    try:
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key or embedding.PLACEHOLDER_KEY, timeout=60)
        t0 = _time.perf_counter()
        resp = client.embeddings.create(model=model, input=["连通性测试"])
        latency = int((_time.perf_counter() - t0) * 1000)
        dim = len(resp.data[0].embedding)
        return {"ok": True, "latency_ms": latency, "model": model, "dim": dim,
                "mode": mode, "base_url": base_url, "local": local}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "model": model, "error": str(e)[:300]}


# ---- 品牌（FR-RAG-13：固定枚举 + 可配置新增，不做自由文本输入） ----


class BrandIn(BaseModel):
    name: str
    sort: int | None = None


class BrandPatch(BaseModel):
    enabled: bool | None = None
    sort: int | None = None


@router.get("/brands")
def list_brands(conn: Connection = Depends(get_db)) -> list[dict]:
    rows = conn.execute("SELECT name, sort, enabled FROM brand ORDER BY sort, name").fetchall()
    return [dict(r) for r in rows]


@router.post("/brands", status_code=201)
def add_brand(body: BrandIn, conn: Connection = Depends(get_db)) -> dict:
    try:
        conn.execute("INSERT INTO brand (name, sort) VALUES (?, ?)", (body.name, body.sort or 0))
        conn.commit()
    except Exception as e:  # noqa: BLE001 - UNIQUE 冲突
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": "品牌重名"}) from e
    return {"name": body.name, "sort": body.sort or 0, "enabled": 1}


@router.patch("/brands/{name}")
def patch_brand(name: str, body: BrandPatch, conn: Connection = Depends(get_db)) -> dict:
    row = conn.execute("SELECT name, sort, enabled FROM brand WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "品牌不存在"})
    sort = body.sort if body.sort is not None else row["sort"]
    enabled = int(body.enabled) if body.enabled is not None else row["enabled"]
    conn.execute("UPDATE brand SET sort = ?, enabled = ? WHERE name = ?", (sort, enabled, name))
    conn.commit()
    return {"name": name, "sort": sort, "enabled": enabled}
