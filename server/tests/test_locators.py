"""Reaching every track and every device — returns, master, nested racks."""

import pytest

from alberton_mcp import api, resolve
from alberton_mcp.errors import ToolError


# --- tracks ------------------------------------------------------------------


async def test_index_always_means_a_regular_track(session):
    ref = await resolve.resolve_track(session.bridge, 1)
    assert ref["kind"] == "track" and ref["path"] == "song.tracks.1"


async def test_master_by_word_and_by_name(session):
    for spec in ("master", "Master", "main", "Main"):
        ref = await resolve.resolve_track(session.bridge, spec)
        assert ref == {"kind": "master", "index": None,
                       "path": "song.master_track"} or ref["kind"] == "master"


async def test_return_by_explicit_form_and_by_name(session):
    by_index = await resolve.resolve_track(session.bridge, "return:1")
    assert by_index["path"] == "song.return_tracks.1"
    by_name = await resolve.resolve_track(session.bridge, "return:Delay Return")
    assert by_name["path"] == "song.return_tracks.1"
    bare = await resolve.resolve_track(session.bridge, "Reverb Return")
    assert bare["kind"] == "return" and bare["index"] == 0


async def test_a_regular_track_wins_over_a_return_of_the_same_name(fake, session):
    fake.live.song["tracks"][0]["name"] = "Reverb Return"
    ref = await resolve.resolve_track(session.bridge, "Reverb Return")
    assert ref["kind"] == "track" and ref["index"] == 0


async def test_missing_name_lists_all_three_families(session):
    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_track(session.bridge, "nowhere")
    hint = excinfo.value.hint
    assert "'Lead'" in hint and "Reverb Return" in hint and "Main" in hint


async def test_get_track_reads_the_master(session):
    track = await api.get_track(session, track="master")
    assert track["kind"] == "master" and track["name"] == "Main"
    assert track["mixer"]["volume"]["value"] is not None


async def test_set_track_writes_the_master_mixer(fake, session):
    await api.set_track(session, track="master", volume={"db": -3.0})
    mixer = fake.live.song["master_track"]["mixer_device"]
    assert mixer["volume"]["display_value"] == -3.0


async def test_a_return_is_deleted_from_its_own_vector(fake, session):
    """Song.delete_track(0) would remove a regular track — the wrong one."""
    tracks_before = len(fake.live.song["tracks"])
    result = await api.delete_track(session, track="return:0")
    assert result["kind"] == "return"
    assert len(fake.live.song["return_tracks"]) == 1
    assert len(fake.live.song["tracks"]) == tracks_before


async def test_the_master_cannot_be_deleted_or_duplicated(session):
    for tool in (api.delete_track, api.duplicate_track):
        with pytest.raises(ToolError) as excinfo:
            await tool(session, track="master")
        assert excinfo.value.code == "invalid_argument"


async def test_returns_cannot_be_duplicated(session):
    with pytest.raises(ToolError) as excinfo:
        await api.duplicate_track(session, track="return:0")
    assert "return track" in excinfo.value.message


async def test_a_track_without_slots_says_why(session):
    with pytest.raises(ToolError) as excinfo:
        await api.create_clip(session, track="master", slot=0, length=4.0,
                              name="nope")
    assert excinfo.value.code == "invalid_argument"
    assert "no Session slots" in excinfo.value.hint


async def test_overview_full_locates_returns_and_master(session):
    overview = await api.session_overview(session, detail="full")
    assert overview["returns"][0]["locator"] == "return:0"
    assert overview["master"]["name"] == "Main"
    assert overview["master"]["locator"] == "master"


# --- devices ------------------------------------------------------------------


async def _load_rack(session):
    found = await api.browse(session, query="fakesynth")
    await api.load_device(session, track=0, uri=found["matches"][0]["uri"])


async def test_top_level_device_by_index_and_name(session):
    await _load_rack(session)
    by_index = await resolve.resolve_device(session.bridge, "song.tracks.0", 0)
    by_name = await resolve.resolve_device(session.bridge, "song.tracks.0",
                                           "FakeSynth")
    assert by_index["path"] == by_name["path"] == "song.tracks.0.devices.0"


async def test_a_slash_path_descends_into_a_rack(session):
    await _load_rack(session)
    ref = await resolve.resolve_device(session.bridge, "song.tracks.0",
                                       "FakeSynth/Chain 1/Inner")
    assert ref["path"] == "song.tracks.0.devices.0.chains.0.devices.0"
    assert ref["depth"] == 3
    by_index = await resolve.resolve_device(session.bridge, "song.tracks.0",
                                            "0/0/0")
    assert by_index["path"] == ref["path"]


async def test_a_macro_is_just_a_named_parameter(fake, session):
    await _load_rack(session)
    written = await api.set_device_parameter(session, track=0,
                                             device="FakeSynth",
                                             parameter="Filter Cutoff",
                                             value=90.0)
    assert written["parameter"]["value"] == 90.0
    assert written["parameter"]["name"] == "Filter Cutoff"


async def test_a_nested_device_parameter_is_reachable(fake, session):
    await _load_rack(session)
    written = await api.set_device_parameter(
        session, track=0, device="FakeSynth/Chain 1/Inner",
        parameter="Inner Gain", value=0.75)
    assert written["parameter"]["value"] == 0.75
    inner = fake.live.song["tracks"][0]["devices"][0]["chains"][0]["devices"][0]
    assert inner["parameters"][0]["value"] == 0.75


async def test_a_wrong_segment_names_what_it_looked_in(session):
    await _load_rack(session)
    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_device(session.bridge, "song.tracks.0",
                                     "FakeSynth/Chain 1/Nope")
    assert excinfo.value.code == "not_found"
    assert "'Inner'" in excinfo.value.hint


# --- song ----------------------------------------------------------------------


async def test_tempo_follower_is_settable(fake, session):
    fake.live.song["tempo_follower_enabled"] = False
    result = await api.set_song(session, tempo_follower_enabled=True)
    assert result["values"]["tempo_follower_enabled"] is True
    assert fake.live.song["tempo_follower_enabled"] is True


# --- concurrent editing ---------------------------------------------------------
#
# Found during a stress session: the user added a track in Live while the
# server was adding one of its own. create_*_track computes the new index from
# a count taken before the call, so anything that shifts the vector
# invalidates it.


def _interfere_after_batch(fake, name):
    """Somebody else inserts a track once our create+name batch has landed."""
    original = fake._op_batch

    def hooked(frame, events):
        result = original(frame, events)
        if any(sub.get("method", "").startswith("create_") for sub in
               frame.get("ops", [])):
            from fake_bridge import _track
            fake.live.song["tracks"].insert(0, _track(name))
        return result

    fake._op_batch = hooked
    return original


async def test_a_created_track_is_found_even_if_the_set_shifted(fake, session):
    original = _interfere_after_batch(fake, "theirs")
    try:
        created = await api.create_midi_track(session, name="mine")
    finally:
        fake._op_batch = original
    assert created["track"]["name"] == "mine"
    names = [t["name"] for t in fake.live.song["tracks"]]
    assert names[created["track"]["index"]] == "mine"   # the index was corrected


async def test_an_ambiguous_shift_is_reported_not_guessed(fake, session):
    """If the name is now in two places there is no safe answer, so say so."""
    original = _interfere_after_batch(fake, "mine")
    try:
        with pytest.raises(ToolError) as excinfo:
            await api.create_midi_track(session, name="mine")
    finally:
        fake._op_batch = original
    assert excinfo.value.code == "conflict"
    assert "changed underneath" in excinfo.value.message
    assert "session_overview" in excinfo.value.hint
