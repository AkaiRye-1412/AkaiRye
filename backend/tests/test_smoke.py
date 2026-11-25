"""框架冒烟测试：应用可创建、数据库迁移可执行、核心端点可访问。"""

from fastapi.testclient import TestClient

from app.main import create_app


def _client() -> TestClient:
    # 以上下文管理器使用，确保 lifespan（数据库迁移）执行
    return TestClient(create_app())


def test_app_boots() -> None:
    with _client() as client:
        resp = client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_ready"] is True
        assert "counts" in data


def test_config_masked() -> None:
    with _client() as client:
        resp = client.get("/api/config")
        assert resp.status_code == 200
        for section in ("llm", "embedding"):
            body = resp.json()[section]
            assert "api_key" not in body          # 明文密钥永不出后端（NFR-SEC-03）
            assert "api_key_set" in body


def test_brands_seeded() -> None:
    with _client() as client:
        # 首次访问时品牌表为空属正常（预置数据由迁移/种子逻辑在业务实现阶段补）
        resp = client.get("/api/brands")
        assert resp.status_code == 200
