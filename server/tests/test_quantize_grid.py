"""quantize_clip must accept the grids its own hint advertises.

Found 2026-08-05: the tolerance was 1e-6, and the documented four-decimal
forms 0.3333 / 0.1667 sit 3.3e-5 from the exact triplet values — so the tool
refused the very numbers its docstring and its error hint listed.
"""
import pytest

from alberton_mcp import api
from alberton_mcp.errors import ToolError


async def test_documented_grids_are_accepted(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0, name="q")
    for grid in (1.0, 0.5, 0.3333, 1.0 / 3.0, 0.25, 0.1667, 1.0 / 6.0, 0.125):
        out = await api.quantize_clip(session, clip={"track": 0, "slot": 0},
                                      grid=grid)
        assert out["quantized"] is True, grid


async def test_a_grid_between_the_known_ones_is_refused(fake, session):
    await api.create_clip(session, track=0, slot=0, length=4.0, name="q")
    with pytest.raises(ToolError) as caught:
        await api.quantize_clip(session, clip={"track": 0, "slot": 0}, grid=0.7)
    assert caught.value.code == "invalid_argument"
