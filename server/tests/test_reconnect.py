"""What survives a connection being replaced — found by the lifecycle probe.

Subscriptions live inside the Remote Script and die with their socket, and a
restarted Live hands out subscription ids from 1 again. Anything the server
cached about the far side has to be void from that moment.
"""

import asyncio

from alberton_mcp import api
from alberton_mcp.bridge import Bridge

from fake_bridge import FakeBridgeServer


async def test_epoch_advances_on_every_handshake(session):
    assert session.bridge.epoch == 0          # nothing connected yet
    await api.session_overview(session, detail="minimal")
    assert session.bridge.epoch == 1
    await session.bridge.close()
    await api.session_overview(session, detail="minimal")
    assert session.bridge.epoch == 2          # reconnected lazily


async def test_watches_are_voided_when_the_connection_is_replaced(fake, session):
    watched = await api.watch(session, path="song", props=["tempo"])
    await api.set_song(session, tempo=99.0)
    await asyncio.sleep(0.05)
    assert session.watches and session.bridge.feed.since(0)

    await session.bridge.close()          # Live went away
    changes = await api.get_changes(session)

    assert changes["watches_dropped"] == [watched["watch_id"]]
    assert changes["active_watches"] == {}
    assert changes["events"] == []        # stale events cannot be reattributed
    assert "watch() again" in changes["note"]


async def test_unwatch_after_a_drop_is_not_an_error(fake, session):
    watched = await api.watch(session, path="song", props=["tempo"])
    await session.bridge.close()
    result = await api.unwatch(session, watch_id=watched["watch_id"])
    assert result["unwatched"] == watched["watch_id"]
    assert "previous connection" in result["note"]


async def test_watching_again_after_a_restart_starts_clean(fake, session):
    await api.watch(session, path="song", props=["tempo"])
    await session.bridge.close()

    # a new Live: the fake hands out subscription ids from 1 again
    restarted = FakeBridgeServer()
    await restarted.start()
    try:
        session.bridge.port = restarted.port
        fresh = await api.watch(session, path="song", props=["tempo"])
        assert fresh["watch_id"] == 1
        assert list(session.watches) == [1]   # the old registry is gone
        changes = await api.get_changes(session)
        assert changes["active_watches"] == {"1": {"path": "song",
                                                   "props": ["tempo"]}}
        assert "watches_dropped" not in changes
    finally:
        await restarted.stop()


async def test_a_gone_event_evicts_its_watch(fake, session):
    """`gone` is best-effort from the bridge; when it does arrive, honour it."""
    watched = await api.watch(session, path="song.tracks.0", props=["name"])
    session.bridge.feed.ingest({"event": "gone", "sub": watched["watch_id"],
                                "seq": 2, "reason": "path_invalid"})
    changes = await api.get_changes(session, verify=False)
    assert [e["kind"] for e in changes["events"]] == ["gone"]
    assert changes["active_watches"] == {}      # evicted, not advertised


async def test_a_deleted_watched_object_is_caught_at_pull_time(fake, session):
    """Live sends nothing when it deletes a watched object — verified against
    12.4.3 — so get_changes checks liveness itself."""
    watched = await api.watch(session, path="song.tracks.2", props=["name"])
    await api.delete_track(session, track=2)

    changes = await api.get_changes(session)
    assert changes["watches_died"] == [{"watch_id": watched["watch_id"],
                                        "path": "song.tracks.2"}]
    assert changes["active_watches"] == {}
    assert "no longer exists" in changes["note"]

    # and it is not reported twice
    assert "watches_died" not in await api.get_changes(session)


async def test_verification_can_be_skipped(fake, session):
    await api.watch(session, path="song.tracks.2", props=["name"])
    await api.delete_track(session, track=2)
    fake.op_log.clear()
    changes = await api.get_changes(session, verify=False)
    assert "watches_died" not in changes
    assert not fake.op_log          # no wire traffic at all


async def test_reconnecting_to_a_different_set_is_not_stale(fake, session):
    """The server holds no per-document state, so pointing at another Live
    just works — this is the case the lifecycle probe hit for real."""
    before = await api.session_overview(session, detail="minimal")
    assert before["counts"]["tracks"] == 3

    other = FakeBridgeServer()
    other.live.song["tracks"] = other.live.song["tracks"][:1]
    other.live.song["tempo"] = 140.0
    await other.start()
    try:
        await session.bridge.close()
        session.bridge.port = other.port
        after = await api.session_overview(session, detail="minimal")
        assert after["counts"]["tracks"] == 1
        assert after["tempo"] == 140.0
    finally:
        await other.stop()


async def test_watching_the_playhead_warns_about_its_rate(session):
    """It fires on every bridge tick while playing, and a few such watches
    dominate the event budget — measured against Live 12.4.3."""
    watched = await api.watch(session, path="song",
                              props=["current_song_time"])
    assert "ten times a second" in watched["warning"]
    quiet = await api.watch(session, path="song", props=["tempo"])
    assert "warning" not in quiet
