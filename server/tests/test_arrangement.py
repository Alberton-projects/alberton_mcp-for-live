import pytest

from alberton_mcp import api
from alberton_mcp.errors import ToolError

NOTES = [{"pitch": 60, "start": 0.0, "duration": 0.5},
         {"pitch": 64, "start": 1.0 / 3.0, "duration": 1.0 / 3.0}]


async def test_create_arrangement_clip(fake, session):
    result = await api.create_arrangement_clip(
        session, track="Lead", time=32.0, length=8.0, name="verse",
        color="#3DC300", notes=NOTES, signature_numerator=7,
        signature_denominator=4)
    clip = result["clip"]
    assert clip["view"] == "arrangement"
    assert clip["start"] == 32.0 and clip["end"] == 40.0
    assert clip["name"] == "verse"
    assert len(result["added_note_ids"]) == 2
    live = fake.live.song["tracks"][0]["arrangement_clips"][0]
    assert live["signature_numerator"] == 7
    assert live["notes"][1]["start"] == pytest.approx(1.0 / 3.0)


async def test_overlap_is_refused_before_live_trims_it(fake, session):
    await api.create_arrangement_clip(session, track=0, time=0.0, length=16.0,
                                      name="a")
    with pytest.raises(ToolError) as excinfo:
        await api.create_arrangement_clip(session, track=0, time=8.0,
                                          length=8.0, name="b")
    assert excinfo.value.code == "conflict"
    assert "0-16" in excinfo.value.message
    assert len(fake.live.song["tracks"][0]["arrangement_clips"]) == 1


async def test_midi_clip_refused_on_audio_track(fake, session):
    with pytest.raises(ToolError) as excinfo:
        await api.create_arrangement_clip(session, track="Loops", time=0.0,
                                          length=4.0, name="x")
    assert excinfo.value.code == "invalid_argument"
    assert "import_audio_clip" in excinfo.value.hint


async def test_locate_arrangement_clip_by_time_and_index(fake, session):
    await api.create_arrangement_clip(session, track=0, time=0.0, length=4.0,
                                      name="first")
    await api.create_arrangement_clip(session, track=0, time=16.0, length=4.0,
                                      name="second")
    inside = await api.get_clip(session, clip={"track": 0, "time": 17.5})
    assert inside["name"] == "second"
    assert inside["arrangement"]["start"] == 16.0
    by_index = await api.get_clip(session, clip={"track": 0, "arrangement": 0})
    assert by_index["name"] == "first"
    with pytest.raises(ToolError) as excinfo:
        await api.get_clip(session, clip={"track": 0, "time": 100.0})
    assert excinfo.value.code == "not_found"
    assert "0-4" in excinfo.value.hint


async def test_set_and_delete_arrangement_clip(fake, session):
    await api.create_arrangement_clip(session, track=0, time=8.0, length=4.0,
                                      name="tmp")
    clip = {"track": 0, "time": 8.0}
    await api.set_arrangement_clip(session, clip=clip, name="kept",
                                   muted=True, start_marker=1.0)
    live = fake.live.song["tracks"][0]["arrangement_clips"][0]
    assert live["name"] == "kept" and live["muted"] is True
    assert live["start_marker"] == 1.0
    result = await api.delete_arrangement_clip(session, clip=clip)
    assert result["deleted"]["start"] == 8.0
    assert fake.live.song["tracks"][0]["arrangement_clips"] == []


async def test_arrangement_position_is_read_only(fake, session):
    await api.create_arrangement_clip(session, track=0, time=8.0, length=4.0,
                                      name="fixed")
    with pytest.raises(ToolError) as excinfo:
        await api.lom_set(session, path="song.tracks.0.arrangement_clips.0",
                          props={"start_time": 16.0})
    assert excinfo.value.code == "property_read_only"


async def test_notes_work_on_arrangement_clips(fake, session):
    created = await api.create_arrangement_clip(
        session, track=0, time=4.0, length=8.0, name="n", notes=NOTES)
    clip = {"track": 0, "time": 4.0}
    got = await api.get_notes(session, clip=clip)
    assert got["count"] == 2 and got["view"] == "arrangement"
    await api.edit_notes(session, clip=clip,
                         remove_ids=[created["added_note_ids"][0]])
    assert (await api.get_notes(session, clip=clip))["count"] == 1


async def test_delete_arrangement_clip_rejects_a_session_locator(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0, name="s")
    with pytest.raises(ToolError) as excinfo:
        await api.delete_arrangement_clip(session, clip={"track": 0, "slot": 0})
    assert "delete_clip" in excinfo.value.hint
