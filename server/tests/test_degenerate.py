"""Sets with nothing in them.

A set with no tracks at all cannot be built against a real Live without
destroying whatever is open, so it lives here. `tools/degenerate_probe.py`
covers the rest — empty clips, awkward names, values at their limits — against
the real thing.
"""

import pytest

from alberton_mcp import api, resolve
from alberton_mcp.errors import ToolError


@pytest.fixture
def bare(fake):
    """A set Live would open on a fresh install with everything removed."""
    fake.live.song["tracks"] = []
    fake.live.song["return_tracks"] = []
    fake.live.song["scenes"] = []
    return fake


async def test_overview_of_an_empty_set(bare, session):
    overview = await api.session_overview(session, detail="standard")
    assert overview["counts"] == {"tracks": 0, "scenes": 0, "returns": 0}
    assert overview["tracks"] == [] and overview["scenes"] == []
    assert overview["tempo"] == 120.0          # the song itself still reads


async def test_full_overview_of_an_empty_set_still_finds_the_master(bare, session):
    overview = await api.session_overview(session, detail="full")
    assert overview["returns"] == []
    assert overview["master"]["name"] == "Main"


async def test_locating_a_track_in_an_empty_set(bare, session):
    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_track(session.bridge, 0)
    assert excinfo.value.code == "not_found"
    assert "there are 0 tracks" in excinfo.value.hint

    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_track(session.bridge, "anything")
    assert "none" in excinfo.value.hint      # no candidates to offer


async def test_the_master_is_reachable_with_no_tracks(bare, session):
    track = await api.get_track(session, track="master")
    assert track["kind"] == "master"


async def test_scenes_note_is_absent_when_there_are_none(bare, session):
    overview = await api.session_overview(session, detail="minimal")
    assert "scenes_note" not in overview


async def test_creating_the_first_track_of_an_empty_set(bare, session):
    created = await api.create_midi_track(session, name="first")
    assert created["track"]["index"] == 0
    overview = await api.session_overview(session, detail="minimal")
    assert overview["counts"]["tracks"] == 1


async def test_a_set_with_tracks_but_no_scenes(fake, session):
    fake.live.song["scenes"] = []
    for track in fake.live.song["tracks"]:
        track["clip_slots"] = []
    overview = await api.session_overview(session, detail="standard")
    assert overview["counts"]["scenes"] == 0
    assert overview["tracks"][0]["clips"] == {}
    with pytest.raises(ToolError) as excinfo:
        await api.create_clip(session, track=0, slot=0, length=4.0, name="x")
    assert "no Session slots" in excinfo.value.hint


# --- frozen tracks --------------------------------------------------------------


async def test_writing_notes_to_a_frozen_track_warns(fake, session):
    """Live's LOM allows it although its UI locks the clip, and the rendered
    audio does not change — so success without a word would mislead.
    Verified against Live 12.4.3."""
    await api.create_clip(session, track=0, slot=0, length=4.0, name="c")
    fake.live.song["tracks"][0]["is_frozen"] = True
    result = await api.edit_notes(session, clip={"track": 0, "slot": 0},
                                  add=[{"pitch": 60, "start": 0.0,
                                        "duration": 0.5}])
    assert result["counts"]["added"] == 1        # it really was written
    assert "unfrozen" in result["warning"]


async def test_no_warning_on_a_normal_track(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0, name="c")
    result = await api.edit_notes(session, clip={"track": 0, "slot": 0},
                                  add=[{"pitch": 60, "start": 0.0,
                                        "duration": 0.5}])
    assert "warning" not in result


async def test_overview_and_get_track_report_frozen(fake, session):
    fake.live.song["tracks"][1]["is_frozen"] = True
    overview = await api.session_overview(session, detail="standard")
    assert overview["tracks"][1]["frozen"] is True
    assert "frozen" not in overview["tracks"][0]     # only when it is true
    assert (await api.get_track(session, track=1))["frozen"] is True
