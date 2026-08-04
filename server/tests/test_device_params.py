"""get_track(detail='full') must give a caller enough to set a parameter.

The docstring promised a range long before the code returned one; nothing
tested it, so nobody noticed until someone tried to drive a real device.
"""
from alberton_mcp import api

from fake_bridge import _param


def _device(name, params):
    return {"__class__": "PluginDevice", "name": name,
            "class_name": "PluginDevice", "parameters": params, "chains": []}


async def test_full_detail_carries_range_value_and_flags(fake, session):
    fake.live.song["tracks"][0]["devices"] = [_device("Synth", [
        _param("Device On", 1.0, 0.0, 1.0, display=1.0, quantized=True),
        _param("Cutoff", 47.0, 0.0, 127.0, display="1.20 kHz"),
        _param("Macro'd", 0.5, 0.0, 1.0, display=0.5, enabled=False),
    ])]

    out = await api.get_track(session, track=0, detail="full")
    params = out["devices"][0]["parameters"]
    assert [p["name"] for p in params] == ["Device On", "Cutoff", "Macro'd"]

    cutoff = params[1]
    assert (cutoff["value"], cutoff["min"], cutoff["max"]) == (47.0, 0.0, 127.0)
    # the reading in Live's own units, only when it says something the raw
    # value does not
    assert cutoff["display"] == "1.20 kHz"
    assert "quantized" not in cutoff and "enabled" not in cutoff

    assert params[0]["quantized"] is True
    assert params[2]["enabled"] is False, "a macro-mapped parameter must say so"


async def test_standard_detail_still_costs_nothing(fake, session):
    fake.live.song["tracks"][0]["devices"] = [
        _device("Synth", [_param("Cutoff", 47.0, 0.0, 127.0)])]
    out = await api.get_track(session, track=0)
    device = out["devices"][0]
    assert device["parameter_count"] == 1
    assert "parameters" not in device


async def test_a_wall_of_parameters_falls_back_to_names(fake, session):
    fake.live.song["tracks"][0]["devices"] = [_device(
        "Monster", [_param("P%d" % i, float(i), 0.0, 999.0)
                    for i in range(api.PARAM_DETAIL_LIMIT + 1)])]

    out = await api.get_track(session, track=0, detail="full")
    device = out["devices"][0]
    assert device["parameters"][:2] == ["P0", "P1"], "names, not dicts"
    assert "lom_get" in device["parameters_note"], "and it must say how to dig"


async def test_every_device_on_the_track_is_covered(fake, session):
    """The read used to loop device by device, one round trip each."""
    fake.live.song["tracks"][0]["devices"] = [
        _device("One", [_param("A", 1.0), _param("B", 2.0)]),
        _device("Two", [_param("C", 3.0)]),
    ]
    out = await api.get_track(session, track=0, detail="full")
    assert [[p["name"] for p in d["parameters"]] for d in out["devices"]] \
        == [["A", "B"], ["C"]]
    assert out["devices"][1]["parameters"][0]["value"] == 3.0
