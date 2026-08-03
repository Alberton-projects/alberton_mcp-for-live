# Alberton bridge implementation — executed from disk by __init__.py.
#
# Implements Layer A of docs/CONTRACT.md v1.1: NDJSON over 127.0.0.1:17853,
# eleven generic operations over LOM object paths, batch with undo-step
# atomicity, subscriptions with coalesce-on-tick backpressure. Object stubs
# carry identity as well as location, and answers are written ahead of events
# so a client cannot starve its own replies.
#
# Threading model (CONTRACT A.8): socket threads only move bytes. All LOM
# access happens on Live's main thread inside update_display (~100 ms tick):
# adopt pending connection, execute queued ops FIFO, flush dirty subscriptions.

import json
import os
import socket
import threading
import time
import traceback
from collections import deque

try:
    import queue
except ImportError:  # pragma: no cover — Live 12 is Python 3
    import Queue as queue

import Live

HOST = "127.0.0.1"
PORT = 17853

CONTRACT_VERSION = "1.1"
SCRIPT_VERSION = "0.2.0"

LINE_MAX = 16 * 1024 * 1024
BATCH_MAX = 256
NOTES_MAX = 20000
SUBS_MAX = 128
OUTBOX_MAX = 4096
TICK_OP_BUDGET_S = 0.050  # max main-thread time spent executing ops per tick
TICK_OP_MAX = 64          # and never more than this many ops per tick

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(_SCRIPT_DIR, "alberton.log")

_LIVE_MODULE_NAMES = set(n for n in dir(Live) if not n.startswith("__"))

BATCHABLE_OPS = ("describe", "get", "set", "call", "get_notes", "edit_notes")

NOTE_FIELDS = (
    ("pitch", "pitch"),
    ("start", "start_time"),
    ("duration", "duration"),
    ("velocity", "velocity"),
    ("mute", "mute"),
    ("probability", "probability"),
    ("velocity_deviation", "velocity_deviation"),
    ("release_velocity", "release_velocity"),
)


def _log(message):
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), message))
    except Exception:
        pass


class _DeferredRollback(Exception):
    """Raised by batch when its undo step must be undone on the NEXT tick.

    Verified on Live 12.4.3: Song.undo() called in the same main-thread slice
    as end_undo_step() does not yet see the step in the undo history; one tick
    later it does. The batch response is therefore deferred until after the
    rollback, so callers still observe atomic-or-absent.
    """

    def __init__(self, results):
        Exception.__init__(self, "deferred rollback")
        self.results = results


class ProtocolError(Exception):
    def __init__(self, code, message, **fields):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.fields = fields

    def to_error(self):
        error = {"code": self.code, "message": self.message}
        for key, value in self.fields.items():
            if value is not None:
                error[key] = value
        return error


# --- value encoding / decoding (CONTRACT A.4) --------------------------------


def _is_lom_object(value):
    try:
        if hasattr(value, "_live_ptr"):
            return True
    except Exception:
        pass
    module = getattr(type(value), "__module__", "") or ""
    root = module.split(".")[0]
    return root == "Live" or root in _LIVE_MODULE_NAMES


def _is_lom_vector(value):
    if isinstance(value, (str, bytes, list, tuple, dict)):
        return False
    return hasattr(value, "__len__") and hasattr(value, "__getitem__")


class Bridge(object):

    # ---- lifecycle -----------------------------------------------------------

    def __init__(self, c_instance):
        self._c = c_instance
        self._running = True
        self._lock = threading.Lock()
        self._inbox = deque()          # (generation, frame)
        self._generation = 0
        self._client = None            # dict: conn, answers, events, gen, alive
        self._pending_conn = None
        self._subs = {}                # sub_id -> sub record
        self._next_sub_id = 1
        self._dirty = set()            # (sub_id, prop)
        self._undo_depth = 0
        self._pending_rollback = None  # {"id", "gen", "results"} awaiting next tick
        self._note_spec_kwargs = None  # discovered working kwarg set, cached
        self._listen_sock = None
        self._start_listener()
        _log("bridge %s up, contract %s, listening on %s:%d"
             % (SCRIPT_VERSION, CONTRACT_VERSION, HOST, PORT))
        self._status("Alberton: listening on %s:%d" % (HOST, PORT))

    def _status(self, message):
        try:
            self._c.show_message(message)
        except Exception:
            pass
        try:
            self._c.log_message(message)
        except Exception:
            pass

    def _song(self):
        return self._c.song()

    # ---- sockets (never touch the LOM from these threads) --------------------

    def _start_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, PORT))
        sock.listen(1)
        self._listen_sock = sock
        thread = threading.Thread(target=self._accept_loop, name="alberton-accept")
        thread.daemon = True
        thread.start()

    def _accept_loop(self):
        while self._running:
            try:
                conn, _addr = self._listen_sock.accept()
            except Exception:
                break  # listener closed on disconnect
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                if self._pending_conn is not None:
                    try:
                        self._pending_conn.close()
                    except Exception:
                        pass
                self._pending_conn = conn
        _log("accept loop ended")

    def _adopt_pending_conn(self):
        """Main thread: swap in a newly accepted connection (CONTRACT A.1)."""
        with self._lock:
            conn = self._pending_conn
            self._pending_conn = None
        if conn is None:
            return
        self._drop_client("replaced by new connection")
        self._clear_subscriptions()
        self._generation += 1
        gen = self._generation
        client = {"conn": conn, "gen": gen, "alive": [True],
                  "answers": queue.Queue(), "events": queue.Queue()}
        reader = threading.Thread(target=self._read_loop, args=(client,),
                                  name="alberton-read-%d" % gen)
        writer = threading.Thread(target=self._write_loop, args=(client,),
                                  name="alberton-write-%d" % gen)
        reader.daemon = True
        writer.daemon = True
        self._client = client
        reader.start()
        writer.start()
        _log("client adopted (gen %d)" % gen)

    def _drop_client(self, why):
        client = self._client
        if client is None:
            return
        self._client = None
        client["alive"][0] = False
        try:
            client["conn"].shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            client["conn"].close()
        except Exception:
            pass
        client["answers"].put(None)  # writer sentinel
        _log("client dropped (gen %d): %s" % (client["gen"], why))

    def _read_loop(self, client):
        conn, gen, alive = client["conn"], client["gen"], client["alive"]
        buf = b""
        while alive[0] and self._running:
            try:
                data = conn.recv(65536)
            except Exception:
                break
            if not data:
                break
            buf += data
            if len(buf) > LINE_MAX and b"\n" not in buf:
                self._send(client, {"id": None, "ok": False, "error": {
                    "code": "too_large",
                    "message": "request line exceeds %d bytes" % LINE_MAX}})
                break
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                if len(line) > LINE_MAX:
                    self._send(client, {"id": None, "ok": False, "error": {
                        "code": "too_large",
                        "message": "request line exceeds %d bytes" % LINE_MAX}})
                    alive[0] = False
                    break
                try:
                    frame = json.loads(line.decode("utf-8"))
                except Exception as exc:
                    self._send(client, {"id": None, "ok": False, "error": {
                        "code": "bad_request",
                        "message": "unparseable JSON line: %s" % exc}})
                    continue
                if not isinstance(frame, dict):
                    self._send(client, {"id": None, "ok": False, "error": {
                        "code": "bad_request",
                        "message": "frame must be a JSON object"}})
                    continue
                with self._lock:
                    self._inbox.append((gen, frame))
        alive[0] = False
        _log("read loop ended (gen %d)" % gen)

    def _write_loop(self, client):
        """Answers first, always.

        Responses and events used to share one queue, so a client watching a
        fast-changing property could push its own command replies behind
        thousands of events — measured at ~3.4 s of latency at the outbox cap.
        Two queues, responses drained first, and a client can no longer starve
        itself.
        """
        conn, alive = client["conn"], client["alive"]
        answers, events = client["answers"], client["events"]
        while alive[0] or not answers.empty() or not events.empty():
            item = None
            try:
                item = answers.get_nowait()
            except queue.Empty:
                try:
                    item = events.get(timeout=0.25)
                except queue.Empty:
                    continue
            if item is None:
                break
            try:
                conn.sendall(item)
            except Exception:
                alive[0] = False
                break
        _log("write loop ended (gen %d)" % client["gen"])

    def _send(self, client, frame, event_sub=None):
        """Queue a frame. Responses jump the queue; events respect OUTBOX_MAX."""
        if client is None:
            return False
        if event_sub is not None and client["events"].qsize() >= OUTBOX_MAX:
            sub = self._subs.get(event_sub)
            if sub is not None:
                sub["dropped"] += 1
            return False
        try:
            payload = (json.dumps(frame, separators=(",", ":"),
                                  allow_nan=False) + "\n").encode("utf-8")
        except ValueError:
            payload = (json.dumps(_scrub_nan(frame), separators=(",", ":")) +
                       "\n").encode("utf-8")
        if "event" in frame:
            client["events"].put(payload)
        else:
            client["answers"].put(payload)
        return True

    # ---- path resolution (CONTRACT A.5) ---------------------------------------

    def _resolve(self, path):
        if not isinstance(path, str) or not path:
            raise ProtocolError("bad_request", "missing or non-string path")
        parts = path.split(".")
        if parts[0] == "song":
            obj = self._song()
        elif parts[0] == "app":
            obj = Live.Application.get_application()
        else:
            raise ProtocolError("path_not_found",
                                "%s: unknown root '%s' (use song|app)" % (path, parts[0]),
                                path=path)
        walked = parts[0]
        for segment in parts[1:]:
            if segment.isdigit():
                index = int(segment)
                try:
                    length = len(obj)
                except Exception:
                    raise ProtocolError("path_not_found",
                                        "%s: '%s' is not indexable" % (path, walked),
                                        path=path)
                if index >= length:
                    raise ProtocolError("path_not_found",
                                        "%s: index %d out of range (len %d)"
                                        % (path, index, length), path=path)
                obj = obj[index]
            else:
                cls = type(obj)
                descriptor = getattr(cls, segment, None)
                if not isinstance(descriptor, property):
                    raise ProtocolError("path_not_found",
                                        "%s: no property '%s' on %s"
                                        % (path, segment, cls.__name__), path=path)
                try:
                    obj = getattr(obj, segment)
                except Exception as exc:
                    raise ProtocolError("live_error",
                                        "%s: reading '%s' raised: %s"
                                        % (path, segment, exc), path=path)
                if obj is None:
                    raise ProtocolError("path_not_found",
                                        "%s: '%s.%s' is None" % (path, walked, segment),
                                        path=path)
            walked = walked + "." + segment
        return obj

    def _path_of(self, obj):
        """Best-effort canonical path for objects returned by calls."""
        try:
            ptr = obj._live_ptr
        except Exception:
            return None
        song = self._song()

        def scan(vector, base):
            try:
                for index in range(len(vector)):
                    try:
                        if vector[index]._live_ptr == ptr:
                            return "%s.%d" % (base, index)
                    except Exception:
                        pass
            except Exception:
                pass
            return None

        cls = type(obj).__name__
        if cls == "Track":
            found = scan(song.tracks, "song.tracks")
            if found is None:
                found = scan(song.return_tracks, "song.return_tracks")
            if found is None:
                try:
                    if song.master_track._live_ptr == ptr:
                        found = "song.master_track"
                except Exception:
                    pass
            return found
        if cls == "Scene":
            return scan(song.scenes, "song.scenes")
        if cls in ("ClipSlot", "Clip"):
            try:
                for t_index in range(len(song.tracks)):
                    track = song.tracks[t_index]
                    for s_index in range(len(track.clip_slots)):
                        slot = track.clip_slots[s_index]
                        base = "song.tracks.%d.clip_slots.%d" % (t_index, s_index)
                        try:
                            if slot._live_ptr == ptr:
                                return base
                            clip = slot.clip
                            if clip is not None and clip._live_ptr == ptr:
                                return base + ".clip"
                        except Exception:
                            pass
            except Exception:
                pass
            return None
        return None

    # ---- encoding -------------------------------------------------------------

    def _encode(self, value, path_hint=None):
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return int(value)  # Boost enums are int subclasses
        if isinstance(value, float):
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return [self._encode(item) for item in value]
        if _is_lom_vector(value) and _is_lom_object_vector_or_any(value):
            try:
                length = len(value)
            except Exception:
                length = -1
            elem_class = None
            if length > 0:
                try:
                    elem_class = type(value[0]).__name__
                except Exception:
                    pass
            return {"$vec": {"class": elem_class, "len": length}}
        if _is_lom_object(value):
            path = path_hint if path_hint is not None else self._path_of(value)
            # Identity as well as location. Many objects have no canonical path
            # at all (envelopes, Arrangement clips, device parameters), and a
            # path that was correct at encode time can be invalidated a
            # millisecond later by a human editing in Live. The pointer never
            # lies: it is what _path_of scans by, and it costs nothing to send.
            stub = {"class": type(value).__name__, "path": path}
            try:
                stub["ptr"] = int(value._live_ptr)
            except Exception:
                pass
            return {"$obj": stub}
        return {"$repr": repr(value)[:200], "class": type(value).__name__}

    def _decode(self, value):
        if isinstance(value, dict):
            if "$obj" in value:
                ref = value["$obj"]
                if not isinstance(ref, dict) or not ref.get("path"):
                    raise ProtocolError("bad_request", "$obj reference without path")
                return self._resolve(ref["path"])
            return dict((k, self._decode(v)) for k, v in value.items())
        if isinstance(value, list):
            return [self._decode(item) for item in value]
        return value

    # ---- undo grouping (CONTRACT A.6 batch, and per-op steps) ------------------

    def _undo_begin(self):
        if self._undo_depth == 0:
            self._song().begin_undo_step()
        self._undo_depth += 1

    def _undo_end(self):
        self._undo_depth -= 1
        if self._undo_depth == 0:
            self._song().end_undo_step()

    # ---- operations ------------------------------------------------------------

    def _op_ping(self, params):
        app = Live.Application.get_application()
        return {
            "contract": CONTRACT_VERSION,
            "script": SCRIPT_VERSION,
            "live": "%d.%d.%d" % (app.get_major_version(),
                                  app.get_minor_version(),
                                  app.get_bugfix_version()),
            "python": "%d.%d.%d" % tuple(__import__("sys").version_info[:3]),
        }

    def _op_describe(self, params):
        path = params.get("path")
        obj = self._resolve(path)
        cls = type(obj)
        props = {}
        for name in sorted(dir(cls)):
            if name.startswith("__"):
                continue
            if name.startswith(("add_", "remove_")) or name.endswith("_has_listener"):
                continue
            descriptor = getattr(cls, name, None)
            if not isinstance(descriptor, property):
                continue
            try:
                value = getattr(obj, name)
            except Exception as exc:
                props[name] = {"$error": {"code": "live_error", "message": str(exc)}}
                continue
            props[name] = self._encode(value, path_hint=path + "." + name)
        return {"class": cls.__name__, "path": path, "props": props}

    def _op_get(self, params):
        path = params.get("path")
        props = params.get("props")
        if not isinstance(props, list) or not props:
            raise ProtocolError("bad_request", "get requires non-empty props list")
        obj = self._resolve(path)
        values = {}
        for prop in props:
            descriptor = getattr(type(obj), prop, None)
            if not isinstance(descriptor, property):
                values[prop] = {"$error": {
                    "code": "property_not_found",
                    "message": "no property '%s' on %s" % (prop, type(obj).__name__)}}
                continue
            try:
                values[prop] = self._encode(getattr(obj, prop),
                                            path_hint=path + "." + prop)
            except Exception as exc:
                values[prop] = {"$error": {"code": "live_error", "message": str(exc)}}
        return {"values": values}

    def _op_set(self, params):
        path = params.get("path")
        props = params.get("props")
        if not isinstance(props, dict) or not props:
            raise ProtocolError("bad_request", "set requires non-empty props object")
        obj = self._resolve(path)
        cls = type(obj)
        for prop in props:  # validate everything before touching anything
            descriptor = getattr(cls, prop, None)
            if not isinstance(descriptor, property):
                raise ProtocolError("property_not_found",
                                    "no property '%s' on %s" % (prop, cls.__name__),
                                    path=path, prop=prop)
            if descriptor.fset is None:
                raise ProtocolError("property_read_only",
                                    "%s.%s is read-only" % (cls.__name__, prop),
                                    path=path, prop=prop)
        self._undo_begin()
        try:
            for prop, value in props.items():
                try:
                    setattr(obj, prop, self._decode(value))
                except ProtocolError:
                    raise
                except Exception as exc:
                    raise ProtocolError("live_error",
                                        "setting %s.%s raised: %s"
                                        % (cls.__name__, prop, exc),
                                        path=path, prop=prop)
        finally:
            self._undo_end()
        values = {}
        for prop in props:  # read-back (CONTRACT A.6)
            try:
                values[prop] = self._encode(getattr(obj, prop),
                                            path_hint=path + "." + prop)
            except Exception as exc:
                values[prop] = {"$error": {"code": "live_error", "message": str(exc)}}
        return {"values": values}

    def _op_call(self, params):
        path = params.get("path")
        method_name = params.get("method")
        if not isinstance(method_name, str) or not method_name:
            raise ProtocolError("bad_request", "call requires a method name")
        obj = self._resolve(path)
        method = getattr(obj, method_name, None)
        if method is None or not callable(method):
            raise ProtocolError("method_not_found",
                                "no method '%s' on %s" % (method_name,
                                                          type(obj).__name__),
                                path=path, method=method_name)
        args = [self._decode(a) for a in params.get("args", [])]
        kwargs = dict((k, self._decode(v))
                      for k, v in params.get("kwargs", {}).items())
        if method_name in ("undo", "redo", "begin_undo_step", "end_undo_step"):
            # Undo-machinery calls must not run inside an undo step of ours.
            try:
                return {"value": self._encode(method(*args, **kwargs))}
            except Exception as exc:
                raise ProtocolError("live_error", "%s raised: %s" % (method_name, exc),
                                    path=path, method=method_name)
        self._undo_begin()
        try:
            try:
                result = method(*args, **kwargs)
            except ProtocolError:
                raise
            except TypeError as exc:
                raise ProtocolError("type_error", "%s: %s" % (method_name, exc),
                                    path=path, method=method_name)
            except Exception as exc:
                raise ProtocolError("live_error", "%s raised: %s" % (method_name, exc),
                                    path=path, method=method_name)
        finally:
            self._undo_end()
        return {"value": self._encode(result)}

    # ---- notes -----------------------------------------------------------------

    def _midi_clip(self, path):
        obj = self._resolve(path)
        if type(obj).__name__ != "Clip":
            raise ProtocolError("not_a_midi_clip",
                                "%s is %s, not a Clip" % (path, type(obj).__name__),
                                path=path)
        try:
            if not obj.is_midi_clip:
                raise ProtocolError("not_a_midi_clip",
                                    "%s is an audio clip" % path, path=path)
        except ProtocolError:
            raise
        except Exception as exc:
            raise ProtocolError("live_error", str(exc), path=path)
        return obj

    @staticmethod
    def _note_to_wire(note):
        wire = {"id": int(note.note_id)}
        for wire_name, lom_name in NOTE_FIELDS:
            value = getattr(note, lom_name)
            if isinstance(value, bool):
                wire[wire_name] = value
            elif wire_name in ("pitch", "velocity", "release_velocity"):
                wire[wire_name] = int(value) if float(value).is_integer() else float(value)
            else:
                wire[wire_name] = float(value)
        return wire

    def _region_args(self, params, clip):
        from_pitch = int(params.get("from_pitch", 0))
        pitch_span = int(params.get("pitch_span", 128))
        from_time = float(params.get("from_time", 0.0))
        if "time_span" in params:
            time_span = float(params["time_span"])
        else:
            time_span = 1048576.0  # "whole clip": beyond any realistic content
        return from_pitch, pitch_span, from_time, time_span

    def _op_get_notes(self, params):
        clip = self._midi_clip(params.get("path"))
        from_pitch, pitch_span, from_time, time_span = self._region_args(params, clip)
        try:
            vector = clip.get_notes_extended(from_pitch, pitch_span,
                                             from_time, time_span)
        except Exception as exc:
            raise ProtocolError("live_error", "get_notes_extended: %s" % exc,
                                path=params.get("path"))
        notes = [self._note_to_wire(vector[i]) for i in range(len(vector))]
        if len(notes) > NOTES_MAX:
            raise ProtocolError("too_large",
                                "%d notes exceed the %d limit; narrow the region"
                                % (len(notes), NOTES_MAX), path=params.get("path"))
        return {"notes": notes}

    def _make_note_spec(self, note):
        """Build a MidiNoteSpecification, discovering supported kwargs once."""
        for required in ("pitch", "start", "duration"):
            if required not in note:
                raise ProtocolError("bad_request",
                                    "note is missing required field '%s'" % required)
        full = {
            "pitch": int(note["pitch"]),
            "start_time": float(note["start"]),
            "duration": float(note["duration"]),
            "velocity": float(note.get("velocity", 100)),
            "mute": bool(note.get("mute", False)),
            "probability": float(note.get("probability", 1.0)),
            "velocity_deviation": float(note.get("velocity_deviation", 0.0)),
            "release_velocity": float(note.get("release_velocity", 64)),
        }
        candidates = []
        if self._note_spec_kwargs is not None:
            candidates.append(self._note_spec_kwargs)
        candidates.append(tuple(full.keys()))
        candidates.append(("pitch", "start_time", "duration", "velocity", "mute",
                           "probability", "velocity_deviation"))
        candidates.append(("pitch", "start_time", "duration", "velocity", "mute"))
        last_error = None
        for keys in candidates:
            kwargs = dict((k, full[k]) for k in keys if k in full)
            try:
                spec = Live.Clip.MidiNoteSpecification(**kwargs)
                self._note_spec_kwargs = tuple(keys)
                return spec
            except TypeError as exc:
                last_error = exc
        _log("MidiNoteSpecification kwargs exhausted; __init__ doc: %s"
             % getattr(Live.Clip.MidiNoteSpecification.__init__, "__doc__", "?"))
        raise ProtocolError("internal",
                            "MidiNoteSpecification rejected all kwarg sets: %s"
                            % last_error)

    def _op_edit_notes(self, params):
        clip = self._midi_clip(params.get("path"))
        path = params.get("path")
        add = params.get("add", [])
        update = params.get("update", [])
        remove_ids = params.get("remove_ids", [])
        remove_region = params.get("remove_region")
        if len(add) + len(update) > NOTES_MAX:
            raise ProtocolError("too_large", "more than %d notes in one edit"
                                % NOTES_MAX, path=path)
        counts = {"added": 0, "updated": 0, "removed": 0}
        added_ids = []
        self._undo_begin()
        try:
            # CONTRACT order: remove_region, remove_ids, update, add.
            if remove_region:
                before = len(clip.get_notes_extended(
                    *self._region_args(remove_region, clip)))
                clip.remove_notes_extended(*self._region_args(remove_region, clip))
                counts["removed"] += before
            if remove_ids:
                ids = tuple(int(i) for i in remove_ids)
                clip.remove_notes_by_id(ids)
                counts["removed"] += len(ids)
            if update:
                by_id = {}
                for entry in update:
                    if "id" not in entry:
                        raise ProtocolError("bad_request",
                                            "update entries need an 'id'", path=path)
                    by_id[int(entry["id"])] = entry
                vector = clip.get_notes_by_id(tuple(by_id.keys()))
                found = set()
                for i in range(len(vector)):
                    note = vector[i]
                    entry = by_id[int(note.note_id)]
                    found.add(int(note.note_id))
                    for wire_name, lom_name in NOTE_FIELDS:
                        if wire_name in entry:
                            setattr(note, lom_name, entry[wire_name])
                missing = set(by_id.keys()) - found
                if missing:
                    raise ProtocolError("bad_request",
                                        "unknown note ids: %s" % sorted(missing),
                                        path=path)
                clip.apply_note_modifications(vector)
                counts["updated"] = len(update)
            if add:
                specs = tuple(self._make_note_spec(n) for n in add)
                id_vector = clip.add_new_notes(specs)
                added_ids = [int(id_vector[i]) for i in range(len(id_vector))]
                counts["added"] = len(added_ids)
        except ProtocolError:
            raise
        except Exception as exc:
            raise ProtocolError("live_error", "edit_notes: %s" % exc, path=path)
        finally:
            self._undo_end()
        return {"added_ids": added_ids, "counts": counts}

    # ---- batch -------------------------------------------------------------------

    def _op_batch(self, params):
        ops = params.get("ops")
        stop_on_error = params.get("stop_on_error", True)
        if not isinstance(ops, list) or not ops:
            raise ProtocolError("bad_request", "batch requires non-empty ops list")
        if len(ops) > BATCH_MAX:
            raise ProtocolError("too_large", "batch exceeds %d ops" % BATCH_MAX)
        for sub in ops:
            if not isinstance(sub, dict) or sub.get("op") not in BATCHABLE_OPS:
                raise ProtocolError("unsupported_in_batch",
                                    "batch sub-ops must be one of: %s"
                                    % ", ".join(BATCHABLE_OPS))
        results = []
        failed = False
        mutated = False
        self._undo_begin()
        try:
            for sub in ops:
                if failed and stop_on_error:
                    results.append({"skipped": True})
                    continue
                handler = self._ops[sub["op"]]
                try:
                    results.append({"ok": True, "result": handler(self, sub)})
                    if sub["op"] in ("set", "call", "edit_notes"):
                        mutated = True
                except ProtocolError as exc:
                    results.append({"ok": False, "error": exc.to_error()})
                    failed = True
                except Exception as exc:
                    results.append({"ok": False, "error": {
                        "code": "internal", "message": str(exc)}})
                    failed = True
        finally:
            self._undo_end()
        if failed and stop_on_error and mutated:
            # Atomic-or-absent: undo the single step this batch created — on
            # the NEXT tick, where the step is visible in the undo history
            # (verified: same-tick undo() misses it). Only when a mutating
            # sub-op actually ran, so an all-read failure can never eat the
            # user's own undo history.
            raise _DeferredRollback(results)
        return {"results": results, "rolled_back": False}

    # ---- subscriptions (CONTRACT A.6 subscribe/unsubscribe) ------------------------

    def _op_subscribe(self, params):
        path = params.get("path")
        props = params.get("props")
        if not isinstance(props, list) or not props:
            raise ProtocolError("bad_request", "subscribe requires non-empty props list")
        if len(self._subs) >= SUBS_MAX:
            raise ProtocolError("too_large", "subscription limit (%d) reached" % SUBS_MAX)
        obj = self._resolve(path)
        adders = {}
        for prop in props:
            adder = getattr(obj, "add_%s_listener" % prop, None)
            remover = getattr(obj, "remove_%s_listener" % prop, None)
            if adder is None or remover is None:
                raise ProtocolError("not_listenable",
                                    "%s has no listener for '%s'"
                                    % (type(obj).__name__, prop),
                                    path=path, prop=prop)
            adders[prop] = (adder, remover)
        sub_id = self._next_sub_id
        self._next_sub_id += 1
        sub = {"id": sub_id, "path": path, "obj": obj, "seq": 0, "dropped": 0,
               "listeners": {}}
        for prop, (adder, remover) in adders.items():
            callback = self._make_listener(sub_id, prop)
            adder(callback)
            sub["listeners"][prop] = (remover, callback)
        self._subs[sub_id] = sub
        values = {}
        for prop in props:
            try:
                values[prop] = self._encode(getattr(obj, prop),
                                            path_hint=path + "." + prop)
            except Exception as exc:
                values[prop] = {"$error": {"code": "live_error", "message": str(exc)}}
        return {"sub": sub_id, "values": values}

    def _make_listener(self, sub_id, prop):
        def _callback():
            # Live calls this on its main thread; just mark dirty (A.6).
            self._dirty.add((sub_id, prop))
        return _callback

    def _op_unsubscribe(self, params):
        sub_id = params.get("sub")
        sub = self._subs.pop(sub_id, None)
        if sub is None:
            raise ProtocolError("subscription_not_found",
                                "no subscription %r" % sub_id)
        self._remove_listeners(sub)
        return {}

    def _remove_listeners(self, sub):
        for prop, (remover, callback) in sub["listeners"].items():
            try:
                remover(callback)
            except Exception:
                pass  # object may already be gone

    def _clear_subscriptions(self):
        for sub in list(self._subs.values()):
            self._remove_listeners(sub)
        self._subs.clear()
        self._dirty.clear()

    def _flush_subscriptions(self):
        if not self._dirty:
            dirty = ()
        else:
            dirty = tuple(self._dirty)
            self._dirty.clear()
        client = self._client
        for sub_id, prop in dirty:
            sub = self._subs.get(sub_id)
            if sub is None:
                continue
            try:
                value = self._encode(getattr(sub["obj"], prop),
                                     path_hint=sub["path"] + "." + prop)
            except Exception:
                sub["seq"] += 1
                self._send(client, {"event": "gone", "sub": sub_id,
                                    "seq": sub["seq"], "reason": "path_invalid"},
                           event_sub=sub_id)
                self._subs.pop(sub_id, None)
                self._remove_listeners(sub)
                continue
            sub["seq"] += 1
            self._send(client, {"event": "change", "sub": sub_id, "seq": sub["seq"],
                                "path": sub["path"], "prop": prop, "value": value},
                       event_sub=sub_id)
        for sub in list(self._subs.values()):
            if sub["dropped"]:
                sub["seq"] += 1
                dropped, sub["dropped"] = sub["dropped"], 0
                self._send(client, {"event": "overflow", "sub": sub["id"],
                                    "seq": sub["seq"], "dropped": dropped})

    # ---- dispatch --------------------------------------------------------------

    _ops = {}

    def _dispatch(self, frame, client):
        frame_id = frame.get("id")
        op = frame.get("op")
        handler = self._ops.get(op)
        if handler is None:
            code = "bad_request" if not isinstance(op, str) else "unknown_op"
            return {"id": frame_id, "ok": False, "error": {
                "code": code, "message": "unknown op %r" % (op,)}}
        try:
            result = handler(self, frame)
            return {"id": frame_id, "ok": True, "result": result}
        except _DeferredRollback as deferred:
            self._pending_rollback = {"id": frame_id, "gen": client["gen"],
                                      "results": deferred.results}
            return None  # response is sent by the next tick, after the undo
        except ProtocolError as exc:
            return {"id": frame_id, "ok": False, "error": exc.to_error()}
        except RuntimeError as exc:
            return {"id": frame_id, "ok": False, "error": {
                "code": "live_error", "message": str(exc)}}
        except Exception as exc:
            _log("internal error on op %r:\n%s" % (op, traceback.format_exc()))
            return {"id": frame_id, "ok": False, "error": {
                "code": "internal", "message": "%s: %s" % (type(exc).__name__, exc)}}

    # ---- Live surface interface ---------------------------------------------------

    def update_display(self):
        if not self._running:
            return
        try:
            self._adopt_pending_conn()
            client = self._client
            if client is not None and not client["alive"][0]:
                self._drop_client("connection closed")
                self._clear_subscriptions()
                client = None
            if self._pending_rollback is not None:
                pending, self._pending_rollback = self._pending_rollback, None
                if client is not None and pending["gen"] == client["gen"]:
                    try:
                        undo_hint = self._song().undo()
                        rolled_back = True
                    except Exception as exc:
                        undo_hint = "undo failed: %s" % exc
                        rolled_back = False
                    self._send(client, {"id": pending["id"], "ok": True, "result": {
                        "results": pending["results"],
                        "rolled_back": rolled_back,
                        "undo_hint": self._encode(undo_hint)}})
            if client is not None:
                deadline = time.time() + TICK_OP_BUDGET_S
                handled = 0
                while handled < TICK_OP_MAX and time.time() < deadline:
                    if self._pending_rollback is not None:
                        break  # finish the rollback before any later op
                    with self._lock:
                        if not self._inbox:
                            break
                        gen, frame = self._inbox.popleft()
                    if gen != client["gen"]:
                        continue  # frame from a replaced connection
                    response = self._dispatch(frame, client)
                    if response is not None:
                        self._send(client, response)
                    handled += 1
            self._flush_subscriptions()
        except Exception:
            _log("tick error:\n" + traceback.format_exc())

    def disconnect(self):
        self._running = False
        self._clear_subscriptions()
        try:
            if self._listen_sock is not None:
                self._listen_sock.close()
        except Exception:
            pass
        self._drop_client("script disconnect")
        _log("bridge down")

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


Bridge._ops = {
    "ping": Bridge._op_ping,
    "describe": Bridge._op_describe,
    "get": Bridge._op_get,
    "set": Bridge._op_set,
    "call": Bridge._op_call,
    "get_notes": Bridge._op_get_notes,
    "edit_notes": Bridge._op_edit_notes,
    "batch": Bridge._op_batch,
    "subscribe": Bridge._op_subscribe,
    "unsubscribe": Bridge._op_unsubscribe,
}


def _is_lom_object_vector_or_any(value):
    """True for Boost vectors (they may be empty, so element checks can't decide)."""
    type_name = type(value).__name__
    if type_name.endswith("Vector") or type_name == "Vector":
        return True
    module = getattr(type(value), "__module__", "") or ""
    return module.split(".")[0] in _LIVE_MODULE_NAMES


def _scrub_nan(value):
    if isinstance(value, float) and (value != value or value in (float("inf"),
                                                                 float("-inf"))):
        return None
    if isinstance(value, dict):
        return dict((k, _scrub_nan(v)) for k, v in value.items())
    if isinstance(value, list):
        return [_scrub_nan(v) for v in value]
    return value


def build(c_instance):
    return Bridge(c_instance)
