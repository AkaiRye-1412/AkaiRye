"""本地向量化（Ollama）配置口径与降级行为。

对应能力：
- services.embedding 的地址归一化 / 本机判定（Ollama 的 /v1 前缀、免密钥）；
- /api/system/status 把「本地模型 + 地址」判定为已配置；
- Ollama 没启动时 /api/ollama/models 与 test-embedding 只报错不 500。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import embedding


@pytest.fixture()
def client():
    c = TestClient(create_app())
    c.__enter__()
    yield c
    c.__exit__(None, None, None)


def test_base_url_completes_v1_for_ollama():
    assert embedding.base_url({"mode": "ollama", "provider": "http://127.0.0.1:11434"}) == (
        "http://127.0.0.1:11434/v1"
    )
    assert embedding.base_url({"mode": "ollama", "base_url": "http://127.0.0.1:11434/v1"}) == (
        "http://127.0.0.1:11434/v1"
    )
    # 尾部斜杠也要归一
    assert embedding.base_url({"mode": "ollama", "provider": "http://127.0.0.1:11434/"}) == (
        "http://127.0.0.1:11434/v1"
    )


def test_base_url_untouched_for_cloud():
    url = "https://ark.cn-beijing.volces.com/api/v3"
    assert embedding.base_url({"mode": "cloud", "provider": url}) == url
    assert embedding.base_url({"provider": url}) == url  # 历史配置没有 mode 字段


def test_empty_ollama_base_falls_back_to_default():
    assert embedding.base_url({"mode": "ollama"}) == embedding.OLLAMA_DEFAULT_BASE
    assert embedding.base_url({"mode": "cloud"}) == ""


def test_is_local_hosts():
    assert embedding.is_local("http://127.0.0.1:11434/v1")
    assert embedding.is_local("http://localhost:11434/v1")
    assert embedding.is_local("http://[::1]:11434/v1")
    assert not embedding.is_local("https://ark.cn-beijing.volces.com/api/v3")
    assert not embedding.is_local("")


def test_is_configured_needs_model():
    assert embedding.is_configured({"mode": "ollama", "model": "qwen3-embed-4b"})
    assert not embedding.is_configured({"mode": "ollama", "model": "  "})


def test_ollama_root_strips_v1():
    assert embedding.ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert embedding.ollama_root("http://127.0.0.1:11434") == "http://127.0.0.1:11434"


def test_status_counts_local_embedding_as_configured(client):
    c = client
    c.put(
        "/api/config",
        json={
            "embedding": {
                "mode": "ollama",
                "provider": "http://127.0.0.1:11434",
                "model": "qwen3-embed-4b",
                "dim": 2560,
            }
        },
    )
    status = c.get("/api/system/status").json()
    assert "embedding" not in status["missing_config_keys"]


def test_status_requires_key_for_cloud(client):
    c = client
    c.put(
        "/api/config",
        json={"embedding": {"mode": "cloud", "provider": "https://example.com/v1", "model": "m"}},
    )
    status = c.get("/api/system/status").json()
    assert "embedding" in status["missing_config_keys"]


def test_ollama_models_reports_down_service(client):
    """端口上没有服务：返回 running=False + error，HTTP 仍是 200。"""
    c = client
    resp = c.get("/api/ollama/models", params={"base_url": "http://127.0.0.1:9/v1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["models"] == []
    assert "未检测到本机 Ollama" in body["error"]


def test_test_embedding_local_without_key_does_not_500(client):
    """本地模式下没填 Key 也要走到真实请求，失败只返回 ok=False。"""
    c = client
    resp = c.post(
        "/api/config/test-embedding",
        json={
            "mode": "ollama",
            "base_url": "http://127.0.0.1:9",
            "model": "qwen3-embed-4b",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "未配置完整" not in (body.get("error") or "")
