"""Locator resolution: names or indices at the tool boundary, paths on the wire.

All name intelligence lives here, server-side; the bridge only understands
paths (CONTRACT A.5). Every miss produces a structured error with an
actionable hint.
"""

import contextvars

from .errors import ToolError

# Guards collected by the CURRENT tool call: identities of every object this
# call resolved from a name, consumed by the call's write. A context variable,
# not server state, because two tool calls can be in flight at once — each
# task gets its own copy, so one call's guards can neither block another's
# write nor vanish from its own. Reviewed 2026-08-05: as shared bridge state,
# a guard left by a read-only call blocked the next unrelated write, and a
# parallel call could consume a write's guard out from under it.
_CALL_GUARDS = contextvars.ContextVar("alberton_call_guards")


def begin_call():
    """Start a fresh guard scope. The MCP server calls this once per tool
    call; anything resolved before the call's write is guarded, anything from
    earlier calls is gone."""
    _CALL_GUARDS.set([])


def current_guards():
    try:
        return _CALL_GUARDS.get()
    except LookupError:
        fresh = []
        _CALL_GUARDS.set(fresh)
        return fresh


def as_index(spec):
    """Digit strings count as indices: MCP clients stringify untyped params,
    so a caller's `track: 0` can arrive as "0". Names always win — this is
    only reached after an exact-name lookup has failed."""
    if isinstance(spec, str) and spec.strip().isdigit():
        return int(spec.strip())
    return spec


def note_guard(ref):
    """Remember a name-resolved object's identity for the write that follows.

    An identity is a number. If the bridge could not read one -- an older
    script, an object that has none -- there is nothing to compare against,
    and a guard that cannot fail is worse than no guard: it reads as proof.
    """
    guards = current_guards()
    if isinstance(ref.get("ptr"), int) and ref not in guards:
        guards.append(ref)
    return ref


async def vec_len(bridge, path, prop):
    result = await bridge.request("get", path=path, props=[prop])
    value = result["values"].get(prop)
    if not isinstance(value, dict) or "$vec" not in value:
        raise ToolError("internal", "%s.%s is not a vector: %r" % (path, prop, value))
    return value["$vec"]["len"]


async def names_of(bridge, base, count, with_ptr=False):
    """The names in a vector, and optionally each object's identity.

    `_live_ptr` rides along in the same batch, so it is free. It is what lets a
    caller notice that the thing it named has moved out from under the index it
    resolved to — see `identity_of`.
    """
    if count == 0:
        return ([], []) if with_ptr else []
    props = ["name", "_live_ptr"] if with_ptr else ["name"]
    ops = [{"op": "get", "path": "%s.%d" % (base, i), "props": props}
           for i in range(count)]
    result = await bridge.request("batch", ops=ops, stop_on_error=False)
    names, ptrs = [], []
    for sub in result["results"]:
        values = sub["result"]["values"] if sub.get("ok") else {}
        names.append(values.get("name"))
        ptrs.append(values.get("_live_ptr"))
    return (names, ptrs) if with_ptr else names


async def _resolve_indexed(bridge, spec, base_path, vec_prop, kind, parent="song"):
    count = await vec_len(bridge, parent, vec_prop) if base_path is None else None
    base = base_path or ("%s.%s" % (parent, vec_prop))
    if count is None:
        # base_path given explicitly: derive count from its parent property
        parent_path, prop = base.rsplit(".", 1)
        count = await vec_len(bridge, parent_path, prop)
    if isinstance(spec, bool):
        raise ToolError("invalid_argument", "%s locator cannot be a boolean" % kind)
    if isinstance(spec, int):
        if not 0 <= spec < count:
            raise ToolError("not_found", "%s %d out of range" % (kind, spec),
                            hint="there are %d %ss (indices 0–%d)"
                                 % (count, kind, count - 1))
        return note_guard({"index": spec, "path": "%s.%d" % (base, spec)})
    if isinstance(spec, str):
        names, ptrs = await names_of(bridge, base, count, with_ptr=True)
        matches = [i for i, name in enumerate(names) if name == spec]
        if len(matches) == 1:
            # Carry the identity of the object we matched. The index is only
            # true for as long as nobody in front of it is added or removed,
            # and a human editing in Live does that in a fifth of a second.
            return note_guard({
                "index": matches[0], "path": "%s.%d" % (base, matches[0]),
                "name": spec, "ptr": ptrs[matches[0]]})
        if not matches:
            index = as_index(spec)
            if isinstance(index, int) and 0 <= index < count:
                return note_guard({"index": index,
                                   "path": "%s.%d" % (base, index)})
            listing = ", ".join("%d:%r" % (i, n) for i, n in enumerate(names))
            raise ToolError("not_found", "no %s named %r" % (kind, spec),
                            hint="available: %s" % (listing or "none"))
        raise ToolError("ambiguous_name",
                        "%d %ss are named %r" % (len(matches), kind, spec),
                        hint="use an index instead: %s" % matches)
    raise ToolError("invalid_argument",
                    "%s locator must be an int index or exact name" % kind)


MASTER_NAMES = ("master", "main")


async def _one(bridge, path, prop):
    result = await bridge.request("get", path=path, props=[prop])
    value = result["values"].get(prop)
    return None if isinstance(value, dict) else value


def _master_ref():
    return {"kind": "master", "index": None, "path": "song.master_track"}


async def resolve_track(bridge, track):
    """Any track: a regular one, a return, or the master.

    An integer always means a regular track — all three families count from
    zero, so a bare index could not say which. Returns and the master answer
    to their own name, or to the explicit forms "return:0", "return:A-Reverb"
    and "master". Live 12.4.3 calls the master track "Main".
    """
    if isinstance(track, str):
        text = track.strip()
        if text.lower() in MASTER_NAMES:
            return _master_ref()
        if text.lower().startswith("return:"):
            ref = await _resolve_indexed(bridge, as_index(text.split(":", 1)[1].strip()),
                                         None, "return_tracks", "return track")
            ref["kind"] = "return"
            return ref
    try:
        ref = await _resolve_indexed(bridge, track, None, "tracks", "track")
        ref["kind"] = "track"
        return ref
    except ToolError as exc:
        if exc.code != "not_found" or not isinstance(track, str):
            raise
        regular = exc.hint
    # Not a regular track: it may be a return or the master under its own name.
    count = await vec_len(bridge, "song", "return_tracks")
    names = await names_of(bridge, "song.return_tracks", count)
    matches = [i for i, name in enumerate(names) if name == track]
    if len(matches) == 1:
        return {"kind": "return", "index": matches[0], "name": track,
                "path": "song.return_tracks.%d" % matches[0]}
    if len(matches) > 1:
        raise ToolError("ambiguous_name",
                        "%d return tracks are named %r" % (len(matches), track),
                        hint="use return:%d or another index" % matches[0])
    master_name = await _one(bridge, "song.master_track", "name")
    if master_name == track:
        ref = _master_ref()
        ref["name"] = master_name
        return ref
    raise ToolError("not_found", "no track named %r" % track,
                    hint="%s; returns: %s; master: %r (or say 'master')"
                         % (regular,
                            ", ".join("%d:%r" % (i, n)
                                      for i, n in enumerate(names)) or "none",
                            master_name))


async def resolve_return_track(bridge, spec):
    return await _resolve_indexed(bridge, spec, None, "return_tracks", "return track")


async def resolve_scene(bridge, scene):
    return await _resolve_indexed(bridge, scene, None, "scenes", "scene")


async def resolve_device(bridge, track_path, device):
    """A device on a track, or one nested inside a rack.

    `0` or `"Bass Raw"` addresses a top-level device. A slash-separated path
    descends into racks, alternating device and chain:
    `"Bass Raw/0/Operator"` is the Operator inside chain 0 of the Bass Raw
    rack. Every segment may be an index or an exact name.
    """
    segments = ([s.strip() for s in device.split("/")]
                if isinstance(device, str) and "/" in device else [device])
    ref = None
    base = track_path + ".devices"
    for depth, segment in enumerate(segments):
        kind = "device" if depth % 2 == 0 else "chain"
        ref = await _resolve_indexed(bridge, segment, base, None, kind)
        base = ref["path"] + (".chains" if depth % 2 == 0 else ".devices")
    ref["depth"] = len(segments)
    return ref


async def resolve_parameter(bridge, device_path, parameter):
    return await _resolve_indexed(bridge, parameter, device_path + ".parameters",
                                  None, "parameter")


async def resolve_slot(bridge, track, slot):
    """-> dict with track index, slot index, slot path, has_clip."""
    track_ref = await resolve_track(bridge, track)
    slot_count = await vec_len(bridge, track_ref["path"], "clip_slots")
    slot = as_index(slot)
    if slot_count == 0:
        raise ToolError("invalid_argument",
                        "the %s track holds no clips"
                        % (track_ref["kind"] if track_ref["kind"] != "track"
                           else "group"),
                        hint="return and master tracks have no Session slots; "
                             "route audio to them instead")
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < slot_count:
        raise ToolError("not_found", "slot %r out of range" % (slot,),
                        hint="track %s has %d slots (scenes 0–%d)"
                             % (track_ref["index"], slot_count, slot_count - 1))
    slot_path = "%s.clip_slots.%d" % (track_ref["path"], slot)
    result = await bridge.request("get", path=slot_path, props=["has_clip"])
    has_clip = result["values"].get("has_clip")
    return {"track_index": track_ref["index"], "slot": slot,
            "track_path": track_ref["path"], "slot_path": slot_path,
            "has_clip": bool(has_clip) if isinstance(has_clip, bool) else False}


async def arrangement_clips(bridge, track_path):
    """[(index, start, end)] for a track's Arrangement clips, in time order."""
    count = await vec_len(bridge, track_path, "arrangement_clips")
    if count == 0:
        return []
    ops = [{"op": "get", "path": "%s.arrangement_clips.%d" % (track_path, i),
            "props": ["start_time", "end_time"]} for i in range(count)]
    result = await bridge.request("batch", ops=ops, stop_on_error=False)
    found = []
    for index, sub in enumerate(result["results"]):
        if not sub.get("ok"):
            continue
        values = sub["result"]["values"]
        found.append((index, values.get("start_time"), values.get("end_time")))
    return sorted(found, key=lambda entry: (entry[1] is None, entry[1]))


async def resolve_arrangement_clip(bridge, track, time=None, index=None):
    track_ref = await resolve_track(bridge, track)
    clips = await arrangement_clips(bridge, track_ref["path"])
    if not clips:
        raise ToolError("not_found",
                        "track %d has no Arrangement clips" % track_ref["index"],
                        hint="create_arrangement_clip or "
                             "duplicate_clip_to_arrangement first")
    if index is not None:
        index = as_index(index)
        ordered = [entry[0] for entry in clips]
        if not isinstance(index, int) or not 0 <= index < len(ordered):
            raise ToolError("not_found", "arrangement index %r out of range"
                            % (index,),
                            hint="track %d has %d Arrangement clips"
                                 % (track_ref["index"], len(clips)))
        chosen = clips[index]
    else:
        at = float(time)
        inside = [e for e in clips
                  if e[1] is not None and e[1] <= at < (e[2] if e[2] is not None
                                                        else e[1])]
        if inside:
            chosen = inside[0]
        else:
            near = [e for e in clips
                    if e[1] is not None and abs(e[1] - at) < 1e-6]
            if not near:
                listing = ", ".join("%g-%g" % (e[1], e[2]) for e in clips
                                    if e[1] is not None)
                raise ToolError("not_found",
                                "no Arrangement clip at beat %g on track %d"
                                % (at, track_ref["index"]),
                                hint="clips occupy: %s" % (listing or "none"))
            chosen = near[0]
    return {"track_index": track_ref["index"], "track_path": track_ref["path"],
            "arrangement_index": chosen[0], "start": chosen[1], "end": chosen[2],
            "clip_path": "%s.arrangement_clips.%d" % (track_ref["path"],
                                                      chosen[0]),
            "view": "arrangement"}


async def resolve_clip(bridge, clip):
    """Polymorphic clip locator.

    {"track": t, "slot": n}        -> a Session clip
    {"track": t, "time": beats}    -> the Arrangement clip at/containing a beat
    {"track": t, "arrangement": n} -> an Arrangement clip by time order
    """
    if not isinstance(clip, dict) or "track" not in clip:
        raise ToolError("invalid_argument",
                        "clip locator needs a track plus one of: slot "
                        "(Session), time or arrangement (Arrangement)")
    if "arrangement" in clip:
        return await resolve_arrangement_clip(bridge, clip["track"],
                                              index=clip["arrangement"])
    if "time" in clip and "slot" not in clip:
        return await resolve_arrangement_clip(bridge, clip["track"],
                                              time=clip["time"])
    if "slot" not in clip:
        raise ToolError("invalid_argument",
                        "clip locator needs 'slot' (Session) or 'time'/"
                        "'arrangement' (Arrangement)")
    ref = await resolve_slot(bridge, clip["track"], clip["slot"])
    if not ref["has_clip"]:
        raise ToolError("not_found",
                        "no clip at track %d slot %d"
                        % (ref["track_index"], ref["slot"]),
                        hint="the slot is empty — create_clip first, or check "
                             "session_overview")
    ref["clip_path"] = ref["slot_path"] + ".clip"
    ref["view"] = "session"
    return ref
