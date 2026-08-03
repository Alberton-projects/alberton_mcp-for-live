import pytest

from alberton_mcp import resolve
from alberton_mcp.errors import ToolError


async def test_track_by_index(session):
    ref = await resolve.resolve_track(session.bridge, 1)
    assert ref == {"index": 1, "path": "song.tracks.1"}


async def test_track_by_name(session):
    ref = await resolve.resolve_track(session.bridge, "Bass")
    assert ref["index"] == 1


async def test_track_index_out_of_range(session):
    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_track(session.bridge, 17)
    assert excinfo.value.code == "not_found"
    assert "0–2" in excinfo.value.hint


async def test_track_name_missing_lists_candidates(session):
    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_track(session.bridge, "Nope")
    assert excinfo.value.code == "not_found"
    assert "'Bass'" in excinfo.value.hint


async def test_track_name_ambiguous(fake, session):
    fake.live.song["tracks"][2]["name"] = "Bass"
    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_track(session.bridge, "Bass")
    assert excinfo.value.code == "ambiguous_name"
    assert "[1, 2]" in excinfo.value.hint


async def test_digit_string_falls_back_to_index(session):
    # MCP clients stringify untyped params: track=0 can arrive as "0".
    ref = await resolve.resolve_track(session.bridge, "1")
    assert ref["index"] == 1


async def test_a_track_actually_named_like_a_number_wins(fake, session):
    fake.live.song["tracks"][2]["name"] = "1"
    ref = await resolve.resolve_track(session.bridge, "1")
    assert ref["index"] == 2  # the name beats the index reading


async def test_digit_string_slot(fake, session):
    ref = await resolve.resolve_slot(session.bridge, "Lead", "2")
    assert ref["slot"] == 2


async def test_clip_locator_requires_clip(session):
    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_clip(session.bridge, {"track": 0, "slot": 0})
    assert excinfo.value.code == "not_found"
    assert "create_clip" in excinfo.value.hint
