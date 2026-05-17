"""安全功能测试：限流、认证、安全头、健康检查。"""

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    """GET /health 返回 200 和状态信息。"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert "checks" in data


async def test_security_headers(client: AsyncClient) -> None:
    """响应应包含安全头。"""
    resp = await client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-XSS-Protection") == "1; mode=block"


async def test_no_api_key_passes_when_not_configured(client: AsyncClient) -> None:
    """未配置 API Key 时，无需 Key 即可通过。"""
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.api_key = ""
        resp = await client.post("/v1/rules/search", json={"query": "test", "top_k": 1})
        # 不应返回 401
        assert resp.status_code != 401


async def test_api_key_required_when_configured(client: AsyncClient) -> None:
    """配置了 API Key 时，无 Key 应返回 401。"""
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.api_key = "test-secret-key"
        resp = await client.post("/v1/rules/search", json={"query": "test", "top_k": 1})
        assert resp.status_code == 401
        assert "X-API-Key" in resp.json()["detail"]


async def test_api_key_wrong_value_returns_401(client: AsyncClient) -> None:
    """错误的 API Key 应返回 401。"""
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.api_key = "test-secret-key"
        resp = await client.post(
            "/v1/rules/search",
            json={"query": "test", "top_k": 1},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401


async def test_api_key_correct_value_passes(client: AsyncClient) -> None:
    """正确的 API Key 应通过认证。"""
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.api_key = "test-secret-key"
        resp = await client.post(
            "/v1/rules/search",
            json={"query": "test", "top_k": 1},
            headers={"X-API-Key": "test-secret-key"},
        )
        assert resp.status_code != 401


async def test_health_no_auth_required(client: AsyncClient) -> None:
    """health 端点不需要认证。"""
    with patch("app.core.auth.settings") as mock_settings:
        mock_settings.api_key = "test-secret-key"
        resp = await client.get("/health")
        assert resp.status_code == 200


async def test_rate_limit_allows_normal_requests(client: AsyncClient) -> None:
    """正常请求不应被限流。"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.status_code != 429
