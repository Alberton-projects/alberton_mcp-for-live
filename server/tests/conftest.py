import pytest_asyncio

from alberton_mcp import api
from alberton_mcp.bridge import Bridge

from fake_bridge import FakeBridgeServer


@pytest_asyncio.fixture
async def fake():
    server = FakeBridgeServer()
    await server.start()
    yield server
    await server.stop()


@pytest_asyncio.fixture
async def session(fake):
    bridge = Bridge(host="127.0.0.1", port=fake.port)
    sess = api.Session(bridge)
    yield sess
    await bridge.close()
