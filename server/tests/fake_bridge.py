"""An in-process fake of the Alberton bridge, speaking CONTRACT Layer A.

Runs a real asyncio TCP server on an ephemeral port, so tests exercise the
actual Bridge client (framing, correlation, events) against a miniature LOM
held in dicts. Rollback is simulated with a deep-copy snapshot, which matches
the observable contract (atomic-or-absent) without ticks.
"""

import asyncio
import copy
import json


_next_ptr = [1000]


def _param(name, value=0.0, lo=0.0, hi=1.0, display=0.0,
           quantized=False, enabled=True):
    # is_quantized and is_enabled are what a caller needs before writing: a
    # stepped parameter snaps, and a macro-mapped one ignores the write.
    _next_ptr[0] += 1
    return {"__class__": "DeviceParameter", "name": name, "value": value,
            "min": lo, "max": hi, "display_value": display,
            "is_quantized": quantized, "is_enabled": enabled,
            "_live_ptr": _next_ptr[0]}


def _track(name, midi=True, slots=4):
    return {
        "__class__": "Track", "name": name, "color": 0x808080,
        "has_midi_input": midi, "has_audio_input": not midi,
        "is_foldable": False, "is_frozen": False, "is_grouped": False,
        "mute": False, "solo": False, "arm": False,
        "can_be_armed": True, "playing_slot_index": -1,
        "clip_slots": [{"__class__": "ClipSlot", "has_clip": False,
                        "clip": None} for _ in range(slots)],
        "devices": [],
        "arrangement_clips": [],
        "mixer_device": {
            "__class__": "MixerDevice",
            "volume": _param("Volume", 0.85),
            "panning": _param("Pan", 0.0, lo=-1.0, hi=1.0),
            "sends": [_param("A", 0.0, display=-70.0),
                      _param("B", 0.0, display=-70.0)],
        },
    }


def _arrangement_clip(name, at, length, color=0x333333, midi=True, notes=None):
    return {"__class__": "Clip", "name": name, "color": color,
            "start_time": float(at), "end_time": float(at) + float(length),
            "length": float(length), "muted": False,
            "start_marker": 0.0, "end_marker": float(length),
            "looping": False, "loop_start": 0.0, "loop_end": float(length),
            "signature_numerator": 4, "signature_denominator": 4,
            "is_midi_clip": midi, "is_playing": False,
            "is_arrangement_clip": True, "notes": notes or [],
            "automation_envelopes": [], "has_envelopes": False}


def _browser_item(name, uri=None, children=None):
    return {"__class__": "BrowserItem", "name": name,
            "uri": uri or ("query:%s" % name),
            "is_loadable": children is None, "is_folder": children is not None,
            "children": children or []}


READ_ONLY = {
    ("Application", "average_process_usage"),
    ("Track", "has_midi_input"), ("Track", "has_audio_input"),
    ("Track", "can_be_armed"), ("Track", "playing_slot_index"),
    ("ClipSlot", "has_clip"), ("Clip", "is_midi_clip"),
    ("Clip", "is_playing"), ("Clip", "length"),
    # Arrangement position has no setter in Live — a clip cannot be moved
    ("Clip", "start_time"), ("Clip", "end_time"),
    ("Clip", "is_arrangement_clip"), ("Clip", "file_path"),
    ("Song", "is_playing"),
    ("DeviceParameter", "min"), ("DeviceParameter", "max"),
    ("DeviceParameter", "name"),
}

INTERNAL_KEYS = ("__class__",)


class FakeLive:
    def __init__(self):
        self.song = {
            "__class__": "Song", "tempo": 120.0, "is_playing": False,
            "tempo_follower_enabled": False, "current_song_time": 0.0, "signature_numerator": 4,
            "signature_denominator": 4, "scale_name": "Major", "root_note": 0,
            "scale_mode": False, "groove_amount": 0.0, "metronome": False,
            "tracks": [_track("Lead"), _track("Bass"),
                       _track("Loops", midi=False)],
            "return_tracks": [_track("Reverb Return", midi=False),
                              _track("Delay Return", midi=False)],
            "master_track": _track("Main", midi=False, slots=0),
            "scenes": [{"__class__": "Scene", "name": "Scene %d" % i,
                        "color": 0x333333} for i in range(4)],
            "view": {"__class__": "View", "selected_track": None},
        }
        self.app = {
            "__class__": "Application", "average_process_usage": 1.5,
            "view": {"__class__": "View"},
            "browser": {
                "__class__": "Browser",
                "instruments": [
                    _browser_item("Synths", children=[
                        _browser_item("FakeSynth"),
                        _browser_item("FakePad"),
                    ]),
                    _browser_item("FakePiano"),
                ],
                "sounds": [], "drums": [], "audio_effects": [],
                "midi_effects": [], "plugins": [], "samples": [],
                "packs": [], "user_library": [], "max_for_live": [],
            },
        }
        self.next_note_id = 100
        self.view_log = []

    # --- path handling ------------------------------------------------------

    def resolve(self, path):
        parts = path.split(".")
        if parts[0] == "song":
            node = self.song
        elif parts[0] == "app":
            node = self.app
        else:
            raise WireFail("path_not_found", "%s: unknown root" % path)
        walked = parts[0]
        for segment in parts[1:]:
            if segment.isdigit():
                index = int(segment)
                if not isinstance(node, list) or index >= len(node):
                    raise WireFail("path_not_found",
                                   "%s: index out of range" % path)
                node = node[index]
            else:
                if not isinstance(node, dict) or segment in INTERNAL_KEYS \
                        or segment not in node:
                    cls = node.get("__class__") if isinstance(node, dict) else \
                        type(node).__name__
                    raise WireFail("path_not_found",
                                   "%s: no property '%s' on %s"
                                   % (path, segment, cls))
                node = node[segment]
                if node is None:
                    raise WireFail("path_not_found",
                                   "%s: '%s.%s' is None" % (path, walked, segment))
            walked += "." + segment
        return node

    def encode(self, value, path):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, list):
            # plain dicts (a clip's note records) carry no __class__
            elem = value[0].get("__class__") if value and isinstance(value[0], dict) \
                else None
            return {"$vec": {"class": elem, "len": len(value)}}
        if isinstance(value, dict):
            stub = {"class": value.get("__class__"), "path": path}
            if "_live_ptr" in value:
                stub["ptr"] = value["_live_ptr"]
            else:
                stub["ptr"] = id(value)      # stable for the object's lifetime
            return {"$obj": stub}
        return {"$repr": repr(value)}

    def decode(self, value):
        if isinstance(value, dict) and "$obj" in value:
            return self.resolve(value["$obj"]["path"])
        return value


class WireFail(Exception):
    def __init__(self, code, message, **fields):
        super().__init__(message)
        self.error = {"code": code, "message": message}
        self.error.update(fields)


class FakeBridgeServer:
    def __init__(self):
        self.live = FakeLive()
        self.op_log = []
        self._server = None
        self.port = None
        self._writers = []

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        for writer in self._writers:
            try:
                writer.close()
            except Exception:
                pass
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer):
        self._writers.append(writer)
        self.subscriptions = getattr(self, "subscriptions", {})
        while True:
            line = await reader.readline()
            if not line:
                break
            frame = json.loads(line)
            self.op_log.append((frame.get("op"), frame))
            response, events = self._dispatch(frame)
            payload = json.dumps(response) + "\n"
            for event in events:
                payload += json.dumps(event) + "\n"
            writer.write(payload.encode())
            try:
                await writer.drain()
            except ConnectionError:
                break

    # --- dispatch -------------------------------------------------------------

    def _dispatch(self, frame):
        frame_id = frame.get("id")
        op = frame.get("op")
        events = []
        try:
            handler = getattr(self, "_op_" + str(op), None)
            if handler is None:
                raise WireFail("unknown_op", "unknown op %r" % op)
            result = handler(frame, events)
            return {"id": frame_id, "ok": True, "result": result}, events
        except WireFail as exc:
            return {"id": frame_id, "ok": False, "error": exc.error}, events
        except Exception as exc:
            # A bug in the fake must surface as an error frame, not as a dead
            # connection the client waits 15 s for.
            return {"id": frame_id, "ok": False, "error": {
                "code": "internal",
                "message": "fake bridge raised: %r" % (exc,)}}, events

    def _op_ping(self, frame, events):
        return {"contract": "1.2", "script": "fake", "live": "12.4.3",
                "python": "3.11.6"}

    def _op_describe(self, frame, events):
        path = frame["path"]
        node = self.live.resolve(path)
        if not isinstance(node, dict):
            raise WireFail("bad_request", "cannot describe %s" % path)
        props = {}
        for key, value in node.items():
            if key in INTERNAL_KEYS:
                continue
            props[key] = self.live.encode(value, "%s.%s" % (path, key))
        return {"class": node.get("__class__"), "path": path, "props": props}

    def _op_expect(self, frame, events):
        """Fail unless a property still holds what the caller resolved."""
        node = self.live.resolve(frame["path"])
        prop = frame.get("prop")
        if not isinstance(prop, str) or not prop:
            raise WireFail("bad_request", "expect requires a prop name")
        if "equals" not in frame:
            raise WireFail("bad_request", "expect requires 'equals'")
        actual = node.get("_live_ptr", id(node)) if prop == "_live_ptr" \
            else (node.get(prop) if isinstance(node, dict) else None)
        if actual != frame["equals"]:
            raise WireFail("expectation_failed",
                           "%s.%s is %r, not the %r this call resolved"
                           % (frame["path"], prop, actual, frame["equals"]),
                           path=frame["path"], prop=prop)
        return {"ok_expected": frame["equals"]}

    def _op_get(self, frame, events):
        path = frame["path"]
        node = self.live.resolve(path)
        values = {}
        for prop in frame["props"]:
            # Every LOM object has an identity, and it is what tells a caller
            # that the thing at an index is not the thing it resolved. The fake
            # had none on tracks, so a stale-locator guard could not fail here
            # even when it would have failed against Live.
            if prop == "_live_ptr" and isinstance(node, dict):
                values[prop] = node.get("_live_ptr", id(node))
                continue
            if not isinstance(node, dict) or prop in INTERNAL_KEYS \
                    or prop not in node:
                values[prop] = {"$error": {"code": "property_not_found",
                                           "message": "no property '%s'" % prop}}
                continue
            values[prop] = self.live.encode(node[prop], "%s.%s" % (path, prop))
        return {"values": values}

    def _op_set(self, frame, events):
        path = frame["path"]
        node = self.live.resolve(path)
        cls = node.get("__class__")
        for prop in frame["props"]:
            if prop in INTERNAL_KEYS or prop not in node:
                raise WireFail("property_not_found",
                               "no property '%s' on %s" % (prop, cls),
                               path=path, prop=prop)
            if (cls, prop) in READ_ONLY:
                raise WireFail("property_read_only",
                               "%s.%s is read-only" % (cls, prop),
                               path=path, prop=prop)
        values = {}
        for prop, value in frame["props"].items():
            decoded = self.live.decode(value)
            if prop == "display_value":
                # emulate Live 12.4.3: display_value is NUMERIC display units
                if isinstance(decoded, str):
                    raise WireFail("live_error",
                                   "display_value setter takes a float")
                node[prop] = float(decoded)
            else:
                node[prop] = decoded
            values[prop] = self.live.encode(node[prop], "%s.%s" % (path, prop))
            self._emit_change(path, prop, events)
        return {"values": values}

    def _emit_change(self, path, prop, events):
        for sub_id, sub in getattr(self, "subscriptions", {}).items():
            if sub["path"] == path and prop in sub["props"]:
                sub["seq"] += 1
                node = self.live.resolve(path)
                events.append({"event": "change", "sub": sub_id,
                               "seq": sub["seq"], "path": path, "prop": prop,
                               "value": self.live.encode(
                                   node[prop], "%s.%s" % (path, prop))})

    # --- calls ------------------------------------------------------------------

    def _op_call(self, frame, events):
        path = frame["path"]
        method = frame["method"]
        args = [self.live.decode(a) for a in frame.get("args", [])]
        node = self.live.resolve(path)
        cls = node.get("__class__") if isinstance(node, dict) else None
        song = self.live.song

        if cls == "Song":
            if method in ("create_midi_track", "create_audio_track"):
                index = args[0] if args else -1
                track = _track("New Track", midi=method == "create_midi_track")
                position = len(song["tracks"]) if index == -1 else index
                song["tracks"].insert(position, track)
                return {"value": {"$obj": {"class": "Track",
                                           "path": "song.tracks.%d" % position}}}
            if method == "delete_track":
                song["tracks"].pop(args[0])
                return {"value": None}
            if method == "delete_return_track":
                song["return_tracks"].pop(args[0])
                return {"value": None}
            if method == "duplicate_track":
                clone = copy.deepcopy(song["tracks"][args[0]])
                clone["name"] += " Copy"
                song["tracks"].insert(args[0] + 1, clone)
                return {"value": None}
            if method == "create_scene":
                index = args[0] if args else -1
                scene = {"__class__": "Scene", "name": "", "color": 0}
                position = len(song["scenes"]) if index == -1 else index
                song["scenes"].insert(position, scene)
                return {"value": {"$obj": {"class": "Scene",
                                           "path": "song.scenes.%d" % position}}}
            if method == "delete_scene":
                song["scenes"].pop(args[0])
                return {"value": None}
            if method in ("start_playing", "continue_playing"):
                song["is_playing"] = True
                return {"value": None}
            if method == "stop_playing":
                song["is_playing"] = False
                return {"value": None}
            if method == "stop_all_clips":
                return {"value": None}
        if cls == "ClipSlot":
            if method == "create_audio_clip":
                if node["has_clip"]:
                    raise WireFail("live_error", "slot already has a clip")
                clip = _arrangement_clip("", 0.0, 4.0, midi=False)
                clip.pop("start_time"), clip.pop("end_time")
                clip["is_arrangement_clip"] = False
                clip["file_path"] = args[0]
                node["clip"] = clip
                node["has_clip"] = True
                return {"value": None}
            if method == "create_clip":
                if node["has_clip"]:
                    raise WireFail("live_error", "slot already has a clip")
                node["clip"] = {"__class__": "Clip", "name": "", "color": 0,
                                "length": float(args[0]), "looping": True,
                                "loop_start": 0.0, "loop_end": float(args[0]),
                                "signature_numerator": 4,
                                "signature_denominator": 4,
                                "is_midi_clip": True, "is_playing": False,
                                "notes": [], "automation_envelopes": [],
                                "has_envelopes": False}
                node["has_clip"] = True
                return {"value": None}
            if method == "delete_clip":
                node["clip"] = None
                node["has_clip"] = False
                return {"value": None}
            if method == "fire":
                if node.get("clip"):
                    node["clip"]["is_playing"] = True
                return {"value": None}
            if method == "stop":
                if node.get("clip"):
                    node["clip"]["is_playing"] = False
                return {"value": None}
            if method == "duplicate_clip_to":
                target = args[0]
                target["clip"] = copy.deepcopy(node["clip"])
                target["has_clip"] = True
                return {"value": None}
        if cls == "Scene" and method == "fire":
            return {"value": None}
        if cls == "Track":
            if method == "stop_all_clips":
                return {"value": None}
            if method == "duplicate_clip_to_arrangement":
                clip, at = args
                node["arrangement_clips"].append(
                    _arrangement_clip(clip["name"], at, clip["length"],
                                      color=clip["color"],
                                      notes=[dict(n) for n in
                                             clip.get("notes", [])]))
                return {"value": {"$obj": {"class": "Clip", "path": None}}}
            if method == "create_midi_clip":
                at, length = args
                if not node.get("has_midi_input"):
                    raise WireFail("live_error", "not a MIDI track")
                node["arrangement_clips"].append(
                    _arrangement_clip("", at, length))
                return {"value": {"$obj": {"class": "Clip", "path": None}}}
            if method == "create_audio_clip":
                path, at = args
                if not node.get("has_audio_input"):
                    raise WireFail("live_error", "not an audio track")
                clip = _arrangement_clip("", at, 4.0, midi=False)
                clip["file_path"] = path
                node["arrangement_clips"].append(clip)
                return {"value": {"$obj": {"class": "Clip", "path": None}}}
            if method == "delete_clip":
                before = len(node["arrangement_clips"])
                node["arrangement_clips"] = [c for c in node["arrangement_clips"]
                                             if c is not args[0]]
                if len(node["arrangement_clips"]) == before:
                    raise WireFail("live_error", "clip not on this track")
                return {"value": None}
        if cls == "Clip" and method == "quantize":
            node.setdefault("quantize_calls", []).append(tuple(args))
            return {"value": None}
        if cls == "Clip" and method == "create_automation_envelope":
            param = args[0]
            for envelope in node["automation_envelopes"]:
                if envelope["parameter"] is param:
                    # mirrors Live 12.4.3: this is NOT idempotent
                    raise WireFail("live_error",
                                   "There is already an envelope for the "
                                   "parameter")
            node["automation_envelopes"].append(
                {"__class__": "Envelope", "parameter": param, "steps": []})
            node["has_envelopes"] = True
            return {"value": {"$obj": {"class": "Envelope", "path": None}}}
        if cls == "Clip" and method == "clear_all_envelopes":
            node["automation_envelopes"] = []
            node["has_envelopes"] = False
            return {"value": None}
        if cls == "Clip" and method == "clear_envelope":
            node["automation_envelopes"] = [
                e for e in node["automation_envelopes"]
                if e["parameter"] is not args[0]]
            node["has_envelopes"] = bool(node["automation_envelopes"])
            return {"value": None}
        if cls == "Envelope":
            if method == "insert_step":
                time, span, value = args
                node["steps"] = [s for s in node["steps"]
                                 if not (time <= s[0] < time + span)]
                node["steps"].append((float(time), float(span), float(value)))
                node["steps"].sort()
                return {"value": None}
            if method == "value_at_time":
                at = args[0]
                for start, span, value in node["steps"]:
                    if start <= at < start + span:
                        return {"value": value}
                return {"value": None}
        if cls == "Browser" and method == "load_item":
            item = args[0]
            selected = song["view"].get("selected_track")
            if selected is None:
                raise WireFail("live_error", "no selected track")
            selected["devices"].append({
                "__class__": "PluginDevice", "name": item["name"],
                "class_name": "PluginDevice", "can_have_chains": True,
                "parameters": [_param("Device On", 1.0, display=1.0),
                               _param("Filter Cutoff", 47.0, 0.0, 127.0, 835.0)],
                "chains": [{
                    "__class__": "Chain", "name": "Chain 1",
                    "devices": [{
                        "__class__": "PluginDevice", "name": "Inner",
                        "class_name": "PluginDevice",
                        "parameters": [_param("Inner Gain", 0.5, 0.0, 1.0, 0.0)],
                        "chains": []}]}]})
            return {"value": None}
        if cls == "View" and method == "show_view":
            self.live.view_log.append(args[0])
            return {"value": None}
        raise WireFail("method_not_found",
                       "no method '%s' on %s" % (method, cls), path=path)

    # --- notes ---------------------------------------------------------------------

    def _clip_for_notes(self, path):
        node = self.live.resolve(path)
        if not isinstance(node, dict) or node.get("__class__") != "Clip" \
                or not node.get("is_midi_clip"):
            raise WireFail("not_a_midi_clip", "%s is not a MIDI clip" % path,
                           path=path)
        return node

    def _op_get_notes(self, frame, events):
        clip = self._clip_for_notes(frame["path"])
        from_pitch = frame.get("from_pitch", 0)
        pitch_span = frame.get("pitch_span", 128)
        from_time = frame.get("from_time", 0.0)
        time_span = frame.get("time_span", 1048576.0)
        notes = [dict(n) for n in clip["notes"]
                 if from_pitch <= n["pitch"] < from_pitch + pitch_span
                 and from_time <= n["start"] < from_time + time_span]
        return {"notes": notes}

    def _op_edit_notes(self, frame, events):
        clip = self._clip_for_notes(frame["path"])
        counts = {"added": 0, "updated": 0, "removed": 0}
        added_ids = []
        region = frame.get("remove_region")
        if region:
            keep = []
            for note in clip["notes"]:
                inside = (region.get("from_time", 0.0) <= note["start"] <
                          region.get("from_time", 0.0) +
                          region.get("time_span", 1048576.0)
                          and region.get("from_pitch", 0) <= note["pitch"] <
                          region.get("from_pitch", 0) +
                          region.get("pitch_span", 128))
                if inside:
                    counts["removed"] += 1
                else:
                    keep.append(note)
            clip["notes"] = keep
        for note_id in frame.get("remove_ids", []):
            before = len(clip["notes"])
            clip["notes"] = [n for n in clip["notes"] if n["id"] != note_id]
            counts["removed"] += before - len(clip["notes"])
        by_id = {n["id"]: n for n in clip["notes"]}
        for entry in frame.get("update", []):
            if entry.get("id") not in by_id:
                raise WireFail("bad_request",
                               "unknown note ids: [%s]" % entry.get("id"),
                               path=frame["path"])
            by_id[entry["id"]].update(
                {k: v for k, v in entry.items() if k != "id"})
            counts["updated"] += 1
        for spec in frame.get("add", []):
            note = {"id": self.live.next_note_id, "pitch": spec["pitch"],
                    "start": spec["start"], "duration": spec["duration"],
                    "velocity": spec.get("velocity", 100),
                    "mute": spec.get("mute", False),
                    "probability": spec.get("probability", 1.0),
                    "velocity_deviation": spec.get("velocity_deviation", 0.0),
                    "release_velocity": spec.get("release_velocity", 64)}
            self.live.next_note_id += 1
            clip["notes"].append(note)
            added_ids.append(note["id"])
            counts["added"] += 1
        return {"added_ids": added_ids, "counts": counts}

    # --- batch and subscriptions ------------------------------------------------------

    def _op_batch(self, frame, events):
        ops = frame.get("ops", [])
        stop_on_error = frame.get("stop_on_error", True)
        snapshot = (copy.deepcopy(self.live.song), copy.deepcopy(self.live.app))
        results = []
        failed = False
        mutated = False
        for sub in ops:
            if failed and stop_on_error:
                results.append({"skipped": True})
                continue
            try:
                handler = getattr(self, "_op_" + sub["op"])
                results.append({"ok": True, "result": handler(sub, events)})
                if sub["op"] in ("set", "call", "edit_notes"):
                    mutated = True
            except WireFail as exc:
                results.append({"ok": False, "error": exc.error})
                failed = True
        rolled_back = False
        undo_hint = None
        if failed and stop_on_error and mutated:
            self.live.song, self.live.app = snapshot
            rolled_back = True
            undo_hint = 'Undo Fake Batch'
        out = {"results": results, "rolled_back": rolled_back}
        if undo_hint:
            out["undo_hint"] = undo_hint
        return out

    def _op_subscribe(self, frame, events):
        subs = self.subscriptions = getattr(self, "subscriptions", {})
        sub_id = len(subs) + 1
        path = frame["path"]
        node = self.live.resolve(path)
        values = {}
        for prop in frame["props"]:
            if prop not in node:
                raise WireFail("not_listenable", "no listener for %s" % prop,
                               path=path, prop=prop)
            values[prop] = self.live.encode(node[prop], "%s.%s" % (path, prop))
        subs[sub_id] = {"path": path, "props": frame["props"], "seq": 0}
        return {"sub": sub_id, "values": values}

    def _op_unsubscribe(self, frame, events):
        subs = getattr(self, "subscriptions", {})
        if frame.get("sub") not in subs:
            raise WireFail("subscription_not_found",
                           "no subscription %r" % frame.get("sub"))
        del subs[frame["sub"]]
        return {}
