"""Layer B implementations (docs/CONTRACT.md).

Every tool here compiles onto the eleven Layer A wire ops. Mutating tools
compile to a SINGLE wire batch, so each tool is one undo step in Live and
atomic-or-absent; song_batch concatenates several tools' ops into one batch.

Times are absolute beats as floats everywhere. Colors are '#RRGGBB' at this
boundary and integer RGB on the wire.
"""

import asyncio
import time as _time

from . import colors, files, inventory, resolve
from .bridge import WireError
from .errors import ToolError

BATCH_CHUNK = 250          # wire batches are capped at 256 ops (CONTRACT A.9)
# Probing every Session slot of every track costs one wire op each: a
# 29-track, 180-scene set is 5 220 of them, and the clip map they produce is
# ~50 KB of JSON nobody asked for. Above this many slots, `standard` says so
# and leaves the map to get_track. Measured on a real set, 2026-08-03.
CLIP_MAP_LIMIT = 600
BROWSER_MAX_ITEMS = 4000   # per category
BROWSER_MAX_DEPTH = 8
BROWSE_RESULT_LIMIT = 25

BROWSER_CATEGORIES = {
    "instruments": "app.browser.instruments",
    "sounds": "app.browser.sounds",
    "drums": "app.browser.drums",
    "audio_effects": "app.browser.audio_effects",
    "midi_effects": "app.browser.midi_effects",
    "plugins": "app.browser.plugins",
    "samples": "app.browser.samples",
    "packs": "app.browser.packs",
    "user_library": "app.browser.user_library",
    "max_for_live": "app.browser.max_for_live",
}
DEFAULT_BROWSE_CATEGORIES = ("instruments", "sounds", "drums", "audio_effects",
                             "midi_effects", "plugins")

REFERENCE_PITCHES = {"segments": 36, "pulses": 37, "accents": 38}

_GRID_ENUMS = (
    (1.0, "rec_q_quarter"),
    (0.5, "rec_q_eight"),
    (1.0 / 3.0, "rec_q_eight_triplet"),
    (0.25, "rec_q_sixtenth"),
    (1.0 / 6.0, "rec_q_sixtenth_triplet"),
    (0.125, "rec_q_thirtysecond"),
)


class Session:
    """Server-side state: one bridge, the watch registry, the browser cache."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.watches = {}        # sub id -> {"path", "props"}
        self.browser_cache = {}  # category -> {"items": [...], "by_uri": {...}}
        self.seen_epoch = 0      # the bridge connection these watches belong to


# --- small helpers -----------------------------------------------------------


def _scalar(value, default=None):
    """Unwrap a describe/get slot: scalars pass, $-encoded values -> default."""
    if isinstance(value, dict):
        return default
    return value


def _vec_length(value):
    if isinstance(value, dict) and "$vec" in value:
        return value["$vec"]["len"]
    return 0


async def _gets(bridge, specs):
    """Chunked batch of get ops. specs: [(path, [props]) ...] -> aligned values
    dicts (None where that get failed).

    Chunks go out concurrently: a round trip costs about one bridge tick
    (~100 ms) no matter how much it carries, so issuing them in sequence makes
    latency, not work, the bottleneck. The script's per-tick budget still
    bounds how long Live's main thread spends on them.
    """
    if not specs:
        return []
    chunks = [specs[start:start + BATCH_CHUNK]
              for start in range(0, len(specs), BATCH_CHUNK)]
    results = await asyncio.gather(*[
        bridge.request("batch", stop_on_error=False,
                       ops=[{"op": "get", "path": path, "props": props}
                            for path, props in chunk])
        for chunk in chunks])
    out = []
    for result in results:
        for sub in result["results"]:
            out.append(sub["result"]["values"] if sub.get("ok") else None)
    return out


async def _run_atomic(bridge, ops, what):
    """One wire batch = one undo step. Raises ToolError on any sub-op failure,
    reporting the rollback state; returns the per-op results otherwise."""
    if not ops:
        return []
    if len(ops) > 256:
        raise ToolError("too_large",
                        "%s compiles to %d wire ops (max 256)" % (what, len(ops)),
                        hint="split the work into smaller calls")
    result = await bridge.request("batch", ops=ops)
    if any(not sub.get("ok", False) for sub in result["results"]
           if not sub.get("skipped")):
        first_error = next(sub["error"] for sub in result["results"]
                           if sub.get("ok") is False)
        rolled = result.get("rolled_back")
        hint = "Live rolled the whole operation back (%s)" % \
               result.get("undo_hint") if rolled else \
               "no mutation had happened yet, the set is untouched"
        raise ToolError(first_error.get("code", "internal"),
                        "%s failed: %s" % (what, first_error.get("message")),
                        hint=hint)
    return result["results"]


def _require_number(value, name, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("invalid_argument", "%s must be a number" % name)
    if lo is not None and value < lo or hi is not None and value > hi:
        raise ToolError("invalid_argument",
                        "%s=%r out of range [%s, %s]" % (name, value, lo, hi))
    return float(value)


def _validate_notes(notes, what):
    if not isinstance(notes, list):
        raise ToolError("invalid_argument", "%s must be a list of notes" % what)
    for index, note in enumerate(notes):
        if not isinstance(note, dict):
            raise ToolError("invalid_argument",
                            "%s[%d] is not an object" % (what, index))
        for field in ("pitch", "start", "duration"):
            if field not in note:
                raise ToolError("invalid_argument",
                                "%s[%d] is missing '%s'" % (what, index, field),
                                hint="a note needs at least pitch, start "
                                     "(beats), duration (beats)")
        pitch = note["pitch"]
        if isinstance(pitch, bool) or not isinstance(pitch, int) \
                or not 0 <= pitch <= 127:
            raise ToolError("invalid_argument",
                            "%s[%d].pitch must be an int 0-127" % (what, index))
        _require_number(note["start"], "%s[%d].start" % (what, index), lo=0)
        _require_number(note["duration"], "%s[%d].duration" % (what, index))
        if note["duration"] <= 0:
            raise ToolError("invalid_argument",
                            "%s[%d].duration must be > 0" % (what, index))
    return notes


# --- orientation ----------------------------------------------------------------


async def session_overview(session, detail="standard"):
    bridge = session.bridge
    song = await bridge.request("describe", path="song")
    p = song["props"]
    track_count = _vec_length(p.get("tracks"))
    scene_count = _vec_length(p.get("scenes"))
    return_count = _vec_length(p.get("return_tracks"))
    out = {
        "tempo": _scalar(p.get("tempo")),
        "signature": "%s/%s" % (_scalar(p.get("signature_numerator")),
                                _scalar(p.get("signature_denominator"))),
        "scale": {"name": _scalar(p.get("scale_name")),
                  "root_note": _scalar(p.get("root_note")),
                  "scale_mode_on": _scalar(p.get("scale_mode"))},
        "is_playing": _scalar(p.get("is_playing")),
        "position": _scalar(p.get("current_song_time")),
        "counts": {"tracks": track_count, "scenes": scene_count,
                   "returns": return_count},
    }
    # Only named scenes are worth carrying: a long set has hundreds of empty
    # ones, and "Scene 137" tells the reader nothing the index did not.
    scene_values = await _gets(bridge, [("song.scenes.%d" % i, ["name", "color"])
                                        for i in range(scene_count)])
    named = [{"index": i, "name": (v or {}).get("name"),
              "color": colors.to_hex((v or {}).get("color"))}
             for i, v in enumerate(scene_values) if (v or {}).get("name")]
    out["scenes"] = named
    if len(named) < scene_count:
        out["scenes_note"] = ("%d of %d scenes are unnamed and omitted"
                              % (scene_count - len(named), scene_count))
    track_props = ["name", "color", "has_midi_input", "has_audio_input",
                   "is_foldable", "mute", "solo", "can_be_armed",
                   "clip_slots", "devices", "playing_slot_index"]
    track_values = await _gets(bridge, [("song.tracks.%d" % i, track_props)
                                        for i in range(track_count)])
    slot_probes = sum(_vec_length((v or {}).get("clip_slots"))
                      for v in track_values)
    # `full` always pays; `standard` only when the set is small enough that
    # the map is worth its round trips and its bytes.
    want_clip_map = detail == "full" or (detail != "minimal"
                                         and slot_probes <= CLIP_MAP_LIMIT)
    tracks = []
    clip_lookups = []   # (track_entry, slot_index, path)
    device_lookups = []  # (track_entry, path)
    for i, values in enumerate(track_values):
        values = values or {}
        kind = "midi" if _scalar(values.get("has_midi_input")) else "audio"
        if _scalar(values.get("is_foldable")):
            kind = "group"
        entry = {"index": i, "name": _scalar(values.get("name")),
                 "color": colors.to_hex(_scalar(values.get("color"))),
                 "type": kind,
                 "mute": _scalar(values.get("mute")),
                 "solo": _scalar(values.get("solo")),
                 "devices": [], "clips": {}}
        playing = _scalar(values.get("playing_slot_index"))
        if isinstance(playing, int) and playing >= 0:
            entry["playing_slot"] = playing
        tracks.append(entry)
        if detail != "minimal":
            for d in range(_vec_length(values.get("devices"))):
                device_lookups.append((entry, "song.tracks.%d.devices.%d" % (i, d)))
        if want_clip_map:
            for s in range(_vec_length(values.get("clip_slots"))):
                clip_lookups.append((entry, s,
                                     "song.tracks.%d.clip_slots.%d" % (i, s)))
        else:
            entry.pop("clips", None)
    out["tracks"] = tracks
    if detail == "minimal":
        return out
    if not want_clip_map:
        out["clips_note"] = (
            "the Session clip map is omitted: %d slots would each cost a read "
            "(over the %d limit). Use get_track for one track's clips, or "
            "detail='full' to pay for all of them."
            % (slot_probes, CLIP_MAP_LIMIT))
    device_values = await _gets(bridge, [(path, ["name"])
                                         for _e, path in device_lookups])
    for (entry, _path), values in zip(device_lookups, device_values):
        entry["devices"].append((values or {}).get("name"))
    if not clip_lookups:
        return out
    slot_values = await _gets(bridge, [(path, ["has_clip"])
                                       for _e, _s, path in clip_lookups])
    with_clip = [(entry, s, path) for (entry, s, path), values
                 in zip(clip_lookups, slot_values)
                 if values and values.get("has_clip") is True]
    clip_values = await _gets(bridge, [(path + ".clip",
                                        ["name", "color", "length",
                                         "is_playing", "is_midi_clip"])
                                       for _e, _s, path in with_clip])
    for (entry, s, _path), values in zip(with_clip, clip_values):
        values = values or {}
        entry["clips"][str(s)] = {
            "name": _scalar(values.get("name")),
            "color": colors.to_hex(_scalar(values.get("color"))),
            "length": _scalar(values.get("length")),
            "playing": _scalar(values.get("is_playing")),
            "midi": _scalar(values.get("is_midi_clip")),
        }
    if detail == "full":
        return_values = await _gets(bridge,
                                    [("song.return_tracks.%d" % i,
                                      ["name", "mute", "solo"])
                                     for i in range(return_count)])
        out["returns"] = [{"index": i, "name": (v or {}).get("name"),
                           "mute": (v or {}).get("mute"),
                           "locator": "return:%d" % i}
                          for i, v in enumerate(return_values)]
        master = await _gets(bridge, [("song.master_track", ["name"]),
                                      ("song.master_track.mixer_device.volume",
                                       ["display_value"])])
        out["master"] = {"name": (master[0] or {}).get("name"),
                         "volume": (master[1] or {}).get("display_value"),
                         "locator": "master"}
        mixer_values = await _gets(
            bridge,
            [("song.tracks.%d.mixer_device.volume" % i, ["display_value"])
             for i in range(track_count)] +
            [("song.tracks.%d.mixer_device.panning" % i, ["display_value"])
             for i in range(track_count)])
        for i, entry in enumerate(tracks):
            volume = mixer_values[i] or {}
            pan = mixer_values[track_count + i] or {}
            entry["volume"] = volume.get("display_value")
            entry["pan"] = pan.get("display_value")
    return out


async def get_track(session, track, detail="standard"):
    bridge = session.bridge
    ref = await resolve.resolve_track(bridge, track)
    described = await bridge.request("describe", path=ref["path"])
    p = described["props"]
    out = {"index": ref["index"], "kind": ref["kind"], "path": ref["path"],
           "name": _scalar(p.get("name")),
           "color": colors.to_hex(_scalar(p.get("color"))),
           "type": ("group" if _scalar(p.get("is_foldable")) else
                    "midi" if _scalar(p.get("has_midi_input")) else "audio"),
           "mute": _scalar(p.get("mute")), "solo": _scalar(p.get("solo")),
           "arm": _scalar(p.get("arm")),
           "can_be_armed": _scalar(p.get("can_be_armed"))}
    mixer = ref["path"] + ".mixer_device"
    send_count = 0
    mixer_specs = [(mixer + ".volume", ["value", "display_value", "min", "max"]),
                   (mixer + ".panning", ["value", "display_value"])]
    sends_len = await _gets(bridge, [(mixer, ["sends"])])
    if sends_len and sends_len[0]:
        send_count = _vec_length(sends_len[0].get("sends"))
    mixer_specs += [(mixer + ".sends.%d" % i, ["name", "value", "display_value"])
                    for i in range(send_count)]
    mixer_values = await _gets(bridge, mixer_specs)
    volume, panning = mixer_values[0] or {}, mixer_values[1] or {}
    out["mixer"] = {
        "volume": {"value": volume.get("value"),
                   "display": volume.get("display_value")},
        "pan": {"value": panning.get("value"),
                "display": panning.get("display_value")},
        "sends": [{"index": i, "name": (v or {}).get("name"),
                   "value": (v or {}).get("value"),
                   "display": (v or {}).get("display_value")}
                  for i, v in enumerate(mixer_values[2:])],
    }
    device_count = _vec_length(p.get("devices"))
    device_values = await _gets(bridge, [(ref["path"] + ".devices.%d" % i,
                                          ["name", "class_name", "parameters"])
                                         for i in range(device_count)])
    out["devices"] = [{"index": i, "name": (v or {}).get("name"),
                       "class": (v or {}).get("class_name"),
                       "parameter_count": _vec_length((v or {}).get("parameters"))}
                      for i, v in enumerate(device_values)]
    slot_count = _vec_length(p.get("clip_slots"))
    slot_values = await _gets(bridge, [(ref["path"] + ".clip_slots.%d" % s,
                                        ["has_clip"]) for s in range(slot_count)])
    clips = {}
    with_clip = [s for s, v in enumerate(slot_values)
                 if v and v.get("has_clip") is True]
    clip_values = await _gets(bridge,
                              [(ref["path"] + ".clip_slots.%d.clip" % s,
                                ["name", "color", "length", "is_playing"])
                               for s in with_clip])
    for s, values in zip(with_clip, clip_values):
        values = values or {}
        clips[str(s)] = {"name": values.get("name"),
                         "color": colors.to_hex(values.get("color")),
                         "length": values.get("length"),
                         "playing": values.get("is_playing")}
    out["clips"] = clips
    if detail == "full":
        parameter_values = []
        for device_index, device in enumerate(out["devices"]):
            count = device["parameter_count"]
            names = await _gets(bridge, [
                (ref["path"] + ".devices.%d.parameters.%d" % (device_index, i),
                 ["name"]) for i in range(count)])
            device["parameters"] = [(v or {}).get("name") for v in names]
        del parameter_values
    return out


PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def pitch_name(pitch):
    """Ableton's display convention: C3 = 60."""
    return "%s%d" % (PITCH_NAMES[pitch % 12], pitch // 12 - 2)


def summarize_notes(notes, bar_beats=4.0, grid=0.25):
    """Statistics over a note set — the cheap alternative to dumping them all.

    Deliberately statistics, not interpretation: no chord names, no key
    guessing. Naming what the numbers mean is the client's job (CLAUDE.md:
    no domain logic in this repo).
    """
    if not notes:
        return {"count": 0}
    pitches = [n["pitch"] for n in notes]
    starts = [n["start"] for n in notes]
    ends = [n["start"] + n["duration"] for n in notes]
    velocities = [n.get("velocity", 100) for n in notes]
    durations = [n["duration"] for n in notes]
    classes = sorted(set(p % 12 for p in pitches))
    deviations = [min(s % grid, grid - (s % grid)) for s in starts] if grid else []
    span_end = max(ends)
    bars = max(1, int(span_end / bar_beats) + (1 if span_end % bar_beats else 0))
    per_bar = []
    for bar in range(bars):
        lo, hi = bar * bar_beats, (bar + 1) * bar_beats
        per_bar.append(sum(1 for s in starts if lo <= s < hi))
    # max simultaneous sounding notes, by sweeping onsets and offsets
    events = sorted([(s, 1) for s in starts] + [(e, -1) for e in ends])
    live_now = peak = 0
    for _at, delta in events:
        live_now += delta
        peak = max(peak, live_now)
    summary = {
        "count": len(notes),
        "pitch": {"min": pitch_name(min(pitches)), "max": pitch_name(max(pitches)),
                  "distinct": len(set(pitches)),
                  "classes": [PITCH_NAMES[c] for c in classes]},
        "time": {"first_onset": round(min(starts), 6),
                 "last_end": round(span_end, 6),
                 "notes_per_bar": per_bar, "bar_beats": bar_beats},
        "velocity": {"min": min(velocities), "max": max(velocities),
                     "mean": round(sum(velocities) / len(velocities), 2),
                     "distinct": len(set(velocities))},
        "duration": {"min": round(min(durations), 6),
                     "max": round(max(durations), 6),
                     "mean": round(sum(durations) / len(durations), 6)},
        "max_polyphony": peak,
    }
    if deviations:
        on_grid = sum(1 for d in deviations if d < 0.002)
        summary["grid"] = {
            "beats": grid, "on_grid": on_grid, "off_grid": len(notes) - on_grid,
            "mean_deviation": round(sum(deviations) / len(deviations), 6),
            "max_deviation": round(max(deviations), 6),
            "verdict": "quantised" if on_grid == len(notes)
                       else "played" if on_grid < len(notes) * 0.2 else "mixed",
        }
    return summary


async def _bar_beats(bridge, clip_path):
    values = await _gets(bridge, [(clip_path, ["signature_numerator",
                                               "signature_denominator"])])
    values = values[0] or {}
    numerator = _scalar(values.get("signature_numerator")) or 4
    denominator = _scalar(values.get("signature_denominator")) or 4
    return float(numerator) * 4.0 / float(denominator)


async def get_clip(session, clip, include_notes=False, note_summary=False):
    bridge = session.bridge
    ref = await resolve.resolve_clip(bridge, clip)
    described = await bridge.request("describe", path=ref["clip_path"])
    p = described["props"]
    is_midi = _scalar(p.get("is_midi_clip"))
    out = {"track": ref["track_index"], "view": ref.get("view", "session"),
           "path": ref["clip_path"],
           "name": _scalar(p.get("name")),
           "color": colors.to_hex(_scalar(p.get("color"))),
           "length": _scalar(p.get("length")),
           "is_midi": is_midi,
           "looping": _scalar(p.get("looping")),
           "loop_start": _scalar(p.get("loop_start")),
           "loop_end": _scalar(p.get("loop_end")),
           "signature": "%s/%s" % (_scalar(p.get("signature_numerator")),
                                   _scalar(p.get("signature_denominator"))),
           "playing": _scalar(p.get("is_playing"))}
    if ref.get("view") == "arrangement":
        out["arrangement"] = {"index": ref["arrangement_index"],
                              "start": _scalar(p.get("start_time")),
                              "end": _scalar(p.get("end_time")),
                              "start_marker": _scalar(p.get("start_marker")),
                              "end_marker": _scalar(p.get("end_marker")),
                              "muted": _scalar(p.get("muted"))}
    else:
        out["slot"] = ref["slot"]
    if not is_midi:
        out["audio"] = {"warping": _scalar(p.get("warping")),
                        "warp_mode": _scalar(p.get("warp_mode")),
                        "gain": _scalar(p.get("gain")),
                        "pitch_coarse": _scalar(p.get("pitch_coarse")),
                        "pitch_fine": _scalar(p.get("pitch_fine")),
                        "file_path": _scalar(p.get("file_path"))}
    if (include_notes or note_summary) and is_midi:
        notes = await bridge.request("get_notes", path=ref["clip_path"])
        if include_notes:
            out["notes"] = notes["notes"]
        if note_summary:
            bar_beats = await _bar_beats(bridge, ref["clip_path"])
            out["note_summary"] = summarize_notes(notes["notes"], bar_beats)
    return out


async def get_notes(session, clip, from_time=None, time_span=None,
                    from_pitch=None, pitch_span=None, summary=False,
                    grid=0.25):
    bridge = session.bridge
    ref = await resolve.resolve_clip(bridge, clip)
    params = {"path": ref["clip_path"]}
    if from_time is not None:
        params["from_time"] = _require_number(from_time, "from_time", lo=0)
    if time_span is not None:
        params["time_span"] = _require_number(time_span, "time_span")
    if from_pitch is not None:
        params["from_pitch"] = int(from_pitch)
    if pitch_span is not None:
        params["pitch_span"] = int(pitch_span)
    result = await bridge.request("get_notes", **params)
    out = {"track": ref["track_index"], "view": ref.get("view", "session"),
           "count": len(result["notes"])}
    if ref.get("view") == "arrangement":
        out["arrangement"] = ref["arrangement_index"]
    else:
        out["slot"] = ref["slot"]
    if summary:
        bar_beats = await _bar_beats(bridge, ref["clip_path"])
        out["summary"] = summarize_notes(result["notes"], bar_beats, grid)
    else:
        out["notes"] = result["notes"]
    return out


def _reconcile_connection(session):
    """Void anything tied to a connection that no longer exists.

    Subscriptions live inside the Remote Script and die with their socket;
    the script then hands out ids from 1 again after a Live restart, so a
    stale registry would both advertise dead watches and misattribute old
    events to new ones. Returns the watch ids that were dropped.
    """
    bridge = session.bridge
    # Valid only while the very connection that created them is still up:
    # a dropped socket kills them even if nothing has reconnected yet.
    alive = bridge.connected and bridge.epoch == session.seen_epoch
    dropped = []
    if session.watches and not alive:
        dropped = sorted(session.watches)
        session.watches.clear()
        bridge.feed = type(bridge.feed)()
    if bridge.connected:
        session.seen_epoch = bridge.epoch
    return dropped


async def _verify_watches(session):
    """Ask Live whether each watched object still exists.

    The bridge can only notice a dead object when one of its listeners fires,
    and a deleted object fires nothing — so its `gone` event is best-effort
    and in practice rarely arrives. Verified 2026-08-03: deleting a watched
    track produces no event at all. Since MCP is pull-based, the honest place
    to check is the moment the caller asks.
    """
    if not session.watches:
        return []
    watched = sorted(session.watches.items())
    ops = [{"op": "get", "path": entry["path"], "props": entry["props"][:1]}
           for _sub, entry in watched]
    result = await session.bridge.request("batch", ops=ops, stop_on_error=False)
    died = []
    for (sub, entry), sub_result in zip(watched, result["results"]):
        gone = not sub_result.get("ok")
        if not gone:
            values = sub_result["result"]["values"]
            gone = any(isinstance(v, dict) and "$error" in v
                       for v in values.values())
        if gone:
            died.append({"watch_id": sub, "path": entry["path"]})
            session.watches.pop(sub, None)
    return died


async def get_changes(session, since=0, verify=True):
    dropped = _reconcile_connection(session)
    died = await _verify_watches(session) if verify else []
    events = session.bridge.feed.since(since)
    shaped = []
    for event in events:
        entry = {"seq": event["feed_seq"], "kind": event.get("event")}
        for key in ("sub", "path", "prop", "value", "dropped", "reason"):
            if key in event:
                entry[key] = event[key]
        watch = session.watches.get(event.get("sub"))
        if event.get("event") == "gone":
            session.watches.pop(event.get("sub"), None)
        if watch:
            entry["watched"] = "%s (%s)" % (watch["path"],
                                            ",".join(watch["props"]))
        shaped.append(entry)
    out = {"events": shaped, "latest_seq": session.bridge.feed.latest_seq,
           "active_watches": {str(k): v for k, v in session.watches.items()}}
    if dropped:
        out["watches_dropped"] = dropped
        out["note"] = ("the connection to Live was replaced, so these watches "
                       "died with it — call watch() again to resume")
    if died:
        out["watches_died"] = died
        out["note"] = ("what these watches pointed at no longer exists in the "
                       "set — call watch() again on whatever replaced it")
    return out


# --- LOM escape hatches -----------------------------------------------------------


async def lom_describe(session, path):
    return await session.bridge.request("describe", path=path)


async def lom_get(session, path, props):
    return await session.bridge.request("get", path=path, props=props)


async def lom_set(session, path, props):
    if not isinstance(props, dict) or not props:
        raise ToolError("invalid_argument", "props must be a non-empty object")
    described = await session.bridge.request("describe", path=path)
    class_name = described.get("class")
    for prop in props:
        known = inventory.writable(class_name, prop)
        if known is False:
            raise ToolError("property_read_only",
                            "%s.%s is read-only in the inventory"
                            % (class_name, prop),
                            hint="see docs/lom-inventory.md")
        if known is None and inventory.known_class(class_name):
            raise ToolError("property_not_found",
                            "%s has no property '%s' in the inventory"
                            % (class_name, prop),
                            hint="lom_describe the object to see its props")
    return await session.bridge.request("set", path=path, props=props)


async def _c_lom_set(session, params):
    described = await session.bridge.request("describe", path=params.get("path"))
    class_name = described.get("class")
    for prop in params.get("props") or {}:
        if inventory.writable(class_name, prop) is False:
            raise ToolError("property_read_only",
                            "%s.%s is read-only" % (class_name, prop))
    return [{"op": "set", "path": params["path"], "props": params["props"]}]


async def _c_lom_call(session, params):
    described = await session.bridge.request("describe", path=params.get("path"))
    class_name = described.get("class")
    method = params.get("method")
    if inventory.has_method(class_name, method) is False:
        raise ToolError("method_not_found",
                        "%s has no method '%s'" % (class_name, method))
    return [{"op": "call", "path": params["path"], "method": method,
             "args": params.get("args") or [],
             "kwargs": params.get("kwargs") or {}}]


async def lom_call(session, path, method, args=None, kwargs=None):
    described = await session.bridge.request("describe", path=path)
    class_name = described.get("class")
    known = inventory.has_method(class_name, method)
    if known is False:
        raise ToolError("method_not_found",
                        "%s has no method '%s' in the inventory"
                        % (class_name, method),
                        hint="see docs/lom-inventory.md")
    return await session.bridge.request("call", path=path, method=method,
                                        args=args or [], kwargs=kwargs or {})


# --- compilers (shared by single tools and song_batch) -----------------------------


SONG_SETTABLE = {"tempo", "signature_numerator", "signature_denominator",
                 "scale_name", "root_note", "scale_mode", "groove_amount",
                 "metronome", "tempo_follower_enabled"}


async def _c_set_song(session, params):
    props = {}
    for key, value in params.items():
        if key not in SONG_SETTABLE:
            raise ToolError("invalid_argument", "set_song: unknown field %r" % key,
                            hint="settable: %s" % ", ".join(sorted(SONG_SETTABLE)))
        props[key] = value
    if "tempo" in props:
        _require_number(props["tempo"], "tempo", lo=20, hi=999)
    if "root_note" in props and props["root_note"] not in range(12):
        raise ToolError("invalid_argument", "root_note must be 0-11 (0 = C)")
    if not props:
        raise ToolError("invalid_argument", "set_song: nothing to set")
    return [{"op": "set", "path": "song", "props": props}]


def _volume_ops(param_path, value, name):
    # DeviceParameter.display_value is numeric in DISPLAY units (dB for
    # volumes), readable AND writable — verified against Live 12.4.3.
    if isinstance(value, dict) and "db" in value:
        db = _require_number(value["db"], name + ".db")
        return [{"op": "set", "path": param_path,
                 "props": {"display_value": db}}]
    if isinstance(value, dict) and "normalized" in value:
        value = value["normalized"]
    number = _require_number(value, name, lo=0.0, hi=1.0)
    return [{"op": "set", "path": param_path, "props": {"value": number}}]


async def _c_set_track(session, params):
    bridge = session.bridge
    if "track" not in params:
        raise ToolError("invalid_argument", "set_track needs a track locator")
    ref = await resolve.resolve_track(bridge, params["track"])
    ops = []
    props = {}
    if "name" in params:
        props["name"] = str(params["name"])
    if "color" in params:
        props["color"] = colors.to_int(params["color"])
    for flag in ("mute", "solo"):
        if flag in params:
            props[flag] = bool(params[flag])
    if "arm" in params:
        values = await _gets(bridge, [(ref["path"], ["can_be_armed"])])
        if not (values[0] or {}).get("can_be_armed"):
            raise ToolError("invalid_argument",
                            "track %d cannot be armed" % ref["index"],
                            hint="group/return/master tracks have no arm")
        props["arm"] = bool(params["arm"])
    if props:
        ops.append({"op": "set", "path": ref["path"], "props": props})
    mixer = ref["path"] + ".mixer_device"
    if "volume" in params:
        ops += _volume_ops(mixer + ".volume", params["volume"], "volume")
    if "pan" in params:
        pan = _require_number(params["pan"], "pan", lo=-1.0, hi=1.0)
        ops.append({"op": "set", "path": mixer + ".panning",
                    "props": {"value": pan}})
    for send in params.get("sends", []) or []:
        if not isinstance(send, dict) or "send" not in send:
            raise ToolError("invalid_argument",
                            "sends entries are {send: index|name, value|db}")
        spec = send["send"]
        if isinstance(spec, str):
            return_ref = await resolve.resolve_return_track(bridge, spec)
            index = return_ref["index"]
        else:
            index = int(spec)
        send_path = "%s.sends.%d" % (mixer, index)
        if "db" in send:
            ops += _volume_ops(send_path, {"db": send["db"]}, "send")
        else:
            ops += _volume_ops(send_path, send.get("value"), "send")
    if not ops:
        raise ToolError("invalid_argument", "set_track: nothing to set")
    return ops


async def _c_set_clip(session, params):
    ref = await resolve.resolve_clip(session.bridge, params.get("clip"))
    props = {}
    if "name" in params:
        props["name"] = str(params["name"])
    if "color" in params:
        props["color"] = colors.to_int(params["color"])
    for key in ("looping", "loop_start", "loop_end",
                "signature_numerator", "signature_denominator"):
        if key in params:
            props[key] = params[key]
    if not props:
        raise ToolError("invalid_argument", "set_clip: nothing to set")
    return [{"op": "set", "path": ref["clip_path"], "props": props}]


async def _c_set_scene(session, params):
    ref = await resolve.resolve_scene(session.bridge, params.get("scene"))
    props = {}
    if "name" in params:
        props["name"] = str(params["name"])
    if "color" in params:
        props["color"] = colors.to_int(params["color"])
    if not props:
        raise ToolError("invalid_argument", "set_scene: nothing to set")
    return [{"op": "set", "path": ref["path"], "props": props}]


async def _c_edit_notes(session, params):
    ref = await resolve.resolve_clip(session.bridge, params.get("clip"))
    op = {"op": "edit_notes", "path": ref["clip_path"]}
    if params.get("add"):
        op["add"] = _validate_notes(params["add"], "add")
    if params.get("update"):
        op["update"] = params["update"]
    if params.get("remove_ids"):
        op["remove_ids"] = params["remove_ids"]
    if params.get("remove_region"):
        op["remove_region"] = params["remove_region"]
    if len(op) == 2:
        raise ToolError("invalid_argument",
                        "edit_notes: nothing to do (add/update/remove_ids/"
                        "remove_region all empty)")
    return [op]


async def _c_create_clip(session, params):
    for field in ("track", "slot", "length", "name"):
        if field not in params:
            raise ToolError("invalid_argument",
                            "create_clip needs '%s'" % field)
    ref = await resolve.resolve_slot(session.bridge, params["track"],
                                     params["slot"])
    if ref["has_clip"]:
        raise ToolError("conflict",
                        "track %d slot %d already holds a clip"
                        % (ref["track_index"], ref["slot"]),
                        hint="delete_clip first, or choose another slot")
    length = _require_number(params["length"], "length")
    if length <= 0:
        raise ToolError("invalid_argument", "length must be > 0 beats")
    clip_path = ref["slot_path"] + ".clip"
    ops = [{"op": "call", "path": ref["slot_path"], "method": "create_clip",
            "args": [length]}]
    props = {"name": str(params["name"])}
    if params.get("color") is not None:
        props["color"] = colors.to_int(params["color"])
    if params.get("signature_numerator"):
        props["signature_numerator"] = int(params["signature_numerator"])
    if params.get("signature_denominator"):
        props["signature_denominator"] = int(params["signature_denominator"])
    if params.get("loop") is False:
        props["looping"] = False
    ops.append({"op": "set", "path": clip_path, "props": props})
    if params.get("notes"):
        ops.append({"op": "edit_notes", "path": clip_path,
                    "add": _validate_notes(params["notes"], "notes")})
    return ops


async def _c_create_scene(session, params):
    index = params.get("index", -1)
    ops = [{"op": "call", "path": "song", "method": "create_scene",
            "args": [int(index)]}]
    if params.get("name") or params.get("color") is not None:
        scene_count = await resolve.vec_len(session.bridge, "song", "scenes")
        new_index = scene_count if index == -1 else int(index)
        props = {}
        if params.get("name"):
            props["name"] = str(params["name"])
        if params.get("color") is not None:
            props["color"] = colors.to_int(params["color"])
        ops.append({"op": "set", "path": "song.scenes.%d" % new_index,
                    "props": props})
    return ops


async def _c_create_track(session, params, kind):
    index = params.get("index", -1)
    method = "create_midi_track" if kind == "midi" else "create_audio_track"
    ops = [{"op": "call", "path": "song", "method": method, "args": [int(index)]}]
    track_count = await resolve.vec_len(session.bridge, "song", "tracks")
    new_index = track_count if index == -1 else int(index)
    props = {}
    if params.get("name"):
        props["name"] = str(params["name"])
    if params.get("color") is not None:
        props["color"] = colors.to_int(params["color"])
    if props:
        ops.append({"op": "set", "path": "song.tracks.%d" % new_index,
                    "props": props})
    return ops, new_index


async def _c_create_midi_track(session, params):
    ops, _index = await _c_create_track(session, params, "midi")
    return ops


async def _c_create_audio_track(session, params):
    ops, _index = await _c_create_track(session, params, "audio")
    return ops


async def _c_quantize_clip(session, params):
    ref = await resolve.resolve_clip(session.bridge, params.get("clip"))
    grid = _require_number(params.get("grid"), "grid")
    amount = _require_number(params.get("amount", 1.0), "amount", lo=0.0, hi=1.0)
    enum_name = None
    for value, name in _GRID_ENUMS:
        if abs(grid - value) < 1e-6:
            enum_name = name
            break
    if enum_name is None:
        raise ToolError("invalid_argument", "unsupported grid %r" % grid,
                        hint="supported (beats): 1.0, 0.5, 0.3333, 0.25, "
                             "0.1667, 0.125")
    enums = inventory.enum_values("Song.RecordingQuantization") or {}
    if enum_name not in enums:
        raise ToolError("internal", "RecordingQuantization.%s missing" % enum_name)
    return [{"op": "call", "path": ref["clip_path"], "method": "quantize",
             "args": [enums[enum_name], amount]}]


async def _c_fire_clip(session, params):
    ref = await resolve.resolve_clip(session.bridge, params.get("clip"))
    return [{"op": "call", "path": ref["slot_path"], "method": "fire"}]


async def _c_stop_clip(session, params):
    clip = params.get("clip")
    if not isinstance(clip, dict):
        raise ToolError("invalid_argument",
                        "stop_clip needs {track, slot}")
    ref = await resolve.resolve_slot(session.bridge, clip.get("track"),
                                     clip.get("slot"))
    return [{"op": "call", "path": ref["slot_path"], "method": "stop"}]


async def _c_fire_scene(session, params):
    ref = await resolve.resolve_scene(session.bridge, params.get("scene"))
    return [{"op": "call", "path": ref["path"], "method": "fire"}]


async def _c_transport(session, params):
    action = params.get("action")
    ops = []
    if params.get("position") is not None:
        ops.append({"op": "set", "path": "song",
                    "props": {"current_song_time":
                              _require_number(params["position"], "position",
                                              lo=0)}})
    methods = {"play": "start_playing", "stop": "stop_playing",
               "continue": "continue_playing"}
    if action is not None:
        if action not in methods:
            raise ToolError("invalid_argument",
                            "action must be play|stop|continue")
        ops.append({"op": "call", "path": "song", "method": methods[action]})
    if not ops:
        raise ToolError("invalid_argument",
                        "transport: give an action and/or a position")
    return ops


BATCHABLE_TOOLS = {
    "set_song": _c_set_song,
    "set_track": _c_set_track,
    "set_clip": _c_set_clip,
    "set_scene": _c_set_scene,
    "edit_notes": _c_edit_notes,
    "create_clip": _c_create_clip,
    "create_scene": _c_create_scene,
    "create_midi_track": _c_create_midi_track,
    "create_audio_track": _c_create_audio_track,
    "quantize_clip": _c_quantize_clip,
    "fire_clip": _c_fire_clip,
    "fire_scene": _c_fire_scene,
    "stop_clip": _c_stop_clip,
    "transport": _c_transport,
    "lom_set": _c_lom_set,
    "lom_call": _c_lom_call,
}


# --- song & transport tools ----------------------------------------------------


async def set_song(session, **params):
    ops = await _c_set_song(session, params)
    await _run_atomic(session.bridge, ops, "set_song")
    result = await session.bridge.request("get", path="song",
                                          props=list(ops[0]["props"].keys()))
    return {"values": result["values"]}


async def transport(session, action=None, position=None):
    ops = await _c_transport(session, {"action": action, "position": position})
    await _run_atomic(session.bridge, ops, "transport")
    result = await session.bridge.request(
        "get", path="song", props=["is_playing", "current_song_time"])
    out = {"is_playing": result["values"]["is_playing"],
           "position": result["values"]["current_song_time"]}
    if position is not None and out["is_playing"]:
        out["note"] = ("the transport is rolling, so the position read back "
                       "has already advanced past the one that was set")
    return out


# --- tracks ------------------------------------------------------------------------


async def _track_readback(bridge, index):
    values = await _gets(bridge, [("song.tracks.%d" % index, ["name", "color"])])
    values = values[0] or {}
    return {"index": index, "path": "song.tracks.%d" % index,
            "name": values.get("name"),
            "color": colors.to_hex(values.get("color"))}


async def create_midi_track(session, name=None, color=None, index=-1):
    params = {"name": name, "color": color, "index": index}
    ops, new_index = await _c_create_track(session, params, "midi")
    await _run_atomic(session.bridge, ops, "create_midi_track")
    return {"track": await _track_readback(session.bridge, new_index)}


async def create_audio_track(session, name=None, color=None, index=-1):
    params = {"name": name, "color": color, "index": index}
    ops, new_index = await _c_create_track(session, params, "audio")
    await _run_atomic(session.bridge, ops, "create_audio_track")
    return {"track": await _track_readback(session.bridge, new_index)}


async def set_track(session, track, **params):
    params["track"] = track
    ops = await _c_set_track(session, params)
    results = await _run_atomic(session.bridge, ops, "set_track")
    read_back = {}
    for op, sub in zip(ops, results):
        if sub.get("ok") and "values" in (sub.get("result") or {}):
            for prop, value in sub["result"]["values"].items():
                key = op["path"].split(".mixer_device.")[-1] + "." + prop \
                    if ".mixer_device." in op["path"] else prop
                read_back[key] = colors.to_hex(value) if prop == "color" else value
    return {"values": read_back}


async def delete_track(session, track):
    ref = await resolve.resolve_track(session.bridge, track)
    if ref["kind"] == "master":
        raise ToolError("invalid_argument", "Live cannot delete the master track")
    # Regular tracks and returns are separate vectors: deleting a return by
    # its index through Song.delete_track would remove the regular track that
    # happens to share that index.
    method = ("delete_return_track" if ref["kind"] == "return"
              else "delete_track")
    await _run_atomic(session.bridge,
                      [{"op": "call", "path": "song", "method": method,
                        "args": [ref["index"]]}], "delete_track")
    return {"deleted_index": ref["index"], "kind": ref["kind"],
            "was_named": ref.get("name")}


async def duplicate_track(session, track):
    ref = await resolve.resolve_track(session.bridge, track)
    if ref["kind"] != "track":
        raise ToolError("invalid_argument",
                        "Live can only duplicate regular tracks, not the %s"
                        % ("master track" if ref["kind"] == "master"
                           else "return track"))
    await _run_atomic(session.bridge,
                      [{"op": "call", "path": "song", "method": "duplicate_track",
                        "args": [ref["index"]]}], "duplicate_track")
    return {"track": await _track_readback(session.bridge, ref["index"] + 1)}


# --- clips, notes, scenes -----------------------------------------------------------


async def create_clip(session, track, slot, length, name, color=None,
                      notes=None, signature_numerator=None,
                      signature_denominator=None, loop=True):
    params = {"track": track, "slot": slot, "length": length, "name": name,
              "color": color, "notes": notes,
              "signature_numerator": signature_numerator,
              "signature_denominator": signature_denominator, "loop": loop}
    ops = await _c_create_clip(session, params)
    results = await _run_atomic(session.bridge, ops, "create_clip")
    added_ids = []
    if notes:
        added_ids = (results[-1].get("result") or {}).get("added_ids", [])
    ref = await resolve.resolve_clip(session.bridge,
                                     {"track": track, "slot": slot})
    values = await _gets(session.bridge,
                         [(ref["clip_path"], ["name", "length", "color"])])
    values = values[0] or {}
    return {"clip": {"track": ref["track_index"], "slot": ref["slot"],
                     "name": values.get("name"),
                     "length": values.get("length"),
                     "color": colors.to_hex(values.get("color"))},
            "added_note_ids": added_ids}


async def set_clip(session, clip, **params):
    params["clip"] = clip
    ops = await _c_set_clip(session, params)
    results = await _run_atomic(session.bridge, ops, "set_clip")
    values = (results[0].get("result") or {}).get("values", {})
    if "color" in values:
        values["color"] = colors.to_hex(values["color"])
    return {"values": values}


async def delete_clip(session, clip):
    ref = await resolve.resolve_clip(session.bridge, clip)
    await _run_atomic(session.bridge,
                      [{"op": "call", "path": ref["slot_path"],
                        "method": "delete_clip"}], "delete_clip")
    return {"deleted": {"track": ref["track_index"], "slot": ref["slot"]}}


async def duplicate_clip_to_slot(session, clip, target):
    source = await resolve.resolve_clip(session.bridge, clip)
    if not isinstance(target, dict):
        raise ToolError("invalid_argument", "target must be {track, slot}")
    dest = await resolve.resolve_slot(session.bridge, target.get("track"),
                                      target.get("slot"))
    if dest["has_clip"]:
        raise ToolError("conflict", "target slot already holds a clip",
                        hint="delete_clip there first")
    await _run_atomic(session.bridge,
                      [{"op": "call", "path": source["slot_path"],
                        "method": "duplicate_clip_to",
                        "args": [{"$obj": {"path": dest["slot_path"]}}]}],
                      "duplicate_clip_to_slot")
    return {"clip": {"track": dest["track_index"], "slot": dest["slot"]}}


async def edit_notes(session, clip, add=None, update=None, remove_ids=None,
                     remove_region=None):
    params = {"clip": clip, "add": add, "update": update,
              "remove_ids": remove_ids, "remove_region": remove_region}
    ops = await _c_edit_notes(session, params)
    results = await _run_atomic(session.bridge, ops, "edit_notes")
    return results[0].get("result") or {}


async def quantize_clip(session, clip, grid, amount=1.0):
    ops = await _c_quantize_clip(session, {"clip": clip, "grid": grid,
                                           "amount": amount})
    await _run_atomic(session.bridge, ops, "quantize_clip")
    return {"quantized": True, "grid": grid, "amount": amount}


async def create_scene(session, index=-1, name=None, color=None):
    ops = await _c_create_scene(session, {"index": index, "name": name,
                                          "color": color})
    await _run_atomic(session.bridge, ops, "create_scene")
    scene_count = await resolve.vec_len(session.bridge, "song", "scenes")
    new_index = scene_count - 1 if index == -1 else int(index)
    values = await _gets(session.bridge,
                         [("song.scenes.%d" % new_index, ["name", "color"])])
    values = values[0] or {}
    return {"scene": {"index": new_index, "name": values.get("name"),
                      "color": colors.to_hex(values.get("color"))}}


async def set_scene(session, scene, name=None, color=None):
    params = {"scene": scene}
    if name is not None:
        params["name"] = name
    if color is not None:
        params["color"] = color
    ops = await _c_set_scene(session, params)
    results = await _run_atomic(session.bridge, ops, "set_scene")
    values = (results[0].get("result") or {}).get("values", {})
    if "color" in values:
        values["color"] = colors.to_hex(values["color"])
    return {"values": values}


async def delete_scene(session, scene):
    ref = await resolve.resolve_scene(session.bridge, scene)
    await _run_atomic(session.bridge,
                      [{"op": "call", "path": "song", "method": "delete_scene",
                        "args": [ref["index"]]}], "delete_scene")
    return {"deleted_index": ref["index"]}


async def fire_scene(session, scene):
    ops = await _c_fire_scene(session, {"scene": scene})
    await _run_atomic(session.bridge, ops, "fire_scene")
    return {"fired": True}


async def fire_clip(session, clip):
    ops = await _c_fire_clip(session, {"clip": clip})
    await _run_atomic(session.bridge, ops, "fire_clip")
    return {"fired": True,
            "note": "launch respects Live's clip launch quantization"}


async def stop_clip(session, clip):
    ops = await _c_stop_clip(session, {"clip": clip})
    await _run_atomic(session.bridge, ops, "stop_clip")
    return {"stopped": True}


async def stop_all_clips(session, track=None):
    if track is None:
        ops = [{"op": "call", "path": "song", "method": "stop_all_clips"}]
    else:
        ref = await resolve.resolve_track(session.bridge, track)
        ops = [{"op": "call", "path": ref["path"], "method": "stop_all_clips"}]
    await _run_atomic(session.bridge, ops, "stop_all_clips")
    return {"stopped": True}


# --- devices and browser --------------------------------------------------------------


async def _walk_browser_category(session, category):
    bridge = session.bridge
    root_prop = category
    root_owner = "app.browser"
    result = await bridge.request("get", path=root_owner, props=[root_prop])
    value = result["values"].get(root_prop)
    queue = []
    if isinstance(value, dict) and "$vec" in value:
        queue = [("%s.%s.%d" % (root_owner, root_prop, i), "")
                 for i in range(value["$vec"]["len"])]
    elif isinstance(value, dict) and "$obj" in value:
        queue = [("%s.%s" % (root_owner, root_prop), "")]
    items = []
    by_uri = {}
    while queue and len(items) < BROWSER_MAX_ITEMS:
        chunk, queue = queue[:BATCH_CHUNK], queue[BATCH_CHUNK:]
        ops = [{"op": "get", "path": path,
                "props": ["name", "uri", "is_loadable", "is_folder", "children"]}
               for path, _folder in chunk]
        result = await bridge.request("batch", ops=ops, stop_on_error=False)
        for (path, folder), sub in zip(chunk, result["results"]):
            if not sub.get("ok"):
                continue
            values = sub["result"]["values"]
            name = _scalar(values.get("name"))
            entry = {"name": name, "uri": _scalar(values.get("uri")),
                     "loadable": _scalar(values.get("is_loadable")),
                     "folder": folder, "path": path}
            if entry["loadable"] and entry["uri"]:
                items.append(entry)
                by_uri[entry["uri"]] = entry
            child_folder = (folder + " / " + name) if folder else (name or "")
            if child_folder.count(" / ") < BROWSER_MAX_DEPTH:
                for i in range(_vec_length(values.get("children"))):
                    queue.append(("%s.children.%d" % (path, i), child_folder))
    session.browser_cache[category] = {"items": items, "by_uri": by_uri,
                                       "walked_at": _time.time()}
    return session.browser_cache[category]


async def browse(session, query, category=None, refresh=False):
    if category is not None and category not in BROWSER_CATEGORIES:
        raise ToolError("invalid_argument", "unknown category %r" % category,
                        hint="one of: %s" % ", ".join(sorted(BROWSER_CATEGORIES)))
    categories = [category] if category else list(DEFAULT_BROWSE_CATEGORIES)
    terms = [t for t in str(query).lower().split() if t]
    matches = []
    walked = {}
    for cat in categories:
        cache = None if refresh else session.browser_cache.get(cat)
        if cache is None:
            cache = await _walk_browser_category(session, cat)
        walked[cat] = {"items": len(cache["items"]),
                       "age_seconds": round(_time.time() - cache["walked_at"], 1)}
        for item in cache["items"]:
            haystack = ("%s %s" % (item["name"] or "",
                                   item["folder"] or "")).lower()
            if all(term in haystack for term in terms):
                starts = (item["name"] or "").lower().startswith(
                    terms[0]) if terms else False
                matches.append((0 if starts else 1, cat, item))
    matches.sort(key=lambda m: (m[0], len(m[2]["name"] or "")))
    shaped = [{"name": item["name"], "uri": item["uri"], "category": cat,
               "folder": item["folder"]}
              for _rank, cat, item in matches[:BROWSE_RESULT_LIMIT]]
    return {"matches": shaped,
            "searched_categories": categories,
            "total_matches": len(matches),
            "index": walked,
            "note": "the index is cached per category; pass refresh=true after "
                    "installing packs or adding user-library content"}


async def refresh_browser_index(session, category=None):
    """Drop the cached browser walk so the next browse re-reads Live."""
    if category is not None and category not in BROWSER_CATEGORIES:
        raise ToolError("invalid_argument", "unknown category %r" % category,
                        hint="one of: %s" % ", ".join(sorted(BROWSER_CATEGORIES)))
    dropped = sorted(session.browser_cache) if category is None else \
        ([category] if category in session.browser_cache else [])
    if category is None:
        session.browser_cache.clear()
    else:
        session.browser_cache.pop(category, None)
    return {"dropped": dropped,
            "note": "the next browse for these categories walks Live again"}


async def load_device(session, track, uri):
    bridge = session.bridge
    ref = await resolve.resolve_track(bridge, track)
    entry = None
    for cache in session.browser_cache.values():
        entry = cache["by_uri"].get(uri)
        if entry:
            break
    if entry is None:
        for cat in DEFAULT_BROWSE_CATEGORIES:
            if cat not in session.browser_cache:
                cache = await _walk_browser_category(session, cat)
                entry = cache["by_uri"].get(uri)
                if entry:
                    break
    if entry is None:
        raise ToolError("not_found", "no loadable browser item with uri %r" % uri,
                        hint="use browse to find the exact uri")
    before = await resolve.vec_len(bridge, ref["path"], "devices")
    ops = [{"op": "set", "path": "song.view",
            "props": {"selected_track": {"$obj": {"path": ref["path"]}}}},
           {"op": "call", "path": "app.browser", "method": "load_item",
            "args": [{"$obj": {"path": entry["path"]}}]}]
    await _run_atomic(bridge, ops, "load_device")
    after = await resolve.vec_len(bridge, ref["path"], "devices")
    devices = await _gets(bridge, [("%s.devices.%d" % (ref["path"], i), ["name"])
                                   for i in range(after)])
    return {"loaded": entry["name"], "track": ref["index"],
            "devices_now": [(v or {}).get("name") for v in devices],
            "device_count_change": after - before}


async def set_device_parameter(session, track, device, parameter, value):
    bridge = session.bridge
    track_ref = await resolve.resolve_track(bridge, track)
    device_ref = await resolve.resolve_device(bridge, track_ref["path"], device)
    param_ref = await resolve.resolve_parameter(bridge, device_ref["path"],
                                                parameter)
    if isinstance(value, dict) and "display" in value:
        props = {"display_value": _require_number(value["display"], "display")}
    else:
        bounds = await _gets(bridge, [(param_ref["path"], ["min", "max"])])
        bounds = bounds[0] or {}
        number = _require_number(value, "value",
                                 lo=bounds.get("min"), hi=bounds.get("max"))
        props = {"value": number}
    result = await session.bridge.request("set", path=param_ref["path"],
                                          props=props)
    read_back = await _gets(bridge, [(param_ref["path"],
                                      ["value", "display_value", "min", "max",
                                       "name"])])
    return {"parameter": read_back[0], "written": result["values"]}


# --- clip automation ------------------------------------------------------------------

AUTOMATION_MAX_STEPS = 240   # one wire batch is capped at 256 ops


async def _envelope_index(bridge, clip_path, param_path):
    """Index of the clip envelope belonging to a parameter.

    Identity is by `_live_ptr`, not by name: two devices on one track can both
    expose a "Filter Cutoff", and Envelope.parameter comes back as an $obj
    stub with no canonical path.
    """
    values = await _gets(bridge, [(param_path, ["_live_ptr"])])
    target = (values[0] or {}).get("_live_ptr")
    count = await resolve.vec_len(bridge, clip_path, "automation_envelopes")
    ptrs = await _gets(bridge, [("%s.automation_envelopes.%d.parameter"
                                 % (clip_path, i), ["_live_ptr"])
                                for i in range(count)])
    for index, values in enumerate(ptrs):
        if values and values.get("_live_ptr") == target:
            return index
    return None


def _render_steps(points, resolution, mode, lo, hi):
    """Breakpoints -> a tiling of constant steps covering [first, last].

    The caller describes the SHAPE (a few breakpoints); the server renders it,
    because Live's only writable envelope primitive is insert_step and
    EnvelopeEvent objects cannot be constructed over the wire.
    """
    def clamp(value):
        return max(lo, min(hi, float(value)))

    steps = []
    if mode == "hold":
        for index, point in enumerate(points):
            end = (points[index + 1]["time"] if index + 1 < len(points)
                   else point["time"] + resolution)
            span = max(end - point["time"], 1e-6)
            steps.append((point["time"], span, clamp(point["value"])))
        return steps
    for index in range(len(points) - 1):
        a, b = points[index], points[index + 1]
        span = b["time"] - a["time"]
        if span <= 0:
            continue
        count = max(1, int(round(span / resolution)))
        width = span / count
        for k in range(count):
            t = a["time"] + k * width
            ratio = (t - a["time"]) / span
            steps.append((t, width,
                          clamp(a["value"] + (b["value"] - a["value"]) * ratio)))
    last = points[-1]
    steps.append((last["time"], resolution, clamp(last["value"])))
    return steps


async def automate_parameter(session, clip, device, parameter, points,
                             resolution=0.5, mode="ramp"):
    bridge = session.bridge
    if mode not in ("ramp", "hold"):
        raise ToolError("invalid_argument", "mode must be 'ramp' or 'hold'")
    if not isinstance(points, list) or not points:
        raise ToolError("invalid_argument",
                        "points must be [{\"time\": beats, \"value\": n}, ...]")
    shaped = []
    for index, point in enumerate(points):
        if not isinstance(point, dict) or "time" not in point \
                or "value" not in point:
            raise ToolError("invalid_argument",
                            "points[%d] needs 'time' and 'value'" % index)
        shaped.append({"time": _require_number(point["time"],
                                               "points[%d].time" % index, lo=0),
                       "value": _require_number(point["value"],
                                                "points[%d].value" % index)})
    shaped.sort(key=lambda p: p["time"])
    resolution = _require_number(resolution, "resolution")
    if resolution <= 0:
        raise ToolError("invalid_argument", "resolution must be > 0 beats")

    clip_ref = await resolve.resolve_clip(bridge, clip)
    device_ref = await resolve.resolve_device(bridge, clip_ref["track_path"],
                                              device)
    param_ref = await resolve.resolve_parameter(bridge, device_ref["path"],
                                                parameter)
    bounds = await _gets(bridge, [(param_ref["path"],
                                   ["min", "max", "name", "is_quantized"])])
    bounds = bounds[0] or {}
    lo = bounds.get("min", 0.0)
    hi = bounds.get("max", 1.0)

    steps = _render_steps(shaped, resolution, mode, lo, hi)
    if len(steps) > AUTOMATION_MAX_STEPS:
        raise ToolError("too_large",
                        "%d steps exceed the %d-per-call limit"
                        % (len(steps), AUTOMATION_MAX_STEPS),
                        hint="raise `resolution` (e.g. %.2f) or automate a "
                             "shorter span"
                             % (resolution * len(steps) / AUTOMATION_MAX_STEPS))

    # Live's create_automation_envelope is NOT idempotent: it raises
    # "There is already an envelope for the parameter". Look first, create
    # only if missing — that creation is its own small undo step; the shape
    # below is always exactly one.
    index = await _envelope_index(bridge, clip_ref["clip_path"],
                                  param_ref["path"])
    if index is None:
        await bridge.request("call", path=clip_ref["clip_path"],
                             method="create_automation_envelope",
                             args=[{"$obj": {"path": param_ref["path"]}}])
        index = await _envelope_index(bridge, clip_ref["clip_path"],
                                      param_ref["path"])
    if index is None:
        raise ToolError("internal",
                        "could not locate the envelope for %r after creating it"
                        % bounds.get("name"))
    envelope_path = "%s.automation_envelopes.%d" % (clip_ref["clip_path"], index)
    ops = [{"op": "call", "path": envelope_path, "method": "insert_step",
            "args": [t, span, value]} for t, span, value in steps]
    await _run_atomic(bridge, ops, "automate_parameter")
    # Probe at step MIDPOINTS: Live's value_at_time on an exact step boundary
    # reports the step ending there (and at beat 0, with nothing before it,
    # the parameter's static value) — an artifact of sampling, not of the data.
    probes = [steps[0], steps[len(steps) // 2], steps[-1]]
    verified = []
    for start, span, expected in probes:
        at = start + span / 2.0
        result = await bridge.request("call", path=envelope_path,
                                      method="value_at_time", args=[at])
        verified.append({"time": at, "value": result.get("value"),
                         "wrote": expected})
    # Whether the shape survived is answered by the read-back, not by
    # is_quantized: that flag means "has named discrete values" (Operator's
    # Algorithm), while others round silently anyway (Transpose, in
    # semitones over -48..48). Verified 2026-08-03.
    snapped = any(abs(probe["value"] - probe["wrote"]) > 1e-6
                  for probe in verified if probe["value"] is not None)
    out = {"parameter": bounds.get("name"), "device": device_ref["path"],
           "envelope": envelope_path, "steps": len(steps),
           "range": {"min": lo, "max": hi}, "mode": mode,
           "quantized": bool(bounds.get("is_quantized")),
           "snapped": snapped,
           "resolution": resolution, "read_back": verified}
    if snapped:
        out["note"] = ("this parameter does not take every value in its "
                       "range, so the shape landed on the nearest ones it "
                       "accepts — read_back is what Live kept")
    return out


async def clear_automation(session, clip, device=None, parameter=None):
    bridge = session.bridge
    clip_ref = await resolve.resolve_clip(bridge, clip)
    if device is None and parameter is None:
        await _run_atomic(bridge, [{"op": "call", "path": clip_ref["clip_path"],
                                    "method": "clear_all_envelopes"}],
                          "clear_automation")
        return {"cleared": "all"}
    device_ref = await resolve.resolve_device(bridge, clip_ref["track_path"],
                                              device)
    param_ref = await resolve.resolve_parameter(bridge, device_ref["path"],
                                                parameter)
    await _run_atomic(bridge, [{"op": "call", "path": clip_ref["clip_path"],
                                "method": "clear_envelope",
                                "args": [{"$obj": {"path": param_ref["path"]}}]}],
                      "clear_automation")
    return {"cleared": param_ref["path"]}


# --- watches -----------------------------------------------------------------------


async def watch(session, path, props):
    if not isinstance(props, list) or not props:
        raise ToolError("invalid_argument", "props must be a non-empty list")
    described = await session.bridge.request("describe", path=path)
    class_name = described.get("class")
    for prop in props:
        known = inventory.has_listener(class_name, prop)
        if known is False:
            raise ToolError("not_listenable",
                            "%s.%s has no listener" % (class_name, prop),
                            hint="listenable props are in docs/lom-inventory.md")
    result = await session.bridge.request("subscribe", path=path, props=props)
    _reconcile_connection(session)   # after connecting, so the epoch is current
    session.watches[result["sub"]] = {"path": path, "props": props}
    return {"watch_id": result["sub"], "current_values": result["values"],
            "note": "changes accumulate server-side; pull them with get_changes"}


async def unwatch(session, watch_id):
    dropped = _reconcile_connection(session)
    if watch_id in dropped:
        return {"unwatched": watch_id, "note": "it had already died with the "
                                               "previous connection to Live"}
    await session.bridge.request("unsubscribe", sub=watch_id)
    session.watches.pop(watch_id, None)
    return {"unwatched": watch_id}


# --- arrangement (v1 parity) ----------------------------------------------------------


async def show_view(session, view):
    names = {"session": "Session", "arrangement": "Arranger"}
    if view not in names:
        raise ToolError("invalid_argument", "view must be session|arrangement")
    await session.bridge.request("call", path="app.view", method="show_view",
                                 args=[names[view]])
    return {"view": view}


async def list_arrangement_clips(session, track=None):
    bridge = session.bridge
    if track is not None:
        refs = [await resolve.resolve_track(bridge, track)]
    else:
        count = await resolve.vec_len(bridge, "song", "tracks")
        refs = [{"index": i, "path": "song.tracks.%d" % i} for i in range(count)]
    lengths = await _gets(bridge, [(ref["path"], ["arrangement_clips", "name"])
                                   for ref in refs])
    specs = []
    owners = []
    for ref, values in zip(refs, lengths):
        values = values or {}
        for i in range(_vec_length(values.get("arrangement_clips"))):
            specs.append(("%s.arrangement_clips.%d" % (ref["path"], i),
                          ["name", "start_time", "end_time", "length",
                           "muted", "color"]))
            owners.append((ref["index"], values.get("name")))
    clip_values = await _gets(bridge, specs)
    clips = []
    for (track_index, track_name), values in zip(owners, clip_values):
        values = values or {}
        clips.append({"track": track_index, "track_name": track_name,
                      "name": values.get("name"),
                      "start": values.get("start_time"),
                      "end": values.get("end_time"),
                      "length": values.get("length"),
                      "muted": values.get("muted"),
                      "color": colors.to_hex(values.get("color"))})
    clips.sort(key=lambda c: (c["start"] if c["start"] is not None else 0,
                              c["track"]))
    return {"clips": clips, "count": len(clips)}


async def duplicate_clip_to_arrangement(session, clip, time):
    ref = await resolve.resolve_clip(session.bridge, clip)
    position = _require_number(time, "time", lo=0)
    result = await session.bridge.request(
        "call", path=ref["track_path"], method="duplicate_clip_to_arrangement",
        args=[{"$obj": {"path": ref["clip_path"]}}, position])
    return {"placed_at": position, "track": ref["track_index"],
            "result": result.get("value"),
            "note": "see list_arrangement_clips to confirm"}


async def _new_arrangement_clip_ref(bridge, track_path, at):
    """Find the clip a create_* call just placed.

    Live returns the new Clip, but the bridge cannot canonicalize an
    Arrangement clip into a path (it has no clip_slot), so it comes back as a
    path-less stub. Matching by start_time is exact: a track cannot hold two
    Arrangement clips starting at the same beat.
    """
    clips = await resolve.arrangement_clips(bridge, track_path)
    for index, start, end in clips:
        if start is not None and abs(start - at) < 1e-6:
            return {"arrangement_index": index, "start": start, "end": end,
                    "clip_path": "%s.arrangement_clips.%d" % (track_path, index)}
    raise ToolError("internal",
                    "created the clip but could not find it at beat %g" % at)


async def create_arrangement_clip(session, track, time, length, name,
                                  color=None, notes=None,
                                  signature_numerator=None,
                                  signature_denominator=None):
    """Native Arrangement MIDI clip. Times are song-absolute beats."""
    bridge = session.bridge
    track_ref = await resolve.resolve_track(bridge, track)
    at = _require_number(time, "time", lo=0.0, hi=1576800.0)
    length = _require_number(length, "length")
    if length <= 0:
        raise ToolError("invalid_argument", "length must be > 0 beats")
    values = await _gets(bridge, [(track_ref["path"], ["has_midi_input"])])
    if not (values[0] or {}).get("has_midi_input"):
        raise ToolError("invalid_argument",
                        "track %d is not a MIDI track" % track_ref["index"],
                        hint="use import_audio_clip for audio tracks")
    existing = await resolve.arrangement_clips(bridge, track_ref["path"])
    for _index, start, end in existing:
        if start is None or end is None:
            continue
        if start < at + length and at < end:
            raise ToolError("conflict",
                            "an Arrangement clip already occupies %g-%g on "
                            "track %d" % (start, end, track_ref["index"]),
                            hint="Live would trim or replace it; delete_clip "
                                 "first or choose a free span")
    await _run_atomic(bridge, [{"op": "call", "path": track_ref["path"],
                                "method": "create_midi_clip",
                                "args": [at, length]}],
                      "create_arrangement_clip")
    ref = await _new_arrangement_clip_ref(bridge, track_ref["path"], at)
    ops = []
    props = {"name": str(name)}
    if color is not None:
        props["color"] = colors.to_int(color)
    if signature_numerator:
        props["signature_numerator"] = int(signature_numerator)
    if signature_denominator:
        props["signature_denominator"] = int(signature_denominator)
    ops.append({"op": "set", "path": ref["clip_path"], "props": props})
    added_ids = []
    if notes:
        ops.append({"op": "edit_notes", "path": ref["clip_path"],
                    "add": _validate_notes(notes, "notes")})
    results = await _run_atomic(bridge, ops, "create_arrangement_clip")
    if notes:
        added_ids = (results[-1].get("result") or {}).get("added_ids", [])
    read_back = await _gets(bridge, [(ref["clip_path"],
                                      ["name", "color", "start_time",
                                       "end_time", "length"])])
    read_back = read_back[0] or {}
    return {"clip": {"track": track_ref["index"], "view": "arrangement",
                     "arrangement": ref["arrangement_index"],
                     "name": read_back.get("name"),
                     "color": colors.to_hex(read_back.get("color")),
                     "start": read_back.get("start_time"),
                     "end": read_back.get("end_time"),
                     "length": read_back.get("length")},
            "added_note_ids": added_ids}


async def import_audio_clip(session, track, file_path, time=None, slot=None,
                            name=None, color=None):
    """Import an audio file into an Arrangement position or a Session slot."""
    bridge = session.bridge
    path = files.validate_audio_path(file_path)
    track_ref = await resolve.resolve_track(bridge, track)
    values = await _gets(bridge, [(track_ref["path"], ["has_audio_input"])])
    if not (values[0] or {}).get("has_audio_input"):
        raise ToolError("invalid_argument",
                        "track %d is not an audio track" % track_ref["index"],
                        hint="create_audio_track first — Live refuses audio "
                             "clips on MIDI tracks")
    if (time is None) == (slot is None):
        raise ToolError("invalid_argument",
                        "give exactly one of 'time' (Arrangement) or 'slot' "
                        "(Session)")
    if slot is not None:
        slot_ref = await resolve.resolve_slot(bridge, track, slot)
        if slot_ref["has_clip"]:
            raise ToolError("conflict",
                            "track %d slot %d already holds a clip"
                            % (slot_ref["track_index"], slot_ref["slot"]),
                            hint="delete_clip first, or choose another slot")
        await _run_atomic(bridge, [{"op": "call", "path": slot_ref["slot_path"],
                                    "method": "create_audio_clip",
                                    "args": [path]}], "import_audio_clip")
        clip_path = slot_ref["slot_path"] + ".clip"
        located = {"view": "session", "slot": slot_ref["slot"]}
    else:
        at = _require_number(time, "time", lo=0.0, hi=1576800.0)
        await _run_atomic(bridge, [{"op": "call", "path": track_ref["path"],
                                    "method": "create_audio_clip",
                                    "args": [path, at]}], "import_audio_clip")
        ref = await _new_arrangement_clip_ref(bridge, track_ref["path"], at)
        clip_path = ref["clip_path"]
        located = {"view": "arrangement", "arrangement": ref["arrangement_index"]}
    props = {}
    if name is not None:
        props["name"] = str(name)
    if color is not None:
        props["color"] = colors.to_int(color)
    if props:
        await _run_atomic(bridge, [{"op": "set", "path": clip_path,
                                    "props": props}], "import_audio_clip")
    read_back = await _gets(bridge, [(clip_path, ["name", "color", "length",
                                                  "file_path", "warping",
                                                  "start_time"])])
    read_back = read_back[0] or {}
    clip = {"track": track_ref["index"], "name": read_back.get("name"),
            "color": colors.to_hex(read_back.get("color")),
            "length": read_back.get("length"),
            "file": read_back.get("file_path"),
            "warping": read_back.get("warping")}
    clip.update(located)
    if located["view"] == "arrangement":
        clip["start"] = read_back.get("start_time")
    return {"clip": clip, "imported": path}


async def set_arrangement_clip(session, clip, **params):
    """Name, colour, mute and content trim (start/end markers) of a clip.

    Arrangement position itself is read-only in the LOM: Clip.start_time and
    end_time have no setter. To move a clip, delete and recreate it, or use
    duplicate_clip_to_arrangement at the new time.
    """
    ref = await resolve.resolve_clip(session.bridge, clip)
    props = {}
    if "name" in params:
        props["name"] = str(params["name"])
    if "color" in params:
        props["color"] = colors.to_int(params["color"])
    for key in ("muted", "start_marker", "end_marker", "looping",
                "loop_start", "loop_end"):
        if key in params:
            props[key] = params[key]
    if not props:
        raise ToolError("invalid_argument", "set_arrangement_clip: nothing to set",
                        hint="settable: name, color, muted, start_marker, "
                             "end_marker, looping, loop_start, loop_end")
    results = await _run_atomic(session.bridge,
                                [{"op": "set", "path": ref["clip_path"],
                                  "props": props}], "set_arrangement_clip")
    values = (results[0].get("result") or {}).get("values", {})
    if "color" in values:
        values["color"] = colors.to_hex(values["color"])
    return {"values": values, "view": ref.get("view")}


async def delete_arrangement_clip(session, clip):
    ref = await resolve.resolve_clip(session.bridge, clip)
    if ref.get("view") != "arrangement":
        raise ToolError("invalid_argument",
                        "that locator points at a Session clip",
                        hint="use delete_clip for Session slots")
    await _run_atomic(session.bridge,
                      [{"op": "call", "path": ref["track_path"],
                        "method": "delete_clip",
                        "args": [{"$obj": {"path": ref["clip_path"]}}]}],
                      "delete_arrangement_clip")
    return {"deleted": {"track": ref["track_index"],
                        "arrangement": ref["arrangement_index"],
                        "start": ref["start"]}}


# --- structure made visible -------------------------------------------------------------


async def create_reference_clip(session, track, slot, length, name,
                                color=None, segments=None, pulses=None,
                                accents=None):
    length = _require_number(length, "length")
    notes = []
    segments = segments or []
    labels = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or "start" not in segment:
            raise ToolError("invalid_argument",
                            "segments are {start, label?, duration?}")
        start = _require_number(segment["start"], "segments[%d].start" % index,
                                lo=0)
        if "duration" in segment:
            duration = _require_number(segment["duration"],
                                       "segments[%d].duration" % index)
        else:
            next_start = (segments[index + 1]["start"]
                          if index + 1 < len(segments) else length)
            duration = max(float(next_start) - start, 0.05)
        notes.append({"pitch": REFERENCE_PITCHES["segments"], "start": start,
                      "duration": duration, "velocity": 64})
        if segment.get("label"):
            labels.append("%g:%s" % (start, segment["label"]))
    for start in pulses or []:
        notes.append({"pitch": REFERENCE_PITCHES["pulses"],
                      "start": _require_number(start, "pulse", lo=0),
                      "duration": 0.1, "velocity": 70})
    for start in accents or []:
        notes.append({"pitch": REFERENCE_PITCHES["accents"],
                      "start": _require_number(start, "accent", lo=0),
                      "duration": 0.1, "velocity": 127})
    if not notes:
        raise ToolError("invalid_argument",
                        "give at least one of segments/pulses/accents")
    clip_name = name if not labels else "%s [%s]" % (name, " ".join(labels))
    result = await create_clip(session, track=track, slot=slot, length=length,
                               name=clip_name, color=color, notes=notes)
    result["lanes"] = {"segments": REFERENCE_PITCHES["segments"],
                       "pulses": REFERENCE_PITCHES["pulses"],
                       "accents": REFERENCE_PITCHES["accents"]}
    result["hint"] = "mute the reference track; it is for eyes, not ears"
    return result


# --- cross-tool atomicity -----------------------------------------------------------------


async def song_batch(session, calls, stop_on_error=True):
    if not isinstance(calls, list) or not calls:
        raise ToolError("invalid_argument", "calls must be a non-empty list")
    compiled = []
    all_ops = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict) or "tool" not in call:
            raise ToolError("invalid_argument",
                            "calls[%d] must be {tool, params}" % index)
        tool = call["tool"]
        compiler = BATCHABLE_TOOLS.get(tool)
        if compiler is None:
            raise ToolError("invalid_argument",
                            "calls[%d]: %r cannot run inside song_batch" %
                            (index, tool),
                            hint="batchable: %s"
                                 % ", ".join(sorted(BATCHABLE_TOOLS)))
        try:
            ops = await compiler(session, call.get("params") or {})
        except ToolError as exc:
            hint = "nothing was executed; fix the call and retry"
            if exc.hint:
                hint = "%s — %s" % (exc.hint, hint)
            raise ToolError(exc.code,
                            "calls[%d] (%s) failed to compile: %s"
                            % (index, tool, exc.message),
                            hint=hint)
        compiled.append((tool, len(ops)))
        all_ops.extend(ops)
    if len(all_ops) > 256:
        raise ToolError("too_large",
                        "song_batch compiles to %d wire ops (max 256)"
                        % len(all_ops),
                        hint="split into two song_batch calls")
    result = await session.bridge.request("batch", ops=all_ops,
                                          stop_on_error=stop_on_error)
    per_call = []
    cursor = 0
    for tool, span in compiled:
        subs = result["results"][cursor:cursor + span]
        cursor += span
        failed = next((s for s in subs if s.get("ok") is False), None)
        if failed:
            per_call.append({"tool": tool, "ok": False,
                             "error": failed["error"]})
        elif any(s.get("skipped") for s in subs):
            per_call.append({"tool": tool, "ok": False, "skipped": True})
        else:
            per_call.append({"tool": tool, "ok": True})
    out = {"calls": per_call, "rolled_back": result.get("rolled_back", False),
           "atomic": True}
    if result.get("undo_hint"):
        out["undo_hint"] = result["undo_hint"]
    return out
