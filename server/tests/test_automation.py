import pytest

from alberton_mcp import api
from alberton_mcp.errors import ToolError

NOTES = [{"pitch": 60, "start": 0.0, "duration": 0.5}]


async def _clip_with_device(session, fake):
    await api.create_clip(session, track=0, slot=0, length=16.0, name="C",
                          notes=NOTES)
    found = await api.browse(session, query="fakesynth")
    await api.load_device(session, track=0, uri=found["matches"][0]["uri"])
    # give the fake device a sweepable macro alongside "Device On"
    device = fake.live.song["tracks"][0]["devices"][0]
    device["parameters"].append(
        {"__class__": "DeviceParameter", "name": "Sweep", "value": 47.0,
         "min": 0.0, "max": 127.0, "display_value": 835.0, "_live_ptr": 7777})
    return {"track": 0, "slot": 0}


async def test_ramp_renders_and_reads_back(fake, session):
    clip = await _clip_with_device(session, fake)
    result = await api.automate_parameter(
        session, clip=clip, device="FakeSynth", parameter="Sweep",
        points=[{"time": 0, "value": 30}, {"time": 8, "value": 110}],
        resolution=1.0)
    assert result["parameter"] == "Sweep"
    assert result["steps"] == 9  # 8 interpolated + the final breakpoint
    # probes land at step midpoints, so read-back matches what was written
    assert all(p["value"] == p["wrote"] for p in result["read_back"])
    assert result["read_back"][0]["value"] == 30.0
    envelope = fake.live.song["tracks"][0]["clip_slots"][0]["clip"][
        "automation_envelopes"][0]
    assert envelope["parameter"]["name"] == "Sweep"
    values = [round(v, 2) for _t, _s, v in envelope["steps"]]
    assert values[0] == 30.0 and values[-1] == 110.0
    assert values == sorted(values)  # monotonic ramp


async def test_hold_mode_is_stepped(fake, session):
    clip = await _clip_with_device(session, fake)
    await api.automate_parameter(
        session, clip=clip, device="FakeSynth", parameter="Sweep",
        points=[{"time": 0, "value": 20}, {"time": 4, "value": 90}],
        mode="hold")
    envelope = fake.live.song["tracks"][0]["clip_slots"][0]["clip"][
        "automation_envelopes"][0]
    assert [(t, v) for t, _s, v in envelope["steps"]] == [(0.0, 20.0),
                                                          (4.0, 90.0)]


async def test_values_are_clamped_to_the_parameter_range(fake, session):
    clip = await _clip_with_device(session, fake)
    await api.automate_parameter(
        session, clip=clip, device="FakeSynth", parameter="Sweep",
        points=[{"time": 0, "value": -50}, {"time": 4, "value": 900}],
        resolution=2.0)
    envelope = fake.live.song["tracks"][0]["clip_slots"][0]["clip"][
        "automation_envelopes"][0]
    values = [v for _t, _s, v in envelope["steps"]]
    assert min(values) >= 0.0 and max(values) <= 127.0


async def test_too_many_steps_is_refused_with_a_usable_hint(fake, session):
    clip = await _clip_with_device(session, fake)
    with pytest.raises(ToolError) as excinfo:
        await api.automate_parameter(
            session, clip=clip, device="FakeSynth", parameter="Sweep",
            points=[{"time": 0, "value": 0}, {"time": 400, "value": 127}],
            resolution=0.25)
    assert excinfo.value.code == "too_large"
    assert "resolution" in excinfo.value.hint
    envelope = fake.live.song["tracks"][0]["clip_slots"][0]["clip"][
        "automation_envelopes"]
    assert not envelope  # refused before creating anything


async def test_envelope_matched_by_identity_not_name(fake, session):
    clip = await _clip_with_device(session, fake)
    device = fake.live.song["tracks"][0]["devices"][0]
    # a second parameter with the SAME name, as two racks on a track can have
    device["parameters"].append(
        {"__class__": "DeviceParameter", "name": "Sweep", "value": 0.0,
         "min": 0.0, "max": 127.0, "display_value": 0.0, "_live_ptr": 8888})
    # index 3 and index 2 share the name "Sweep": only _live_ptr tells them apart
    await api.automate_parameter(
        session, clip=clip, device="FakeSynth", parameter=3,
        points=[{"time": 0, "value": 10}, {"time": 4, "value": 20}],
        resolution=2.0)
    await api.automate_parameter(
        session, clip=clip, device="FakeSynth", parameter=2,
        points=[{"time": 0, "value": 100}, {"time": 4, "value": 120}],
        resolution=2.0)
    envelopes = fake.live.song["tracks"][0]["clip_slots"][0]["clip"][
        "automation_envelopes"]
    assert len(envelopes) == 2
    by_ptr = {e["parameter"]["_live_ptr"]: e["steps"] for e in envelopes}
    assert by_ptr[8888][0][2] == 10.0   # the second one kept its own shape
    assert by_ptr[7777][0][2] == 100.0


async def test_rewriting_an_existing_envelope(fake, session):
    """Live's create_automation_envelope raises if one already exists, so the
    tool must find-then-create, and a second call must just reshape."""
    clip = await _clip_with_device(session, fake)
    for value in (40, 90):
        await api.automate_parameter(
            session, clip=clip, device="FakeSynth", parameter="Sweep",
            points=[{"time": 0, "value": value}, {"time": 4, "value": value}],
            resolution=2.0)
    envelopes = fake.live.song["tracks"][0]["clip_slots"][0]["clip"][
        "automation_envelopes"]
    assert len(envelopes) == 1  # reshaped, not duplicated
    assert envelopes[0]["steps"][0][2] == 90.0


async def test_clear_automation(fake, session):
    clip = await _clip_with_device(session, fake)
    await api.automate_parameter(
        session, clip=clip, device="FakeSynth", parameter="Sweep",
        points=[{"time": 0, "value": 30}, {"time": 4, "value": 90}],
        resolution=2.0)
    await api.clear_automation(session, clip=clip)
    live_clip = fake.live.song["tracks"][0]["clip_slots"][0]["clip"]
    assert live_clip["automation_envelopes"] == []
    assert live_clip["has_envelopes"] is False


async def test_lom_call_is_batchable(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0, name="C")
    fake.op_log.clear()
    result = await api.song_batch(session, calls=[
        {"tool": "lom_set", "params": {"path": "song", "props": {"tempo": 96.0}}},
        {"tool": "lom_call", "params": {"path": "song.tracks.0.clip_slots.0",
                                        "method": "fire"}},
    ])
    assert [c["ok"] for c in result["calls"]] == [True, True]
    assert fake.live.song["tempo"] == 96.0
    wire_batches = [f for op, f in fake.op_log if op == "batch"
                    and any(s["op"] in ("set", "call") for s in f["ops"])]
    assert len(wire_batches) == 1  # one undo step
