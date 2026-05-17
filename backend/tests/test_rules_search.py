"""规则检索接口测试。"""

from httpx import AsyncClient


async def test_search_rules_returns_results(client: AsyncClient) -> None:
    resp = await client.post("/v1/rules/search", json={"query": "层系统", "top_k": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "total" in data


async def test_search_rules_empty_result(client: AsyncClient) -> None:
    resp = await client.post("/v1/rules/search", json={"query": "不存在的关键词xyz123", "top_k": 5})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
