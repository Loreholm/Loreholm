import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.append(str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


async def make_async_client(app, base_url: str = "http://testserver") -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url=base_url)
