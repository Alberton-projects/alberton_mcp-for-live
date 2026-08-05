"""Naming a track does not entitle anyone to write to whatever is at its index.

Confirmed against a real Live on 2026-08-04: `set_track(track="ZZ race 2")`
resolved to index 29, a human deleted that track, and the write landed on the
track that slid into index 29 — and returned success. The worst failure this
server can have, because nothing tells the caller.
"""
import pytest

from alberton_mcp import api, resolve
from alberton_mcp.errors import ToolError


async def test_a_named_track_that_moved_is_not_written_to(fake, session):
    bridge = session.bridge
    ref = await resolve.resolve_track(bridge, "Bass")
    assert isinstance(ref["ptr"], int), "a name resolution carries the identity"
    assert bridge.guards == [ref]

    # the human, between our resolve and our write: the named track is gone and
    # a different one now occupies that index
    del fake.live.song["tracks"][ref["index"]]

    with pytest.raises(ToolError) as caught:
        await api._run_atomic(bridge, [{"op": "set", "path": ref["path"],
                                        "props": {"name": "WRITTEN BY A RACE"}}],
                              "set_track")
    assert caught.value.code == "not_found"
    assert "Bass" in caught.value.message and "no longer" in caught.value.message
    assert "nothing was written" in (caught.value.hint or "")

    # contract 1.2: the expect op fails first and the batch stops, so the write
    # never happens. Nothing to undo, and nothing to take back.
    assert [t["name"] for t in fake.live.song["tracks"]] == ["Lead", "Loops"]
    assert not [f for op, f in fake.op_log
                if op == "call" and f.get("method") == "undo"], \
        "there is nothing to undo when nothing was written"


async def test_against_an_older_script_it_writes_then_takes_it_back(fake, session):
    """A script without `expect` cannot stop the write, only undo it."""
    bridge = session.bridge
    ref = await resolve.resolve_track(bridge, "Bass")
    bridge.contract = "1.1"                     # pretend the script is older
    del fake.live.song["tracks"][ref["index"]]

    with pytest.raises(ToolError) as caught:
        await api._run_atomic(bridge, [{"op": "set", "path": ref["path"],
                                        "props": {"name": "WRITTEN BY A RACE"}}],
                              "set_track")
    assert caught.value.code == "not_found"
    assert [f for op, f in fake.op_log
            if op == "call" and f.get("method") == "undo"], \
        "the older path must at least try to take it back"


async def test_an_index_locator_is_not_guarded(fake, session):
    """The caller said index 1. Index 1 is what index 1 means."""
    bridge = session.bridge
    ref = await resolve.resolve_track(bridge, 1)
    assert "ptr" not in ref
    assert bridge.guards == []
    await api.set_track(session, track=1, name="renamed by index")
    assert fake.live.song["tracks"][1]["name"] == "renamed by index"


async def test_guards_do_not_leak_into_the_next_call(fake, session):
    """A read that resolved a name must not make a later write fail."""
    bridge = session.bridge
    await api.get_track(session, track="Bass")          # resolves by name
    assert bridge.guards, "the read registered one"
    # a later, unrelated write by index on the same path must go through
    index = 1
    await api.set_track(session, track=index, name="still fine")
    assert fake.live.song["tracks"][index]["name"] == "still fine"


async def test_the_ordinary_case_still_works(fake, session):
    await api.set_track(session, track="Bass", name="renamed by name")
    assert fake.live.song["tracks"][1]["name"] == "renamed by name"


async def test_it_costs_no_extra_round_trip(fake, session):
    before = len([1 for op, _ in fake.op_log if op == "batch"])
    await api.set_track(session, track="Bass", name="x")
    after = len([1 for op, _ in fake.op_log if op == "batch"])
    # one batch for the name lookup, one for the write: the identity check is
    # inside the write's batch, not a batch of its own
    assert after - before == 2
