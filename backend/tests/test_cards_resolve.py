"""牌张查询接口测试。"""

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app.schemas import CardInfo


async def test_resolve_card_found(client: AsyncClient) -> None:
    with patch("app.services.card_service.CardService.resolve_and_get", new_callable=AsyncMock) as mock:
        mock.return_value = CardInfo(input_name="谦卑", resolved_zh_name="谦卑", oracle_name="Humility", oracle_text="All creatures lose all abilities and are 1/1.")
        resp = await client.post("/v1/cards/resolve", json={"name": "谦卑"})
        assert resp.status_code == 200
        assert resp.json()["found"] is True
        assert resp.json()["card"]["oracle_name"] == "Humility"


async def test_resolve_card_not_found(client: AsyncClient) -> None:
    with patch("app.services.card_service.CardService.resolve_and_get", new_callable=AsyncMock) as mock:
        mock.return_value = None
        resp = await client.post("/v1/cards/resolve", json={"name": "不存在的牌"})
        assert resp.status_code == 200
        assert resp.json()["found"] is False
