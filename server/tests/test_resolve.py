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


async def test_clip_locator_requires_clip(session):
    with pytest.raises(ToolError) as excinfo:
        await resolve.resolve_clip(session.bridge, {"track": 0, "slot": 0})
    assert excinfo.value.code == "not_found"
    assert "create_clip" in excinfo.value.hint
