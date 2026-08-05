"""Naming a track does not entitle anyone to write to whatever is at its index.

Confirmed against a real Live on 2026-08-04: `set_track(track="ZZ race 2")`
resolved to index 29, a human deleted that track, and the write landed on the
track that slid into index 29 — and returned success. The worst failure this
server can have, because nothing tells the caller.

Reviewed 2026-08-05: the guard itself had three holes, each pinned below —
a guard leaked from a read-only call could block an unrelated write, a guard
whose index had vanished produced a success-shaped empty answer, and
song_batch / set_device_parameter carried no guard at all.
"""
import asyncio

import pytest

from alberton_mcp import api, resolve
from alberton_mcp import server as srv
from alberton_mcp.errors import ToolError
from fake_bridge import _param


async def test_a_named_track_that_moved_is_not_written_to(fake, session):
    bridge = session.bridge
    resolve.begin_call()
    ref = await resolve.resolve_track(bridge, "Bass")
    assert isinstance(ref["ptr"], int), "a name resolution carries the identity"
    assert resolve.current_guards() == [ref]

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


async def test_a_deleted_tail_track_is_reported_not_skipped(fake, session):
    """When the guarded index itself is gone, expect fails with
    path_not_found rather than expectation_failed. Until 2026-08-05 only the
    second code was handled, and the caller got a success-shaped empty answer
    for a write that never ran."""
    bridge = session.bridge
    resolve.begin_call()
    ref = await resolve.resolve_track(bridge, "Loops")     # the tail, index 2
    del fake.live.song["tracks"][ref["index"]]             # index 2 now gone

    with pytest.raises(ToolError) as caught:
        await api._run_atomic(bridge, [{"op": "set", "path": ref["path"],
                                        "props": {"name": "X"}}], "set_track")
    assert caught.value.code == "not_found"
    assert "nothing was written" in (caught.value.hint or "")


async def test_against_an_older_script_it_writes_then_takes_it_back(fake, session):
    """A script without `expect` cannot stop the write, only undo it."""
    bridge = session.bridge
    resolve.begin_call()
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
    resolve.begin_call()
    ref = await resolve.resolve_track(bridge, 1)
    assert "ptr" not in ref
    assert resolve.current_guards() == []
    await api.set_track(session, track=1, name="renamed by index")
    assert fake.live.song["tracks"][1]["name"] == "renamed by index"


async def test_guards_do_not_leak_into_the_next_call(fake, session, monkeypatch):
    """A read that resolved a name must not make a later write fail — even
    when the read's object was deleted in between. Pinned through the real
    tool boundary (server._run), which is what scopes the guards; the old
    version of this test wrote to the read's own untouched track, so the
    leaked guard passed trivially and the leak was invisible."""
    monkeypatch.setattr(srv, "_session", session)
    out = await srv._run(api.get_track, track="Bass")      # read-only, by name
    assert "error" not in out
    del fake.live.song["tracks"][1]                        # Bass is gone now
    out = await srv._run(api.set_track, track=0, name="still fine")
    assert "error" not in out, out
    assert fake.live.song["tracks"][0]["name"] == "still fine"


async def test_parallel_calls_keep_their_own_guards(fake, session, monkeypatch):
    """Two tool calls in flight at once: each write's batch must carry exactly
    its own guard. As shared bridge state, an interleaving could hand one
    call's guard to the other — or consume it out from under its write."""
    monkeypatch.setattr(srv, "_session", session)
    results = await asyncio.gather(
        srv._run(api.set_track, track="Bass", name="one"),
        srv._run(api.set_track, track="Lead", name="two"))
    assert all("error" not in r for r in results), results
    write_batches = [f for op, f in fake.op_log if op == "batch"
                     and any(o["op"] == "set" for o in f["ops"])]
    assert len(write_batches) == 2
    for frame in write_batches:
        expects = [o for o in frame["ops"] if o["op"] == "expect"]
        assert len(expects) == 1, frame["ops"]


async def test_song_batch_carries_the_guard(fake, session):
    """song_batch resolves names like any tool; until 2026-08-05 its batch
    went out with no expect in front of the writes."""
    resolve.begin_call()
    await api.song_batch(session, calls=[
        {"tool": "set_track", "params": {"track": "Bass", "name": "sb"}}])
    frame = [f for op, f in fake.op_log if op == "batch"][-1]
    ops = [o["op"] for o in frame["ops"]]
    assert ops[0] == "expect", ops
    assert fake.live.song["tracks"][1]["name"] == "sb"


async def test_song_batch_without_stop_on_error_trusts_its_locators(fake, session):
    """stop_on_error=false runs everything regardless, so a failed expect
    could not protect anything — the guard is documented out, not smuggled in."""
    resolve.begin_call()
    await api.song_batch(session, calls=[
        {"tool": "set_track", "params": {"track": "Bass", "name": "loose"}}],
        stop_on_error=False)
    frame = [f for op, f in fake.op_log if op == "batch"][-1]
    assert "expect" not in [o["op"] for o in frame["ops"]]


async def test_set_device_parameter_carries_the_guard(fake, session):
    """Track, device and parameter all resolve by name; the write used to be
    a bare top-level set with no guard at all."""
    fake.live.song["tracks"][0]["devices"].append({
        "__class__": "PluginDevice", "name": "Synth",
        "class_name": "PluginDevice",
        "parameters": [_param("Cutoff", 0.5, 0.0, 1.0, 0.0)],
        "chains": []})
    resolve.begin_call()
    await api.set_device_parameter(session, track="Lead", device="Synth",
                                   parameter="Cutoff", value=0.7)
    assert not [f for op, f in fake.op_log if op == "set"], \
        "the write must ride inside a guarded batch, not a bare set"
    frame = [f for op, f in fake.op_log if op == "batch"
             and any(o["op"] == "set" for o in f["ops"])][-1]
    ops = [o["op"] for o in frame["ops"]]
    assert "expect" in ops, ops
    assert fake.live.song["tracks"][0]["devices"][0][
        "parameters"][0]["value"] == 0.7


async def test_the_ordinary_case_still_works(fake, session):
    resolve.begin_call()
    await api.set_track(session, track="Bass", name="renamed by name")
    assert fake.live.song["tracks"][1]["name"] == "renamed by name"


async def test_it_costs_no_extra_round_trip(fake, session):
    resolve.begin_call()
    before = len([1 for op, _ in fake.op_log if op == "batch"])
    await api.set_track(session, track="Bass", name="x")
    after = len([1 for op, _ in fake.op_log if op == "batch"])
    # one batch for the name lookup, one for the write: the identity check is
    # inside the write's batch, not a batch of its own
    assert after - before == 2
