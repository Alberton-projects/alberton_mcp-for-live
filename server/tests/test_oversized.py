"""A request too big for the wire, and an error with no id to pin it on.

The bridge answers `too_large` with `"id": null` and then drops the connection,
because the line it refused never parsed far enough to have an id. Our client
used to discard that frame and report only "connection to the bridge lost" —
which reads like Live crashed, when in fact the caller simply asked for too
much in one go.
"""
import pytest

from alberton_mcp import api
from alberton_mcp.bridge import LINE_MAX, BridgeUnreachable, WireError


# A string is the cheap way to be enormous; the guard measures the serialised
# line, so what makes it big does not matter.
OVERSIZED = "x" * (LINE_MAX + 1)


async def test_an_oversized_line_is_refused_before_it_is_sent(fake, session):
    await api.session_overview(session, detail="minimal")   # connect first
    with pytest.raises(WireError) as caught:
        await session.bridge.request("set", path="song",
                                     props={"name": OVERSIZED})
    assert caught.value.code == "too_large"
    assert str(LINE_MAX) in caught.value.message, caught.value.message
    assert "Split it" in (caught.value.raw or {}).get("hint", "")


async def test_the_connection_is_not_spent_on_the_attempt(fake, session):
    """Refusing it here means we never provoke the disconnect at all."""
    await api.session_overview(session, detail="minimal")
    with pytest.raises(WireError):
        await session.bridge.request("set", path="song",
                                     props={"name": OVERSIZED})
    out = await api.session_overview(session, detail="minimal")
    assert out["counts"]["tracks"] >= 1, "the bridge is still there"


async def test_nothing_of_it_reached_the_wire(fake, session):
    await api.session_overview(session, detail="minimal")
    before = len(fake.op_log)
    with pytest.raises(WireError):
        await session.bridge.request("set", path="song",
                                     props={"name": OVERSIZED})
    assert len(fake.op_log) == before, "the frame must not go out at all"


async def test_a_reasonable_batch_still_goes_through(fake, session):
    notes = [{"pitch": 60 + (i % 12), "start": float(i) * 0.25,
              "duration": 0.25} for i in range(200)]
    await api.create_clip(session, track=0, slot=0, length=64.0, name="C",
                          notes=notes)
    got = await api.get_notes(session, clip={"track": 0, "slot": 0})
    assert len(got["notes"]) == 200


async def test_an_id_less_refusal_survives_into_the_drop(fake, session):
    """When the bridge does refuse and hang up, say what it refused."""
    await api.session_overview(session, detail="minimal")
    bridge = session.bridge
    bridge._last_wire_complaint = {"code": "too_large",
                                   "message": "request line exceeds 16777216 bytes"}
    future = bridge._pending.setdefault(999, __import__("asyncio").get_event_loop()
                                        .create_future())
    bridge._drop_connection()
    with pytest.raises(BridgeUnreachable) as caught:
        await future
    assert "too_large" in str(caught.value), str(caught.value)
    assert "16777216" in str(caught.value), "the bridge's own words, kept"
