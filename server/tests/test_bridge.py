import asyncio

import pytest

from alberton_mcp.bridge import Bridge, BridgeUnreachable, WireError


async def test_ping_handshake(fake, session):
    result = await session.bridge.request("ping")
    assert result["contract"] == "1.0"
    assert session.bridge.remote_versions["live"] == "12.4.3"


async def test_pipelined_requests_correlate(session):
    bridge = session.bridge
    a, b = await asyncio.gather(
        bridge.request("get", path="song", props=["tempo"]),
        bridge.request("get", path="song", props=["scale_name"]))
    assert a["values"]["tempo"] == 120.0
    assert b["values"]["scale_name"] == "Major"


async def test_wire_error_is_structured(session):
    with pytest.raises(WireError) as excinfo:
        await session.bridge.request("get", path="song.tracks.99",
                                     props=["name"])
    assert excinfo.value.code == "path_not_found"


async def test_events_reach_the_feed(session):
    bridge = session.bridge
    sub = await bridge.request("subscribe", path="song", props=["tempo"])
    assert sub["values"]["tempo"] == 120.0
    await bridge.request("set", path="song", props={"tempo": 99.0})
    await asyncio.sleep(0.05)
    events = bridge.feed.since(0)
    assert any(e.get("event") == "change" and e.get("prop") == "tempo"
               and e.get("value") == 99.0 for e in events)


async def test_unreachable_bridge():
    bridge = Bridge(host="127.0.0.1", port=1)  # nothing listens there
    with pytest.raises(BridgeUnreachable):
        await bridge.request("ping")
