"""Smoke test of the MCP surface itself, over a real stdio transport.

Everything else tests `api.*` directly; this is the only test that exercises
what an MCP client actually sees — the server booting, the tool list, the
generated JSON schemas, and what a tool returns when Live is not there.

Needs no Ableton: the server points at a dead port, so every tool must
degrade into a structured `bridge_unreachable` error rather than hanging or
raising through the protocol.
"""

import os
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BOOT = "from alberton_mcp.server import main; main()"

# Params whose schema is deliberately untyped ({}), because they accept more
# than one shape (an index or a name, a number or a {"db": x} object). MCP
# clients stringify these — resolve.as_index() and the value coercions absorb
# that. Any NEW untyped param must be added here consciously, with the same
# coercion in place, or given a concrete type.
POLYMORPHIC = {
    ("automate_parameter", "device"), ("automate_parameter", "parameter"),
    ("automate_parameter", "points"),
    ("browse", "category"),
    ("clear_automation", "device"), ("clear_automation", "parameter"),
    ("create_arrangement_clip", "color"), ("create_arrangement_clip", "notes"),
    ("create_arrangement_clip", "signature_denominator"),
    ("create_arrangement_clip", "signature_numerator"),
    ("create_arrangement_clip", "track"),
    ("create_audio_track", "color"), ("create_audio_track", "name"),
    ("create_clip", "color"), ("create_clip", "notes"),
    ("create_clip", "signature_denominator"),
    ("create_clip", "signature_numerator"), ("create_clip", "track"),
    ("create_midi_track", "color"), ("create_midi_track", "name"),
    ("create_reference_clip", "accents"), ("create_reference_clip", "color"),
    ("create_reference_clip", "pulses"), ("create_reference_clip", "segments"),
    ("create_reference_clip", "track"),
    ("create_scene", "color"), ("create_scene", "name"),
    ("delete_scene", "scene"), ("delete_track", "track"),
    ("duplicate_track", "track"),
    ("edit_notes", "add"), ("edit_notes", "remove_ids"),
    ("edit_notes", "remove_region"), ("edit_notes", "update"),
    ("fire_scene", "scene"),
    ("get_notes", "from_pitch"), ("get_notes", "from_time"),
    ("get_notes", "pitch_span"), ("get_notes", "time_span"),
    ("get_track", "track"),
    ("import_audio_clip", "color"), ("import_audio_clip", "name"),
    ("import_audio_clip", "slot"), ("import_audio_clip", "time"),
    ("import_audio_clip", "track"),
    ("list_arrangement_clips", "track"),
    ("load_device", "track"),
    ("lom_call", "args"), ("lom_call", "kwargs"),
    ("refresh_browser_index", "category"),
    ("set_arrangement_clip", "color"), ("set_arrangement_clip", "end_marker"),
    ("set_arrangement_clip", "loop_end"), ("set_arrangement_clip", "loop_start"),
    ("set_arrangement_clip", "looping"), ("set_arrangement_clip", "muted"),
    ("set_arrangement_clip", "name"), ("set_arrangement_clip", "start_marker"),
    ("set_clip", "color"), ("set_clip", "loop_end"), ("set_clip", "loop_start"),
    ("set_clip", "looping"), ("set_clip", "name"),
    ("set_clip", "signature_denominator"),
    ("set_clip", "signature_numerator"),
    ("set_device_parameter", "device"), ("set_device_parameter", "parameter"),
    ("set_device_parameter", "track"), ("set_device_parameter", "value"),
    ("set_scene", "color"), ("set_scene", "name"), ("set_scene", "scene"),
    ("set_song", "groove_amount"), ("set_song", "metronome"),
    ("set_song", "root_note"), ("set_song", "scale_mode"),
    ("set_song", "scale_name"), ("set_song", "signature_denominator"),
    ("set_song", "signature_numerator"), ("set_song", "tempo"),
    ("set_track", "arm"), ("set_track", "color"), ("set_track", "mute"),
    ("set_track", "name"), ("set_track", "pan"), ("set_track", "sends"),
    ("set_track", "solo"), ("set_track", "track"), ("set_track", "volume"),
    ("stop_all_clips", "track"),
    ("transport", "action"), ("transport", "position"),
    ("watch", "props"),
}


@asynccontextmanager
async def mcp_client():
    """Not a fixture on purpose: anyio cancel scopes must be entered and left
    in the same task, and pytest-asyncio tears generator fixtures down in a
    different one."""
    env = dict(os.environ)
    env["ALBERTON_PORT"] = "1"          # nothing listens there, ever
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src",
         env.get("PYTHONPATH", "")])
    params = StdioServerParameters(command=sys.executable, args=["-c", BOOT],
                                   env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def with_client(fn):
    """Give the test a live MCP client, opened and closed in its own task.

    Deliberately not functools.wraps: that leaves __wrapped__ behind, pytest
    follows it to the original signature, and then hunts for a `client`
    fixture that does not exist.
    """
    async def wrapper():
        async with mcp_client() as client:
            return await fn(client)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@with_client
async def test_server_boots_and_lists_its_tools(client):
    listed = await client.list_tools()
    names = [tool.name for tool in listed.tools]
    assert len(names) == len(set(names)), "duplicate tool names"
    assert len(names) >= 45, "expected the full catalogue, got %d" % len(names)
    for essential in ("session_overview", "create_clip", "edit_notes",
                      "song_batch", "automate_parameter",
                      "create_arrangement_clip", "import_audio_clip",
                      "lom_call", "watch"):
        assert essential in names, "%s is missing from the catalogue" % essential


@with_client
async def test_every_tool_is_documented(client):
    listed = await client.list_tools()
    for tool in listed.tools:
        assert tool.description, "%s has no description" % tool.name
        assert len(tool.description) > 40, \
            "%s's description is too thin to guide a model" % tool.name


@with_client
async def test_schemas_are_well_formed(client):
    listed = await client.list_tools()
    for tool in listed.tools:
        schema = tool.inputSchema
        assert schema.get("type") == "object", "%s: bad schema type" % tool.name
        assert isinstance(schema.get("properties"), dict), \
            "%s: no properties" % tool.name
        for required in schema.get("required", []):
            assert required in schema["properties"], \
                "%s: required param %r is not declared" % (tool.name, required)


@with_client
async def test_no_new_untyped_params_appear(client):
    """Untyped params get stringified by MCP clients (that is how track=0
    arrived as "0"). Each one must be a conscious choice with coercion behind
    it, so this test fails when a new one shows up unannounced."""
    listed = await client.list_tools()
    untyped = set()
    for tool in listed.tools:
        for param, spec in tool.inputSchema.get("properties", {}).items():
            if not spec or not any(key in spec for key in
                                   ("type", "anyOf", "oneOf", "allOf", "$ref",
                                    "enum")):
                untyped.add((tool.name, param))
    surprises = sorted(untyped - POLYMORPHIC)
    assert not surprises, (
        "new untyped params — give them a type, or add them to POLYMORPHIC "
        "once coercion handles them: %s" % surprises)


@with_client
async def test_tools_degrade_cleanly_without_live(client):
    """With no bridge, a tool must answer with a structured error naming the
    fix — never hang, never raise through the protocol."""
    for name, args in (("session_overview", {}),
                       ("get_track", {"track": 0}),
                       ("create_clip", {"track": 0, "slot": 0, "length": 4.0,
                                        "name": "x"}),
                       ("song_batch", {"calls": [{"tool": "set_song",
                                                  "params": {"tempo": 120}}]})):
        result = await client.call_tool(name, args)
        assert not result.isError, "%s raised through the protocol" % name
        text = result.content[0].text
        assert '"bridge_unreachable"' in text, \
            "%s did not report the bridge is down: %s" % (name, text[:200])
        assert "Alberton MCP" in text and "17853" in text, \
            "%s's error does not say how to fix it" % name


@with_client
async def test_argument_validation_happens_before_the_bridge(client):
    """Bad arguments must be refused on their own merits, not masked by the
    bridge being down."""
    result = await client.call_tool("import_audio_clip",
                                    {"track": 0, "file_path": "relative.wav",
                                     "time": 0.0})
    text = result.content[0].text
    assert "bridge_unreachable" not in text
    assert '"not_found"' in text or '"invalid_argument"' in text

    result = await client.call_tool("automate_parameter",
                                    {"clip": {"track": 0, "slot": 0},
                                     "device": 0, "parameter": 0,
                                     "points": [], "mode": "sideways"})
    assert '"invalid_argument"' in result.content[0].text
