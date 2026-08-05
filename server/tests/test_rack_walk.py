"""get_track sees inside racks, and says when a device can hide parameters.

Two of the review's open items (2026-08-05). Chasing a fault down a real
chain — sequencer, [PITCH], receiver, plugin, some inside racks — used to
mean hand-rolling lom_get walks over `…devices.N.chains.M.devices.K`; and a
Max for Live device's blob parameters are invisible to the LOM with nothing
marking their absence, so a complete-looking answer could be missing nine of
twenty-four parameters.
"""
import pytest

from alberton_mcp import api
from fake_bridge import _param


def _rack(name, chains, class_name="AudioEffectGroupDevice"):
    return {"__class__": "RackDevice", "name": name, "class_name": class_name,
            "can_have_chains": True, "parameters": [_param("Macro 1", 0.5)],
            "chains": chains}


def _chain(name, devices):
    return {"__class__": "Chain", "name": name, "devices": devices}


def _device(name, params, class_name="PluginDevice"):
    return {"__class__": "PluginDevice", "name": name,
            "class_name": class_name, "parameters": params, "chains": []}


def _nested_track_devices():
    """A rack holding a M4L device and an inner rack with an Operator."""
    inner = _rack("Inner Rack", [
        _chain("Deep", [_device("Operator", [_param("Volume", 0.8)])])],
        class_name="InstrumentGroupDevice")
    return [_rack("Outer", [
        _chain("Chain A", [
            _device("Step Seq", [_param("Rate", 1.0)],
                    class_name="MxDeviceMidiEffect"),
            inner])])]


async def test_standard_detail_walks_the_rack(fake, session):
    fake.live.song["tracks"][0]["devices"] = _nested_track_devices()
    out = await api.get_track(session, track=0)
    outer = out["devices"][0]
    assert outer["rack"] is True
    chain = outer["chains"][0]
    assert chain["name"] == "Chain A"
    step, inner = chain["devices"]
    assert (step["name"], step["locator"]) == ("Step Seq", "0/0/0")
    assert step["max_for_live"] is True
    assert (inner["name"], inner["locator"]) == ("Inner Rack", "0/0/1")
    deep = inner["chains"][0]["devices"][0]
    assert (deep["name"], deep["locator"]) == ("Operator", "0/0/1/0/0")
    # standard stays cheap: structure and counts, no parameter dumps
    assert "parameters" not in step and step["parameter_count"] == 1


async def test_full_detail_reaches_nested_parameters(fake, session):
    fake.live.song["tracks"][0]["devices"] = _nested_track_devices()
    out = await api.get_track(session, track=0, detail="full")
    deep = out["devices"][0]["chains"][0]["devices"][1]["chains"][0]["devices"][0]
    volume = deep["parameters"][0]
    assert (volume["name"], volume["value"]) == ("Volume", 0.8)
    assert (volume["min"], volume["max"]) == (0.0, 1.0)


async def test_max_for_live_is_flagged_and_noted(fake, session):
    fake.live.song["tracks"][0]["devices"] = _nested_track_devices()
    out = await api.get_track(session, track=0)
    assert "blob" in out["max_for_live_note"] or \
        "invisible" in out["max_for_live_note"]

    fake.live.song["tracks"][1]["devices"] = [_device("Plain", [_param("A")])]
    out = await api.get_track(session, track=1)
    assert "max_for_live_note" not in out, "no M4L, no warning"


async def test_the_param_budget_counts_nested_devices(fake, session):
    monster = [_param("P%d" % i) for i in range(api.PARAM_DETAIL_LIMIT + 1)]
    fake.live.song["tracks"][0]["devices"] = [_rack("Outer", [
        _chain("A", [_device("Monster", monster)])])]
    out = await api.get_track(session, track=0, detail="full")
    nested = out["devices"][0]["chains"][0]["devices"][0]
    assert nested["parameters"][:2] == ["P0", "P1"], \
        "over the budget the nested device falls back to names too"
    assert "parameters_note" in nested


async def test_the_emitted_locator_round_trips(fake, session):
    fake.live.song["tracks"][0]["devices"] = _nested_track_devices()
    out = await api.get_track(session, track=0)
    locator = out["devices"][0]["chains"][0]["devices"][0]["locator"]
    result = await api.set_device_parameter(session, track=0, device=locator,
                                            parameter="Rate", value=0.25)
    assert result["parameter"]["value"] == 0.25


async def test_no_racks_means_no_extra_round_trips(fake, session):
    fake.live.song["tracks"][0]["devices"] = [_device("Plain", [_param("A")])]
    before = len([1 for op, _ in fake.op_log if op == "batch"])
    await api.get_track(session, track=0)
    after = len([1 for op, _ in fake.op_log if op == "batch"])
    assert after - before == 2, "describe batch + one read batch, as before"


async def test_a_monster_walk_is_truncated_and_says_so(fake, session, monkeypatch):
    monkeypatch.setattr(api, "RACK_WALK_LIMIT", 2)
    fake.live.song["tracks"][0]["devices"] = [_rack("Outer", [
        _chain("A", [_device("D%d" % i, [_param("P")]) for i in range(4)])])]
    out = await api.get_track(session, track=0)
    listed = out["devices"][0]["chains"][0]["devices"]
    assert len(listed) < 4
    assert "stopped" in out["devices_note"]
