"""Alberton MCP for Live — the MCP server (CONTRACT Layer B).

Conventions the model relies on (repeated in tool docstrings where they bite):
- All times and durations are absolute beats as floats. Clip-note times are
  beats from the clip start; transport/Arrangement times are song beats.
- Colors are '#RRGGBB'.
- A clip locator is {"track": <index|exact name>, "slot": <scene index>}.
- Every mutating tool is exactly one undo step in Live and atomic-or-absent;
  song_batch merges several tools into ONE undo step.
- Failures come back as {"error": {code, message, hint?}} — never prose.
"""

from typing import Optional, Union

from mcp.server.fastmcp import FastMCP

from . import api
from .bridge import Bridge, BridgeUnreachable, WireError
from .errors import ToolError

mcp = FastMCP("alberton")

_session: Optional[api.Session] = None


def _get_session() -> api.Session:
    global _session
    if _session is None:
        _session = api.Session(Bridge())
    return _session


async def _run(fn, **kwargs):
    try:
        return await fn(_get_session(), **kwargs)
    except ToolError as exc:
        return exc.to_dict()
    except WireError as exc:
        error = {"code": exc.code, "message": exc.message}
        for key in ("path", "prop", "method"):
            value = getattr(exc, key)
            if value:
                error[key] = value
        return {"error": error}
    except BridgeUnreachable as exc:
        return {"error": {
            "code": "bridge_unreachable",
            "message": str(exc),
            "hint": "Is Ableton Live open with the 'Alberton MCP' Control "
                    "Surface selected under Preferences > Link, Tempo & MIDI? "
                    "The bridge listens on 127.0.0.1:17853."}}


# --- orientation -----------------------------------------------------------------


@mcp.tool()
async def session_overview(detail: str = "standard") -> dict:
    """Map of the whole Live set: tempo, signature, scale, tracks with their
    clips and devices, scenes. detail: 'minimal' (names and counts only),
    'standard', or 'full' (adds returns and mixer displays). Start here."""
    return await _run(api.session_overview, detail=detail)


@mcp.tool()
async def get_track(track: Union[int, str], detail: str = "standard") -> dict:
    """One track in depth: mixer (volume/pan/sends with display strings),
    devices, clips. track = index or exact name. detail 'full' adds each
    device's parameter names."""
    return await _run(api.get_track, track=track, detail=detail)


@mcp.tool()
async def get_clip(clip: dict, include_notes: bool = False) -> dict:
    """Clip properties (name, color, length, loop, signature; audio extras for
    audio clips). clip = {"track": index|name, "slot": scene_index}.
    include_notes=true also returns all MIDI notes."""
    return await _run(api.get_clip, clip=clip, include_notes=include_notes)


@mcp.tool()
async def get_notes(clip: dict, from_time: Optional[float] = None,
                    time_span: Optional[float] = None,
                    from_pitch: Optional[int] = None,
                    pitch_span: Optional[int] = None) -> dict:
    """Notes of a MIDI clip, optionally windowed. Times are beats from the
    clip start; every note carries its stable id (use it with edit_notes)."""
    return await _run(api.get_notes, clip=clip, from_time=from_time,
                      time_span=time_span, from_pitch=from_pitch,
                      pitch_span=pitch_span)


@mcp.tool()
async def get_changes(since: int = 0) -> dict:
    """Pull the change feed accumulated by watch(): events with increasing
    seq. Pass the last seq you saw to get only what is new."""
    return await _run(api.get_changes, since=since)


# --- LOM escape hatches ------------------------------------------------------------


@mcp.tool()
async def lom_describe(path: str) -> dict:
    """Advanced: one LOM object, one level — every property with its current
    value ($obj/$vec stubs for children). Paths look like
    'song.tracks.0.clip_slots.2.clip'. Roots: 'song', 'app'."""
    return await _run(api.lom_describe, path=path)


@mcp.tool()
async def lom_get(path: str, props: list) -> dict:
    """Advanced: read specific properties of the LOM object at path."""
    return await _run(api.lom_get, path=path, props=props)


@mcp.tool()
async def lom_set(path: str, props: dict) -> dict:
    """Advanced: write properties of the LOM object at path. Validated against
    the introspected inventory first; returns the values re-read after
    writing, so clamping is visible. One undo step."""
    return await _run(api.lom_set, path=path, props=props)


@mcp.tool()
async def lom_call(path: str, method: str, args: Optional[list] = None,
                   kwargs: Optional[dict] = None) -> dict:
    """Advanced: call a LOM method. Pass LOM objects as
    {"$obj": {"path": "..."}}. Signatures are in docs/lom-inventory.md.
    One undo step."""
    return await _run(api.lom_call, path=path, method=method, args=args,
                      kwargs=kwargs)


# --- song and transport --------------------------------------------------------------


@mcp.tool()
async def set_song(tempo: Optional[float] = None,
                   signature_numerator: Optional[int] = None,
                   signature_denominator: Optional[int] = None,
                   scale_name: Optional[str] = None,
                   root_note: Optional[int] = None,
                   scale_mode: Optional[bool] = None,
                   groove_amount: Optional[float] = None,
                   metronome: Optional[bool] = None) -> dict:
    """Song-level settings. tempo in BPM (Live stores it as float32 — expect
    e.g. 123.45 to read back as 123.44999...), root_note 0-11 (0=C)."""
    params = {k: v for k, v in dict(
        tempo=tempo, signature_numerator=signature_numerator,
        signature_denominator=signature_denominator, scale_name=scale_name,
        root_note=root_note, scale_mode=scale_mode,
        groove_amount=groove_amount, metronome=metronome).items()
        if v is not None}
    return await _run(api.set_song, **params)


@mcp.tool()
async def transport(action: Optional[str] = None,
                    position: Optional[float] = None) -> dict:
    """Transport control: action play|stop|continue, and/or position in song
    beats (sets the playhead). Both together = atomic."""
    return await _run(api.transport, action=action, position=position)


# --- tracks ------------------------------------------------------------------------------


@mcp.tool()
async def create_midi_track(name: Optional[str] = None,
                            color: Optional[str] = None,
                            index: int = -1) -> dict:
    """New MIDI track (index -1 = append at the end), named and colored in
    the same undo step."""
    return await _run(api.create_midi_track, name=name, color=color, index=index)


@mcp.tool()
async def create_audio_track(name: Optional[str] = None,
                             color: Optional[str] = None,
                             index: int = -1) -> dict:
    """New audio track (index -1 = append), named and colored atomically."""
    return await _run(api.create_audio_track, name=name, color=color,
                      index=index)


@mcp.tool()
async def set_track(track: Union[int, str], name: Optional[str] = None,
                    color: Optional[str] = None, arm: Optional[bool] = None,
                    mute: Optional[bool] = None, solo: Optional[bool] = None,
                    volume: Optional[Union[float, dict]] = None,
                    pan: Optional[float] = None,
                    sends: Optional[list] = None) -> dict:
    """Track properties and mixer in one atomic call. volume: 0..1 normalized
    or {"db": -6.0}. pan: -1..1. sends: [{"send": index|return-name,
    "value": 0..1} or {"send": ..., "db": -12}]. Colors snap to Live's
    palette — the read-back shows what Live kept."""
    params = {k: v for k, v in dict(
        name=name, color=color, arm=arm, mute=mute, solo=solo, volume=volume,
        pan=pan, sends=sends).items() if v is not None}
    return await _run(api.set_track, track=track, **params)


@mcp.tool()
async def delete_track(track: Union[int, str]) -> dict:
    """Delete a track (undoable in Live with one Cmd-Z)."""
    return await _run(api.delete_track, track=track)


@mcp.tool()
async def duplicate_track(track: Union[int, str]) -> dict:
    """Duplicate a track; the copy lands right after the original."""
    return await _run(api.duplicate_track, track=track)


# --- clips and notes -----------------------------------------------------------------------


@mcp.tool()
async def create_clip(track: Union[int, str], slot: int, length: float,
                      name: str, color: Optional[str] = None,
                      notes: Optional[list] = None,
                      signature_numerator: Optional[int] = None,
                      signature_denominator: Optional[int] = None,
                      loop: bool = True) -> dict:
    """Create a MIDI clip in a Session slot and (optionally) fill it — create,
    name, color, signature and notes are ONE undo step. length in beats.
    notes: [{"pitch": 0-127, "start": beats, "duration": beats,
    "velocity"?: 1-127, "mute"?, "probability"?: 0-1, "velocity_deviation"?,
    "release_velocity"?}]. Returns the new notes' stable ids."""
    return await _run(api.create_clip, track=track, slot=slot, length=length,
                      name=name, color=color, notes=notes,
                      signature_numerator=signature_numerator,
                      signature_denominator=signature_denominator, loop=loop)


@mcp.tool()
async def set_clip(clip: dict, name: Optional[str] = None,
                   color: Optional[str] = None,
                   looping: Optional[bool] = None,
                   loop_start: Optional[float] = None,
                   loop_end: Optional[float] = None,
                   signature_numerator: Optional[int] = None,
                   signature_denominator: Optional[int] = None) -> dict:
    """Clip properties (loop points in beats). Atomic; returns read-back."""
    params = {k: v for k, v in dict(
        name=name, color=color, looping=looping, loop_start=loop_start,
        loop_end=loop_end, signature_numerator=signature_numerator,
        signature_denominator=signature_denominator).items() if v is not None}
    return await _run(api.set_clip, clip=clip, **params)


@mcp.tool()
async def delete_clip(clip: dict) -> dict:
    """Remove the clip from its Session slot."""
    return await _run(api.delete_clip, clip=clip)


@mcp.tool()
async def duplicate_clip_to_slot(clip: dict, target: dict) -> dict:
    """Copy a Session clip to another (empty) slot.
    target = {"track": index|name, "slot": scene_index}."""
    return await _run(api.duplicate_clip_to_slot, clip=clip, target=target)


@mcp.tool()
async def edit_notes(clip: dict, add: Optional[list] = None,
                     update: Optional[list] = None,
                     remove_ids: Optional[list] = None,
                     remove_region: Optional[dict] = None) -> dict:
    """Surgical MIDI editing, one undo step, fixed order: remove_region ->
    remove_ids -> update -> add. update entries carry the note "id" plus the
    fields to change. remove_region = {"from_time", "time_span",
    "from_pitch"?, "pitch_span"?}. Times in beats from clip start; exact
    floats (1/3 for triplets) round-trip exactly. Returns added ids."""
    return await _run(api.edit_notes, clip=clip, add=add, update=update,
                      remove_ids=remove_ids, remove_region=remove_region)


@mcp.tool()
async def quantize_clip(clip: dict, grid: float, amount: float = 1.0) -> dict:
    """Quantize all notes of a MIDI clip. grid in beats: 1.0, 0.5, 0.3333,
    0.25, 0.1667 or 0.125. amount 0..1."""
    return await _run(api.quantize_clip, clip=clip, grid=grid, amount=amount)


# --- scenes ---------------------------------------------------------------------------------


@mcp.tool()
async def create_scene(index: int = -1, name: Optional[str] = None,
                       color: Optional[str] = None) -> dict:
    """New scene (index -1 = append), named/colored atomically."""
    return await _run(api.create_scene, index=index, name=name, color=color)


@mcp.tool()
async def set_scene(scene: Union[int, str], name: Optional[str] = None,
                    color: Optional[str] = None) -> dict:
    """Rename/recolor a scene (index or exact name)."""
    return await _run(api.set_scene, scene=scene, name=name, color=color)


@mcp.tool()
async def delete_scene(scene: Union[int, str]) -> dict:
    """Delete a scene."""
    return await _run(api.delete_scene, scene=scene)


@mcp.tool()
async def fire_scene(scene: Union[int, str]) -> dict:
    """Launch a scene (respects launch quantization)."""
    return await _run(api.fire_scene, scene=scene)


@mcp.tool()
async def fire_clip(clip: dict) -> dict:
    """Launch a Session clip."""
    return await _run(api.fire_clip, clip=clip)


@mcp.tool()
async def stop_clip(clip: dict) -> dict:
    """Stop the clip playing in this track (slot stop button)."""
    return await _run(api.stop_clip, clip=clip)


@mcp.tool()
async def stop_all_clips(track: Optional[Union[int, str]] = None) -> dict:
    """Stop all Session clips (of one track, or of the whole song)."""
    return await _run(api.stop_all_clips, track=track)


# --- devices and browser ---------------------------------------------------------------------


@mcp.tool()
async def browse(query: str, category: Optional[str] = None) -> dict:
    """Search Live's browser for loadable items (instruments, sounds, drums,
    audio_effects, midi_effects, plugins, samples, packs, user_library).
    Returns names + uris for load_device. First search per category walks the
    tree and may take a few seconds."""
    return await _run(api.browse, query=query, category=category)


@mcp.tool()
async def load_device(track: Union[int, str], uri: str) -> dict:
    """Load a browser item (instrument/effect/preset) onto a track by the uri
    that browse returned."""
    return await _run(api.load_device, track=track, uri=uri)


@mcp.tool()
async def set_device_parameter(track: Union[int, str],
                               device: Union[int, str],
                               parameter: Union[int, str],
                               value: Union[float, dict]) -> dict:
    """Set one device parameter. value: number in the parameter's [min, max]
    (see get_track detail='full'), or {"display": -6.0} to write in the
    parameter's DISPLAY units (dB, %, st — numeric, as shown in Live).
    Returns value + display read-back."""
    return await _run(api.set_device_parameter, track=track, device=device,
                      parameter=parameter, value=value)


# --- clip automation ---------------------------------------------------------------------------


@mcp.tool()
async def automate_parameter(clip: dict, device: Union[int, str],
                             parameter: Union[int, str], points: list,
                             resolution: float = 0.5,
                             mode: str = "ramp") -> dict:
    """Write clip automation for a device parameter from a few breakpoints.

    You describe the SHAPE, the server renders it: points is
    [{"time": beats, "value": n}, ...] in the parameter's own units (see
    get_track detail='full' for min/max). mode 'ramp' interpolates linearly
    between breakpoints and samples every `resolution` beats; mode 'hold'
    keeps each value until the next breakpoint. The device must be on the
    clip's own track. Values are clamped to the parameter's range. The shape
    is one undo step (creating the envelope the first time is a separate
    small one). Max 240 steps per call — raise `resolution` for long spans."""
    return await _run(api.automate_parameter, clip=clip, device=device,
                      parameter=parameter, points=points,
                      resolution=resolution, mode=mode)


@mcp.tool()
async def clear_automation(clip: dict, device: Optional[Union[int, str]] = None,
                           parameter: Optional[Union[int, str]] = None) -> dict:
    """Remove clip automation: one parameter (give device + parameter), or
    every envelope in the clip (give neither)."""
    return await _run(api.clear_automation, clip=clip, device=device,
                      parameter=parameter)


# --- watches --------------------------------------------------------------------------------


@mcp.tool()
async def watch(path: str, props: list) -> dict:
    """Subscribe to LOM property changes (e.g. path='song',
    props=['tempo','is_playing']). Live pushes; the server accumulates;
    you pull with get_changes. Returns watch_id + current values."""
    return await _run(api.watch, path=path, props=props)


@mcp.tool()
async def unwatch(watch_id: int) -> dict:
    """Cancel a watch created by watch()."""
    return await _run(api.unwatch, watch_id=watch_id)


# --- arrangement ----------------------------------------------------------------------------


@mcp.tool()
async def show_view(view: str) -> dict:
    """Bring 'session' or 'arrangement' view to front in Live."""
    return await _run(api.show_view, view=view)


@mcp.tool()
async def list_arrangement_clips(track: Optional[Union[int, str]] = None) -> dict:
    """Arrangement clips (all tracks or one), with start/end in song beats."""
    return await _run(api.list_arrangement_clips, track=track)


@mcp.tool()
async def duplicate_clip_to_arrangement(clip: dict, time: float) -> dict:
    """Copy a Session clip into the Arrangement at `time` (song beats)."""
    return await _run(api.duplicate_clip_to_arrangement, clip=clip, time=time)


# --- structure made visible -------------------------------------------------------------------


@mcp.tool()
async def create_reference_clip(track: Union[int, str], slot: int,
                                length: float, name: str,
                                color: Optional[str] = None,
                                segments: Optional[list] = None,
                                pulses: Optional[list] = None,
                                accents: Optional[list] = None) -> dict:
    """Render structure a human can SEE in Live: a MIDI clip with one lane per
    layer — segments (held notes, labels go into the clip name), pulses
    (ticks), accents (loud ticks). segments: [{"start": beats, "label"?,
    "duration"?}]; pulses/accents: [beats, ...]. Mute the track; it is
    visual."""
    return await _run(api.create_reference_clip, track=track, slot=slot,
                      length=length, name=name, color=color,
                      segments=segments, pulses=pulses, accents=accents)


# --- cross-tool atomicity ----------------------------------------------------------------------


@mcp.tool()
async def song_batch(calls: list, stop_on_error: bool = True) -> dict:
    """Run several tools as ONE undo step, atomic-or-absent. calls:
    [{"tool": name, "params": {...}}, ...]. Batchable: set_song, set_track,
    set_clip, set_scene, edit_notes, create_clip, create_scene,
    create_midi_track, create_audio_track, quantize_clip, fire_clip,
    fire_scene, stop_clip, transport, lom_set, lom_call. Locators resolve
    before execution:
    when creating tracks/scenes and then filling them in the same batch,
    pass explicit indices."""
    return await _run(api.song_batch, calls=calls, stop_on_error=stop_on_error)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
