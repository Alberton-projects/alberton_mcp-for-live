import asyncio

import pytest

from alberton_mcp import api
from alberton_mcp.errors import ToolError


def batches_containing(fake, op_name):
    """All wire batches whose sub-ops include the given op."""
    return [frame for op, frame in fake.op_log
            if op == "batch" and any(sub["op"] == op_name
                                     for sub in frame["ops"])]


NOTES = [
    {"pitch": 60, "start": 0.0, "duration": 0.5},
    {"pitch": 64, "start": 1.0 / 3.0, "duration": 1.0 / 3.0},
    {"pitch": 67, "start": 2.0 / 3.0, "duration": 1.0 / 3.0,
     "probability": 0.5},
]


async def test_create_clip_is_one_wire_batch(fake, session):
    result = await api.create_clip(session, track="Lead", slot=0, length=4.0,
                                   name="Motif A", color="#FF5533",
                                   notes=NOTES)
    assert len(result["added_note_ids"]) == 3
    assert result["clip"]["name"] == "Motif A"
    assert result["clip"]["color"] == "#FF5533"
    creating = batches_containing(fake, "call")
    assert len(creating) == 1
    ops = [sub["op"] for sub in creating[0]["ops"]]
    assert ops == ["call", "set", "edit_notes"]  # one batch, one undo step
    clip = fake.live.song["tracks"][0]["clip_slots"][0]["clip"]
    assert clip["color"] == 0xFF5533
    assert len(clip["notes"]) == 3
    assert clip["notes"][1]["start"] == pytest.approx(1.0 / 3.0)


async def test_create_clip_conflict_on_occupied_slot(fake, session):
    await api.create_clip(session, track=0, slot=1, length=4.0, name="X")
    with pytest.raises(ToolError) as excinfo:
        await api.create_clip(session, track=0, slot=1, length=4.0, name="Y")
    assert excinfo.value.code == "conflict"
    assert "delete_clip" in excinfo.value.hint


async def test_edit_notes_update_and_remove(fake, session):
    created = await api.create_clip(session, track=0, slot=0, length=4.0,
                                    name="N", notes=NOTES)
    ids = created["added_note_ids"]
    result = await api.edit_notes(session, clip={"track": 0, "slot": 0},
                                  update=[{"id": ids[0], "velocity": 45}],
                                  remove_ids=[ids[1]])
    assert result["counts"] == {"added": 0, "updated": 1, "removed": 1}
    notes = fake.live.song["tracks"][0]["clip_slots"][0]["clip"]["notes"]
    assert len(notes) == 2
    assert notes[0]["velocity"] == 45


async def test_set_track_color_volume_db(fake, session):
    result = await api.set_track(session, track="Bass", color="#00FF00",
                                 volume={"db": -6.0}, pan=0.25)
    track = fake.live.song["tracks"][1]
    assert track["color"] == 0x00FF00
    # display_value is numeric display units (dB) — mirrors Live 12.4.3
    assert track["mixer_device"]["volume"]["display_value"] == -6.0
    assert track["mixer_device"]["panning"]["value"] == 0.25
    assert result["values"]["color"] == "#00FF00"
    assert result["values"]["volume.display_value"] == -6.0


async def test_lom_set_guard_blocks_before_wire(fake, session):
    with pytest.raises(ToolError) as excinfo:
        await api.lom_set(session, path="app",
                          props={"average_process_usage": 0.1})
    assert excinfo.value.code == "property_read_only"
    assert not [f for op, f in fake.op_log if op == "set"]  # never sent


async def test_quantize_grid_mapping(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0, name="Q",
                          notes=NOTES)
    await api.quantize_clip(session, clip={"track": 0, "slot": 0}, grid=0.25,
                            amount=0.8)
    clip = fake.live.song["tracks"][0]["clip_slots"][0]["clip"]
    enum_value, amount = clip["quantize_calls"][0]
    assert isinstance(enum_value, int)  # resolved from the real inventory
    assert amount == 0.8
    with pytest.raises(ToolError) as excinfo:
        await api.quantize_clip(session, clip={"track": 0, "slot": 0},
                                grid=0.7)
    assert excinfo.value.code == "invalid_argument"
    assert "0.25" in excinfo.value.hint


async def test_song_batch_single_wire_batch(fake, session):
    result = await api.song_batch(session, calls=[
        {"tool": "set_song", "params": {"tempo": 99.0}},
        {"tool": "create_clip", "params": {"track": 0, "slot": 2,
                                           "length": 8.0, "name": "B",
                                           "notes": NOTES}},
        {"tool": "set_track", "params": {"track": "Bass", "mute": True}},
    ])
    assert result["rolled_back"] is False
    assert [c["ok"] for c in result["calls"]] == [True, True, True]
    wire_batches = [f for op, f in fake.op_log if op == "batch"
                    and any(s["op"] in ("set", "call", "edit_notes")
                            for s in f["ops"])]
    assert len(wire_batches) == 1  # everything ran as ONE undo step
    assert fake.live.song["tempo"] == 99.0
    assert fake.live.song["tracks"][1]["mute"] is True


async def test_song_batch_rolls_back_atomically(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0, name="N",
                          notes=NOTES)
    fake.op_log.clear()
    result = await api.song_batch(session, calls=[
        {"tool": "set_song", "params": {"tempo": 150.0}},
        {"tool": "edit_notes", "params": {"clip": {"track": 0, "slot": 0},
                                          "update": [{"id": 999999,
                                                      "velocity": 1}]}},
    ])
    assert result["rolled_back"] is True
    assert result["undo_hint"] == "Undo Fake Batch"
    assert result["calls"][0]["ok"] is True
    assert result["calls"][1]["ok"] is False
    assert fake.live.song["tempo"] == 120.0  # the tempo change was undone


async def test_song_batch_compile_error_executes_nothing(fake, session):
    fake.op_log.clear()
    with pytest.raises(ToolError) as excinfo:
        await api.song_batch(session, calls=[
            {"tool": "set_song", "params": {"tempo": 140.0}},
            {"tool": "set_clip", "params": {"clip": {"track": 0, "slot": 3},
                                            "name": "missing"}},
        ])
    assert "nothing was executed" in excinfo.value.hint
    assert "create_clip first" in excinfo.value.hint  # inner hint survives
    assert fake.live.song["tempo"] == 120.0
    mutating = [f for op, f in fake.op_log if op in ("set", "call",
                                                     "edit_notes", "batch")
                and op != "batch" or
                (op == "batch" and any(s["op"] != "get" for s in f["ops"]))]
    assert not mutating


async def test_watch_and_get_changes(fake, session):
    watched = await api.watch(session, path="song", props=["tempo"])
    assert watched["current_values"]["tempo"] == 120.0
    await api.set_song(session, tempo=87.5)
    await asyncio.sleep(0.05)
    changes = await api.get_changes(session, since=0)
    tempo_events = [e for e in changes["events"]
                    if e["kind"] == "change" and e.get("prop") == "tempo"]
    assert tempo_events and tempo_events[-1]["value"] == 87.5
    await api.unwatch(session, watch_id=watched["watch_id"])
    assert not session.watches


async def test_session_overview_shape(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0,
                          name="Motif", color="#112233", notes=NOTES)
    overview = await api.session_overview(session)
    assert overview["tempo"] == 120.0
    assert overview["signature"] == "4/4"
    assert overview["counts"] == {"tracks": 3, "scenes": 4, "returns": 2}
    lead = overview["tracks"][0]
    assert lead["name"] == "Lead"
    assert lead["type"] == "midi"
    assert lead["clips"]["0"]["name"] == "Motif"
    assert lead["clips"]["0"]["color"] == "#112233"


async def test_browse_and_load_device(fake, session):
    found = await api.browse(session, query="fakesynth")
    assert found["matches"]
    uri = found["matches"][0]["uri"]
    assert uri == "query:FakeSynth"
    result = await api.load_device(session, track="Lead", uri=uri)
    assert result["device_count_change"] == 1
    assert result["devices_now"] == ["FakeSynth"]
    assert fake.live.song["tracks"][0]["devices"][0]["name"] == "FakeSynth"


async def test_reference_clip_lanes_and_labels(fake, session):
    result = await api.create_reference_clip(
        session, track=0, slot=3, length=16.0, name="Form",
        segments=[{"start": 0, "label": "A"}, {"start": 8, "label": "B"}],
        pulses=[0, 4, 8, 12], accents=[0, 8])
    clip = fake.live.song["tracks"][0]["clip_slots"][3]["clip"]
    assert clip["name"] == "Form [0:A 8:B]"
    pitches = sorted(set(n["pitch"] for n in clip["notes"]))
    assert pitches == [36, 37, 38]
    segment_notes = [n for n in clip["notes"] if n["pitch"] == 36]
    assert segment_notes[0]["duration"] == 8.0  # runs until the next segment


async def test_transport_and_arrangement(fake, session):
    await api.transport(session, action="play", position=32.0)
    assert fake.live.song["is_playing"] is True
    assert fake.live.song["current_song_time"] == 32.0
    await api.create_clip(session, track=0, slot=0, length=4.0, name="N")
    placed = await api.duplicate_clip_to_arrangement(
        session, clip={"track": 0, "slot": 0}, time=16.0)
    assert placed["placed_at"] == 16.0
    listed = await api.list_arrangement_clips(session)
    assert listed["count"] == 1
    assert listed["clips"][0]["start"] == 16.0
    view = await api.show_view(session, view="arrangement")
    assert view["view"] == "arrangement"
    assert fake.live.view_log == ["Arranger"]
