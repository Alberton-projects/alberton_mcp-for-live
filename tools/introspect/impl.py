# AlbertonIntrospect implementation — executed from disk by __init__.py on
# every instantiation (see the loader for the edit/re-run loop).
#
# Read-only introspection of the Live Object Model as it exists on this
# machine. Produces two files in OUTPUT_DIR:
#   lom-raw.json        — module walk + instance walk (the Phase 0 deliverable)
#   lom-introspect.log  — progress and errors, appended across runs
#
# The human-readable docs/lom-inventory.md is rendered from the JSON outside
# Live, so rendering changes never cost a Live restart. Nothing here mutates
# the Live set: only class introspection, property getters, and the three
# Application version getters are touched.

import json
import os
import sys
import time
import traceback

import Live

OUTPUT_DIR = "/Users/workingburcet/Alberton-MCP for Live/docs"
JSON_PATH = os.path.join(OUTPUT_DIR, "lom-raw.json")
LOG_PATH = os.path.join(OUTPUT_DIR, "lom-introspect.log")

MAX_DEPTH = 8      # object-graph recursion limit from each root (vectors are free)
APP_DEPTH = 4      # shallower walk for Application (the browser is Phase 3 work)
VECTOR_SAMPLE = 2  # elements of each vector to descend into
MAX_NODES = 4000   # hard budget for one instance walk
RERUN_TICK = 30    # update_display ticks (~100 ms each) before the settled pass
SWEEP_DEPTH = 3    # snapshot depth for the device-class sweep
SWEEP_NEST = 6     # how deep into rack chains the sweep looks for new classes

# Up-links drag parents in through side doors (e.g. scenes -> clip_slots ->
# canonical_parent reaches a Track before song.tracks does), which turns the
# canonical locations into revisit stubs. The tree itself already encodes
# parenthood; the class dump documents these properties.
SKIP_INSTANCE_PROPS = ("canonical_parent", "group_track")


def _ensure_output_dir():
    """Fall back to the home directory if the project path is not writable."""
    global OUTPUT_DIR, JSON_PATH, LOG_PATH
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        probe = os.path.join(OUTPUT_DIR, ".alberton-write-test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
    except Exception:
        OUTPUT_DIR = os.path.expanduser("~")
        JSON_PATH = os.path.join(OUTPUT_DIR, "lom-raw.json")
        LOG_PATH = os.path.join(OUTPUT_DIR, "lom-introspect.log")


_ensure_output_dir()


def _log(message):
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), message))
    except Exception:
        pass


def _safe_repr(value, limit=200):
    try:
        text = repr(value)
    except Exception as exc:
        return "<repr failed: %s>" % exc
    return text[:limit]


def _json_fallback(value):
    return _safe_repr(value)


def _write_json(data):
    tmp_path = JSON_PATH + ".tmp"
    with open(tmp_path, "w") as fh:
        json.dump(data, fh, indent=1, sort_keys=True, default=_json_fallback)
    os.replace(tmp_path, JSON_PATH)


# --- class-level walk of the Live module -----------------------------------


def _is_enum(cls):
    return isinstance(cls, type) and hasattr(cls, "names") and hasattr(cls, "values")


def _describe_enum(cls):
    entry = {"doc": cls.__doc__}
    try:
        entry["values"] = dict(sorted((str(k), int(v)) for k, v in cls.names.items()))
    except Exception as exc:
        entry["error"] = _safe_repr(exc)
    return entry


def _describe_member(owner, name):
    try:
        attr = getattr(owner, name)
    except Exception as exc:
        return {"kind": "unreadable", "error": _safe_repr(exc)}
    entry = {"type": type(attr).__name__}
    doc = getattr(attr, "__doc__", None)
    if doc:
        entry["doc"] = doc
    if isinstance(attr, property):
        entry["kind"] = "property"
        entry["writable"] = attr.fset is not None
    elif _is_enum(attr):
        entry["kind"] = "enum"
        entry.update(_describe_enum(attr))
    elif isinstance(attr, type):
        entry["kind"] = "class"
    elif callable(attr):
        entry["kind"] = "method"
    else:
        entry["kind"] = "data"
        entry["repr"] = _safe_repr(attr)
    return entry


def _describe_class(cls, depth=0):
    info = {"doc": cls.__doc__}
    try:
        info["bases"] = [base.__name__ for base in cls.__bases__]
    except Exception:
        pass
    members = {}
    nested = {}
    for name in sorted(dir(cls)):
        if name.startswith("__"):
            continue
        entry = _describe_member(cls, name)
        if entry.get("kind") == "class" and depth < 2:
            try:
                nested[name] = _describe_class(getattr(cls, name), depth + 1)
            except Exception as exc:
                nested[name] = {"error": _safe_repr(exc)}
        else:
            members[name] = entry
    info["members"] = members
    if nested:
        info["nested_classes"] = nested
    return info


def _walk_live_module():
    modules = {}
    for module_name in sorted(dir(Live)):
        if module_name.startswith("__"):
            continue
        try:
            module = getattr(Live, module_name)
        except Exception as exc:
            modules[module_name] = {"error": _safe_repr(exc)}
            continue
        entry = {
            "doc": getattr(module, "__doc__", None),
            "classes": {},
            "enums": {},
            "functions": {},
            "data": {},
        }
        for name in sorted(dir(module)):
            if name.startswith("__"):
                continue
            try:
                attr = getattr(module, name)
            except Exception as exc:
                entry["data"][name] = {"error": _safe_repr(exc)}
                continue
            if _is_enum(attr):
                entry["enums"][name] = _describe_enum(attr)
            elif isinstance(attr, type):
                entry["classes"][name] = _describe_class(attr)
            elif callable(attr):
                entry["functions"][name] = {"doc": getattr(attr, "__doc__", None)}
            else:
                entry["data"][name] = {
                    "type": type(attr).__name__,
                    "repr": _safe_repr(attr),
                }
        modules[module_name] = entry
    return modules


# --- instance walk of the open set ------------------------------------------


def _identity(obj):
    try:
        ptr = getattr(obj, "_live_ptr", None)
        if ptr is not None:
            return ("ptr", int(ptr))
    except Exception:
        pass
    return ("id", id(obj))


# Inside Live, LOM classes carry bare module names ("Song", "Browser", ...),
# not "Live.Song" — so match against the Live package's own submodule list,
# with the universal _live_ptr handle as the primary signal.
_LIVE_MODULE_NAMES = set(name for name in dir(Live) if not name.startswith("__"))


def _is_lom_object(value):
    try:
        if hasattr(value, "_live_ptr"):
            return True
    except Exception:
        pass
    module = getattr(type(value), "__module__", "") or ""
    root = module.split(".")[0]
    return root == "Live" or root in _LIVE_MODULE_NAMES


def _looks_like_vector(value):
    if isinstance(value, (str, bytes)):
        return False
    return hasattr(value, "__len__") and hasattr(value, "__getitem__")


def _describe_value(value, depth, seen, budget):
    if value is None:
        return {"type": "NoneType"}
    if isinstance(value, (bool, int, float)):
        return {"type": type(value).__name__, "value": value}
    if isinstance(value, str):
        return {"type": "str", "value": value[:200]}
    if _looks_like_vector(value):
        node = {"type": type(value).__name__}
        try:
            length = len(value)
        except Exception as exc:
            node["error"] = _safe_repr(exc)
            return node
        node["len"] = length
        samples = []
        for index in range(min(length, VECTOR_SAMPLE)):
            try:
                samples.append(_describe_value(value[index], depth, seen, budget))
            except Exception as exc:
                samples.append({"error": _safe_repr(exc)})
        if samples:
            node["sample"] = samples
        return node
    if _is_lom_object(value):
        return _snapshot(value, depth - 1, seen, budget)
    return {"type": type(value).__name__, "repr": _safe_repr(value)}


def _snapshot(obj, depth, seen, budget):
    node = {"class": type(obj).__name__, "module": getattr(type(obj), "__module__", None)}
    key = _identity(obj)
    if key in seen:
        node["revisit"] = True
        return node
    seen.add(key)
    if depth <= 0:
        node["truncated"] = "depth"
        return node
    if budget[0] <= 0:
        node["truncated"] = "budget"
        return node
    budget[0] -= 1
    properties = {}
    cls = type(obj)
    for name in sorted(dir(cls)):
        if name.startswith("__"):
            continue
        if name.startswith(("add_", "remove_")) or name.endswith("_has_listener"):
            continue
        if name in SKIP_INSTANCE_PROPS:
            continue
        try:
            descriptor = getattr(cls, name)
        except Exception:
            continue
        if not isinstance(descriptor, property):
            continue
        try:
            value = getattr(obj, name)
        except Exception as exc:
            properties[name] = {"error": _safe_repr(exc)}
            continue
        try:
            properties[name] = _describe_value(value, depth, seen, budget)
        except Exception as exc:
            properties[name] = {"error": _safe_repr(exc)}
    node["properties"] = properties
    return node


def _classes_in(node, into):
    """Every class name a walk result mentions, snapshots and stubs alike."""
    if isinstance(node, dict):
        name = node.get("class")
        if isinstance(name, str):
            into.add(name)
        for value in node.values():
            _classes_in(value, into)
    elif isinstance(node, list):
        for value in node:
            _classes_in(value, into)
    return into


def _sweep_unseen_classes(song, seen, budget, already):
    """One snapshot of every class the curated walk never met.

    The curated walk samples the first two elements of each vector, so a set's
    variety mostly hides behind tracks 3..N: device classes inside rack
    chains, and the classes that hang off clips — an audio clip's Sample and
    warp markers, a clip's automation envelopes — and off tracks (take
    lanes). Class-level members come from the module walk either way; what
    this buys is the evidence that a class exists in practice, one real
    instance's property values, and the per-instance errors that only show up
    live. Read-only, like everything here.
    """
    found = {}

    def grab(obj, where):
        cls = type(obj).__name__
        if cls not in already and cls not in found:
            found[cls] = {"where": where,
                          "snapshot": _snapshot(obj, SWEEP_DEPTH, seen, budget)}

    def grab_from_props(obj, where, names):
        """Snapshot prop values (a vector's head element) of unseen classes."""
        for name in names:
            if budget[0] <= 0:
                return
            try:
                value = getattr(obj, name)
            except Exception:
                continue
            spot = "%s.%s" % (where, name)
            if value is None:
                continue
            if _looks_like_vector(value) and not isinstance(value, str):
                try:
                    if len(value) == 0:
                        continue
                    value = value[0]
                    spot += ".0"
                except Exception:
                    continue
            if _is_lom_object(value):
                grab(value, spot)

    def visit_devices(container, where, nest):
        if budget[0] <= 0 or nest > SWEEP_NEST:
            return
        try:
            devices = container.devices
            count = len(devices)
        except Exception:
            return
        for index in range(count):
            try:
                device = devices[index]
            except Exception:
                continue
            here = "%s.devices.%d" % (where, index)
            grab(device, here)
            try:
                if device.can_have_chains:
                    chains = device.chains
                    for c in range(len(chains)):
                        grab(chains[c], "%s.chains.%d" % (here, c))
                        visit_devices(chains[c], "%s.chains.%d" % (here, c),
                                      nest + 1)
            except Exception:
                pass
            try:
                if device.can_have_drum_pads and device.has_drum_pads:
                    pads = device.drum_pads
                    if len(pads):
                        grab(pads[0], "%s.drum_pads.0" % here)
            except Exception:
                pass

    def visit_clip_sources(track, where):
        """First MIDI and first audio clip per track: the classes that hang
        off clips are where most of the never-met list lives."""
        try:
            slots = track.clip_slots
        except Exception:
            return
        wanted = {"midi": True, "audio": True}
        for s in range(len(slots)):
            if budget[0] <= 0 or not (wanted["midi"] or wanted["audio"]):
                return
            try:
                clip = slots[s].clip
                if clip is None:
                    continue
                kind = "midi" if clip.is_midi_clip else "audio"
            except Exception:
                continue
            if not wanted[kind]:
                continue
            wanted[kind] = False
            here = "%s.clip_slots.%d.clip" % (where, s)
            grab_from_props(clip, here,
                            ("sample", "warp_markers", "automation_envelopes"))

    try:
        lanes = ("take_lanes",)
        for t in range(len(song.tracks)):
            where = "song.tracks.%d" % t
            visit_devices(song.tracks[t], where, 0)
            grab_from_props(song.tracks[t], where, lanes)
            visit_clip_sources(song.tracks[t], where)
        for r in range(len(song.return_tracks)):
            visit_devices(song.return_tracks[r], "song.return_tracks.%d" % r, 0)
        visit_devices(song.master_track, "song.master_track", 0)
        grab_from_props(song, "song", ("cue_points", "groove_pool",
                                       "tuning_system"))
    except Exception as exc:
        found["_error"] = _safe_repr(exc)
    return found


def _walk_instances(c_instance):
    """Walk in curated order so full snapshots land at canonical locations."""
    seen = set()
    budget = [MAX_NODES]
    roots = {}
    try:
        song = c_instance.song()
    except Exception as exc:
        song = None
        roots["song"] = {"error": _safe_repr(exc)}
    if song is not None:
        for attr_name in ("tracks", "return_tracks", "master_track", "scenes"):
            try:
                value = getattr(song, attr_name)
            except Exception as exc:
                roots["song." + attr_name] = {"error": _safe_repr(exc)}
                continue
            roots["song." + attr_name] = _describe_value(value, MAX_DEPTH, seen, budget)
        roots["song"] = _snapshot(song, MAX_DEPTH, seen, budget)
    try:
        app = Live.Application.get_application()
    except Exception as exc:
        app = None
        roots["application"] = {"error": _safe_repr(exc)}
    if app is not None:
        roots["application"] = _snapshot(app, APP_DEPTH, seen, budget)
    if song is not None:
        already = _classes_in(roots, set())
        roots["class_sweep"] = _sweep_unseen_classes(
            song, seen, budget, already)
    roots["_nodes_used"] = MAX_NODES - budget[0]
    return roots


# --- odds and ends ------------------------------------------------------------


def _live_version():
    info = {}
    try:
        app = Live.Application.get_application()
    except Exception as exc:
        return {"error": _safe_repr(exc)}
    for name in ("get_major_version", "get_minor_version", "get_bugfix_version"):
        try:
            info[name] = getattr(app, name)()
        except Exception as exc:
            info[name] = _safe_repr(exc)
    return info


def _describe_c_instance(c_instance):
    members = {}
    for name in sorted(dir(c_instance)):
        if name.startswith("__"):
            continue
        try:
            attr = getattr(c_instance, name)
        except Exception as exc:
            members[name] = {"error": _safe_repr(exc)}
            continue
        members[name] = {"type": type(attr).__name__, "doc": getattr(attr, "__doc__", None)}
    return members


# --- the surface ---------------------------------------------------------------


class Introspector(object):
    def __init__(self, c_instance):
        self._c_instance = c_instance
        self._ticks = 0
        self._settled_pass_done = False
        self._data = None
        try:
            self._run("startup")
        except Exception:
            _log("FATAL in startup pass:\n" + traceback.format_exc())
            self._status("Alberton Phase 0: FAILED, see lom-introspect.log")

    def _status(self, message):
        try:
            self._c_instance.show_message(message)
        except Exception:
            pass
        try:
            self._c_instance.log_message(message)
        except Exception:
            pass

    def _run(self, label):
        started = time.time()
        _log("run(%s) begin; python %s" % (label, sys.version.split()[0]))
        data = self._data if self._data is not None else {}
        meta = data.setdefault("meta", {})
        meta["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        meta["pass"] = label
        meta["python"] = sys.version
        meta["platform"] = sys.platform
        meta["live_version"] = _live_version()
        if "modules" not in data:
            data["modules"] = _walk_live_module()
            _log("module walk done: %d modules" % len(data["modules"]))
        if "c_instance" not in data:
            data["c_instance"] = _describe_c_instance(self._c_instance)
        data["instances"] = _walk_instances(self._c_instance)
        _log("instance walk done: nodes=%s" % data["instances"].get("_nodes_used"))
        self._data = data
        _write_json(data)
        _log("run(%s) wrote %s in %.2fs" % (label, JSON_PATH, time.time() - started))
        self._status("Alberton Phase 0: LOM dump written (%s)" % label)

    # Live calls update_display roughly every 100 ms. One more pass once the
    # application has settled, in case the startup pass ran mid-load.
    def update_display(self):
        self._ticks += 1
        if not self._settled_pass_done and self._ticks >= RERUN_TICK:
            self._settled_pass_done = True
            try:
                self._run("settled")
            except Exception:
                _log("FATAL in settled pass:\n" + traceback.format_exc())

    def disconnect(self):
        _log("disconnect")

    def can_lock_to_devices(self):
        return False

    def suggest_input_port(self):
        return ""

    def suggest_output_port(self):
        return ""

    def suggest_map_mode(self, *args):
        return 0

    def suggest_needs_takeover(self, *args):
        return True

    def supports_pad_translation(self):
        return False

    def set_pad_translations(self, *args):
        pass

    def receive_midi(self, midi_bytes):
        pass

    def build_midi_map(self, midi_map_handle):
        pass

    def refresh_state(self):
        pass

    def connect_script_instances(self, instantiated_scripts):
        pass


def build(c_instance):
    return Introspector(c_instance)
