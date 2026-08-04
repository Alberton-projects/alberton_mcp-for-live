"""NaN and infinity must never reach the wire.

JSON has no way to write them. Python does it anyway — bare `NaN`, bare
`Infinity` — and on 2026-08-04 a probe sent one to a real Live: the bridge's
main-thread pump stopped answering, every later call timed out, and Live had to
be force-quit. A model can produce these without trying (any division can), so
the guard sits at the one boundary every path crosses.
"""
import math

import pytest

from alberton_mcp import api
from alberton_mcp.bridge import WireError
from alberton_mcp.errors import ToolError

BAD = [("NaN", float("nan")),
       ("infinity", float("inf")),
       ("negative infinity", float("-inf"))]


@pytest.mark.parametrize("label,value", BAD, ids=[b[0] for b in BAD])
async def test_a_validated_parameter_refuses_it_by_name(fake, session, label, value):
    """Layer B catches it where there is a range check to catch it with."""
    sent = len(fake.op_log)
    with pytest.raises(ToolError) as caught:
        await api.set_song(session, tempo=value)
    assert caught.value.code == "invalid_argument"
    assert "finite" in caught.value.message, caught.value.message
    assert len(fake.op_log) == sent, "the frame must not go out at all"


@pytest.mark.parametrize("label,value", BAD, ids=[b[0] for b in BAD])
async def test_the_wire_refuses_it_where_nothing_else_does(fake, session,
                                                           label, value):
    """lom_set writes an arbitrary property: no range check stands in the way,
    so the guard at the wire is the only thing between it and Live."""
    with pytest.raises(WireError) as caught:
        await api.lom_set(session, path="song", props={"tempo": value})
    assert caught.value.code == "invalid_argument"
    assert "props.tempo" in caught.value.message, caught.value.message
    # the handshake and the inventory check are legitimate; the write is not
    assert not [f for op, f in fake.op_log if op == "set"], \
        "the set frame must never go out"


async def test_it_names_where_the_bad_number_is(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0, name="C",
                          notes=[{"pitch": 60, "start": 0.0, "duration": 1.0}])
    with pytest.raises((WireError, ToolError)) as caught:
        await api.edit_notes(session, clip={"track": 0, "slot": 0},
                             add=[{"pitch": 60, "start": math.inf,
                                   "duration": 1.0}])
    # deep inside a list of dicts, and it still says which field
    assert "start" in caught.value.message, caught.value.message


async def test_the_connection_survives_the_refusal(fake, session):
    with pytest.raises((WireError, ToolError)):
        await api.set_song(session, tempo=float("nan"))
    out = await api.session_overview(session, detail="minimal")
    assert isinstance(out["tempo"], float) and not math.isnan(out["tempo"])


async def test_a_real_number_still_goes_through(fake, session):
    await api.set_song(session, tempo=123.0)
    out = await api.session_overview(session, detail="minimal")
    assert abs(out["tempo"] - 123.0) < 1e-3


async def test_the_model_is_told_what_to_do_instead(fake, session, monkeypatch):
    """A wire error's hint used to be dropped between the bridge and the model."""
    from alberton_mcp import server

    monkeypatch.setattr(server, "_get_session", lambda: session)
    out = await server._run(api.set_song, tempo=float("nan"))
    assert out["error"]["code"] == "invalid_argument"
    assert "hint" in out["error"], "the half that says what to send instead"
    assert "JSON" in out["error"]["hint"]
