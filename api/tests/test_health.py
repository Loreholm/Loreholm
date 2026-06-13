import pytest

from app.main import app
from conftest import make_async_client


@pytest.mark.anyio
async def test_health() -> None:
    async with await make_async_client(app) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.anyio
async def test_root() -> None:
    async with await make_async_client(app) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert response.text == "mcp-api: ok\n"
