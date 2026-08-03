"""Locator resolution: names or indices at the tool boundary, paths on the wire.

All name intelligence lives here, server-side; the bridge only understands
paths (CONTRACT A.5). Every miss produces a structured error with an
actionable hint.
"""

from .errors import ToolError


async def vec_len(bridge, path, prop):
    result = await bridge.request("get", path=path, props=[prop])
    value = result["values"].get(prop)
    if not isinstance(value, dict) or "$vec" not in value:
        raise ToolError("internal", "%s.%s is not a vector: %r" % (path, prop, value))
    return value["$vec"]["len"]


async def names_of(bridge, base, count):
    if count == 0:
        return []
    ops = [{"op": "get", "path": "%s.%d" % (base, i), "props": ["name"]}
           for i in range(count)]
    result = await bridge.request("batch", ops=ops, stop_on_error=False)
    names = []
    for sub in result["results"]:
        if sub.get("ok"):
            names.append(sub["result"]["values"].get("name"))
        else:
            names.append(None)
    return names


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
        return {"index": spec, "path": "%s.%d" % (base, spec)}
    if isinstance(spec, str):
        names = await names_of(bridge, base, count)
        matches = [i for i, name in enumerate(names) if name == spec]
        if len(matches) == 1:
            return {"index": matches[0], "path": "%s.%d" % (base, matches[0]),
                    "name": spec}
        if not matches:
            listing = ", ".join("%d:%r" % (i, n) for i, n in enumerate(names))
            raise ToolError("not_found", "no %s named %r" % (kind, spec),
                            hint="available: %s" % (listing or "none"))
        raise ToolError("ambiguous_name",
                        "%d %ss are named %r" % (len(matches), kind, spec),
                        hint="use an index instead: %s" % matches)
    raise ToolError("invalid_argument",
                    "%s locator must be an int index or exact name" % kind)


async def resolve_track(bridge, track):
    return await _resolve_indexed(bridge, track, None, "tracks", "track")


async def resolve_return_track(bridge, spec):
    return await _resolve_indexed(bridge, spec, None, "return_tracks", "return track")


async def resolve_scene(bridge, scene):
    return await _resolve_indexed(bridge, scene, None, "scenes", "scene")


async def resolve_device(bridge, track_path, device):
    return await _resolve_indexed(bridge, device, track_path + ".devices",
                                  None, "device")


async def resolve_parameter(bridge, device_path, parameter):
    return await _resolve_indexed(bridge, parameter, device_path + ".parameters",
                                  None, "parameter")


async def resolve_slot(bridge, track, slot):
    """-> dict with track index, slot index, slot path, has_clip."""
    track_ref = await resolve_track(bridge, track)
    slot_count = await vec_len(bridge, track_ref["path"], "clip_slots")
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < slot_count:
        raise ToolError("not_found", "slot %r out of range" % (slot,),
                        hint="track %d has %d slots (scenes 0–%d)"
                             % (track_ref["index"], slot_count, slot_count - 1))
    slot_path = "%s.clip_slots.%d" % (track_ref["path"], slot)
    result = await bridge.request("get", path=slot_path, props=["has_clip"])
    has_clip = result["values"].get("has_clip")
    return {"track_index": track_ref["index"], "slot": slot,
            "track_path": track_ref["path"], "slot_path": slot_path,
            "has_clip": bool(has_clip) if isinstance(has_clip, bool) else False}


async def resolve_clip(bridge, clip):
    """clip is {"track": int|str, "slot": int}; requires the slot to hold a clip."""
    if not isinstance(clip, dict) or "track" not in clip or "slot" not in clip:
        raise ToolError("invalid_argument",
                        "clip locator must be {\"track\": int|name, \"slot\": int}")
    ref = await resolve_slot(bridge, clip["track"], clip["slot"])
    if not ref["has_clip"]:
        raise ToolError("not_found",
                        "no clip at track %d slot %d"
                        % (ref["track_index"], ref["slot"]),
                        hint="the slot is empty — create_clip first, or check "
                             "session_overview")
    ref["clip_path"] = ref["slot_path"] + ".clip"
    return ref
