#!/usr/bin/env python3
"""Contract-compliance probe for the Alberton bridge (docs/CONTRACT.md, Layer A).

Run outside Live against a running bridge:

    python3 tools/wire_probe.py [--host 127.0.0.1] [--port 17853]

Exercises every operation. Mutations are polite: tempo is restored, and all
created objects live on one throwaway MIDI track that is deleted at the end.
Exit code 0 iff every check passes.
"""

import argparse
import json
import socket
import sys
import time

DEFAULT_PORT = 17853


class Wire(object):
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.sock.settimeout(0.2)
        self.buf = b""
        self.next_id = 1
        self.pending = {}
        self.events = []

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _pump(self, deadline):
        while time.time() < deadline:
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if not line.strip():
                    continue
                frame = json.loads(line.decode("utf-8"))
                if "event" in frame:
                    self.events.append(frame)
                else:
                    self.pending[frame.get("id")] = frame
                return True
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("bridge closed the connection")
            self.buf += data
        return False

    def request(self, op, timeout=10.0, **params):
        frame_id = self.next_id
        self.next_id += 1
        frame = {"id": frame_id, "op": op}
        frame.update(params)
        payload = (json.dumps(frame) + "\n").encode("utf-8")
        self.sock.sendall(payload)
        deadline = time.time() + timeout
        while frame_id not in self.pending:
            if not self._pump(deadline) and frame_id not in self.pending:
                if time.time() >= deadline:
                    raise TimeoutError("no response to %s (id %d)" % (op, frame_id))
        return self.pending.pop(frame_id)

    def wait_event(self, predicate, timeout=4.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for event in list(self.events):
                if predicate(event):
                    self.events.remove(event)
                    return event
            self._pump(time.time() + 0.3)
        return None


class Runner(object):
    def __init__(self):
        self.results = []

    def check(self, name, condition, detail=""):
        self.results.append((name, bool(condition), detail))
        marker = "PASS" if condition else "FAIL"
        print("  [%s] %s%s" % (marker, name, (" — " + detail) if detail and not condition else ""))
        return bool(condition)

    def summary(self):
        failed = [r for r in self.results if not r[1]]
        print()
        print("%d checks, %d failed" % (len(self.results), len(failed)))
        for name, _ok, detail in failed:
            print("  FAIL %s — %s" % (name, detail))
        return 0 if not failed else 1


def ok(resp):
    return resp.get("ok") is True


def err_code(resp):
    return (resp.get("error") or {}).get("code")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    run = Runner()
    wire = Wire(args.host, args.port)
    created_track_index = None
    original_tempo = None

    try:
        # 1. ping / handshake
        resp = wire.request("ping")
        run.check("ping ok", ok(resp), json.dumps(resp))
        result = resp.get("result", {})
        run.check("contract major version 1",
                  str(result.get("contract", "")).split(".")[0] == "1",
                  str(result))
        contract_minor = int(str(result.get("contract", "1.0")).split(".")[1])
        print("    bridge: script %s · Live %s · Python %s" % (
            result.get("script"), result.get("live"), result.get("python")))

        # 2. describe song
        resp = wire.request("describe", path="song")
        props = resp.get("result", {}).get("props", {})
        run.check("describe song", ok(resp) and resp["result"].get("class") == "Song"
                  and "tempo" in props and isinstance(props["tempo"], (int, float)),
                  json.dumps(resp)[:300])
        run.check("describe encodes vectors", isinstance(props.get("tracks"), dict)
                  and "$vec" in props.get("tracks", {}), str(props.get("tracks")))

        if contract_minor >= 1:
            # 1.1: object stubs carry identity as well as location, because a
            # path can be null or can go stale between one op and the next.
            stub = (props.get("master_track") or {}).get("$obj") or {}
            run.check("object stubs carry a stable ptr (1.1)",
                      isinstance(stub.get("ptr"), int), json.dumps(stub))
            second = wire.request("describe", path="song")
            again = ((second.get("result", {}).get("props", {})
                      .get("master_track") or {}).get("$obj") or {})
            run.check("the same object reports the same ptr (1.1)",
                      stub.get("ptr") == again.get("ptr"),
                      "%s vs %s" % (stub.get("ptr"), again.get("ptr")))

        # 3. get
        resp = wire.request("get", path="song", props=["tempo", "is_playing"])
        values = resp.get("result", {}).get("values", {})
        run.check("get song tempo+is_playing", ok(resp)
                  and isinstance(values.get("tempo"), (int, float))
                  and isinstance(values.get("is_playing"), bool),
                  json.dumps(resp)[:300])
        original_tempo = values.get("tempo")

        # 4. set with read-back
        # Live stores tempo as float32 (123.45 -> 123.44999694824219): the
        # read-back exposes the quantization, which is exactly its job. The
        # canonical value is what Live kept; later checks compare against it.
        target = 123.45 if abs((original_tempo or 0) - 123.45) > 0.01 else 111.11
        resp = wire.request("set", path="song", props={"tempo": target})
        read_back = resp.get("result", {}).get("values", {}).get("tempo")
        run.check("set tempo read-back (float32 quantization tolerated)", ok(resp)
                  and read_back is not None and abs(read_back - target) < 0.01,
                  json.dumps(resp)[:300])
        canonical = read_back if read_back is not None else target
        resp = wire.request("get", path="song", props=["tempo"])
        run.check("set persisted", ok(resp)
                  and abs(resp["result"]["values"]["tempo"] - canonical) < 1e-9,
                  json.dumps(resp)[:200])

        # 5. per-prop error slot
        resp = wire.request("get", path="song", props=["tempo", "no_such_prop"])
        slot = resp.get("result", {}).get("values", {}).get("no_such_prop", {})
        run.check("get error slot", ok(resp) and isinstance(slot, dict)
                  and slot.get("$error", {}).get("code") == "property_not_found",
                  json.dumps(resp)[:300])

        # 6. structured path error
        resp = wire.request("get", path="song.tracks.9999", props=["name"])
        run.check("path_not_found", not ok(resp)
                  and err_code(resp) == "path_not_found"
                  and "9999" in resp.get("error", {}).get("message", ""),
                  json.dumps(resp)[:300])

        # 7. read-only rejection
        resp = wire.request("set", path="app", props={"average_process_usage": 0.5})
        run.check("property_read_only", not ok(resp)
                  and err_code(resp) == "property_read_only",
                  json.dumps(resp)[:300])

        # 8. call
        resp = wire.request("call", path="app", method="get_major_version")
        run.check("call get_major_version", ok(resp)
                  and resp["result"].get("value") == 12, json.dumps(resp)[:200])

        # 9. call that returns a LOM object, with canonical path
        resp = wire.request("get", path="song", props=["tracks"])
        # (no assumption on count; create at the end)
        resp = wire.request("call", path="song", method="create_midi_track",
                            args=[-1])
        obj = resp.get("result", {}).get("value", {}).get("$obj", {})
        run.check("create_midi_track returns $obj", ok(resp)
                  and obj.get("class") == "Track", json.dumps(resp)[:300])
        run.check("$obj carries canonical path",
                  isinstance(obj.get("path"), str)
                  and obj["path"].startswith("song.tracks."), str(obj))
        track_path = obj.get("path")
        created_track_index = int(track_path.rsplit(".", 1)[1]) if track_path else None

        # 10. set on the new track
        resp = wire.request("set", path=track_path,
                            props={"name": "Alberton probe", "color": 16725558})
        run.check("set track name/color read-back", ok(resp)
                  and resp["result"]["values"].get("name") == "Alberton probe",
                  json.dumps(resp)[:300])

        # 11. create a clip and edit notes
        slot_path = track_path + ".clip_slots.0"
        resp = wire.request("call", path=slot_path, method="create_clip",
                            args=[8.0])
        run.check("create_clip", ok(resp), json.dumps(resp)[:300])
        clip_path = slot_path + ".clip"
        notes = [
            {"pitch": 60, "start": 0.0, "duration": 0.5, "velocity": 100},
            {"pitch": 64, "start": 0.5, "duration": 0.5, "velocity": 90},
            {"pitch": 67, "start": 1.0, "duration": 1.0 / 3.0, "velocity": 80},
            {"pitch": 69, "start": 1.0 + 1.0 / 3.0, "duration": 1.0 / 3.0,
             "velocity": 80, "probability": 0.75},
            {"pitch": 72, "start": 1.0 + 2.0 / 3.0, "duration": 1.0 / 3.0,
             "velocity": 80, "velocity_deviation": 10.0},
        ]
        resp = wire.request("edit_notes", path=clip_path, add=notes)
        added = resp.get("result", {}).get("added_ids", [])
        run.check("edit_notes add 5", ok(resp) and len(added) == 5,
                  json.dumps(resp)[:300])

        resp = wire.request("get_notes", path=clip_path)
        got = resp.get("result", {}).get("notes", [])
        run.check("get_notes count", ok(resp) and len(got) == 5,
                  json.dumps(resp)[:300])
        triplet = [n for n in got if n["pitch"] == 69]
        run.check("triplet float survives round-trip", len(triplet) == 1 and
                  abs(triplet[0]["start"] - (1.0 + 1.0 / 3.0)) < 1e-9 and
                  abs(triplet[0]["probability"] - 0.75) < 1e-9,
                  json.dumps(triplet))

        first_id = added[0]
        resp = wire.request("edit_notes", path=clip_path,
                            update=[{"id": first_id, "velocity": 45}])
        run.check("edit_notes update", ok(resp)
                  and resp["result"]["counts"]["updated"] == 1,
                  json.dumps(resp)[:300])
        resp = wire.request("get_notes", path=clip_path, from_pitch=60,
                            pitch_span=1)
        got = resp.get("result", {}).get("notes", [])
        run.check("update visible", len(got) == 1
                  and abs(got[0]["velocity"] - 45) < 1e-6 and got[0]["id"] == first_id,
                  json.dumps(got))

        resp = wire.request("edit_notes", path=clip_path, remove_ids=[added[1]])
        run.check("edit_notes remove_ids", ok(resp)
                  and resp["result"]["counts"]["removed"] == 1,
                  json.dumps(resp)[:300])
        resp = wire.request("edit_notes", path=clip_path,
                            remove_region={"from_time": 0.0, "time_span": 100.0})
        run.check("edit_notes remove_region", ok(resp)
                  and resp["result"]["counts"]["removed"] == 4,
                  json.dumps(resp)[:300])
        resp = wire.request("get_notes", path=clip_path)
        run.check("clip empty after removes",
                  ok(resp) and len(resp["result"]["notes"]) == 0,
                  json.dumps(resp)[:200])

        # 12. batch atomicity: second sub-op fails -> rollback of the first
        resp = wire.request("batch", ops=[
            {"op": "set", "path": "song", "props": {"tempo": 87.5}},
            {"op": "set", "path": "song", "props": {"bogus_prop": 1}},
        ])
        result = resp.get("result", {})
        sub_results = result.get("results", [])
        run.check("batch reports per-op results", ok(resp)
                  and len(sub_results) == 2 and sub_results[0].get("ok") is True
                  and sub_results[1].get("ok") is False
                  and sub_results[1]["error"]["code"] == "property_not_found",
                  json.dumps(resp)[:400])
        run.check("batch rolled back", result.get("rolled_back") is True,
                  json.dumps(result)[:300])
        resp = wire.request("get", path="song", props=["tempo"])
        run.check("tempo unchanged after rollback",
                  ok(resp) and abs(resp["result"]["values"]["tempo"] - canonical) < 1e-9,
                  "expected %s, got %s" % (canonical, resp))
        run.check("rollback names the undone step",
                  isinstance(result.get("undo_hint"), str) and result["undo_hint"],
                  json.dumps(result)[:300])

        # 13. batch success is atomic-and-present
        resp = wire.request("batch", ops=[
            {"op": "set", "path": track_path, "props": {"name": "Alberton probe 2"}},
            {"op": "get", "path": track_path, "props": ["name"]},
        ])
        sub_results = resp.get("result", {}).get("results", [])
        run.check("batch success", ok(resp)
                  and resp["result"].get("rolled_back") is False
                  and len(sub_results) == 2
                  and sub_results[1]["result"]["values"]["name"] == "Alberton probe 2",
                  json.dumps(resp)[:400])

        # 14. subscriptions: change event arrives, coalesced, with seq
        resp = wire.request("subscribe", path="song", props=["tempo"])
        sub_id = resp.get("result", {}).get("sub")
        run.check("subscribe returns sub + current value", ok(resp)
                  and isinstance(sub_id, int)
                  and abs(resp["result"]["values"]["tempo"] - canonical) < 1e-9,
                  json.dumps(resp)[:300])
        resp = wire.request("set", path="song", props={"tempo": 95.0})
        run.check("set during subscription", ok(resp), json.dumps(resp)[:200])
        event = wire.wait_event(lambda e: e.get("event") == "change"
                                and e.get("sub") == sub_id
                                and e.get("prop") == "tempo")
        run.check("change event received", event is not None
                  and abs(event.get("value", 0) - 95.0) < 1e-6
                  and isinstance(event.get("seq"), int),
                  json.dumps(event) if event else "no event within timeout")
        resp = wire.request("unsubscribe", sub=sub_id)
        run.check("unsubscribe", ok(resp), json.dumps(resp)[:200])
        resp = wire.request("unsubscribe", sub=sub_id)
        run.check("unsubscribe twice -> subscription_not_found",
                  not ok(resp) and err_code(resp) == "subscription_not_found",
                  json.dumps(resp)[:200])

        # 15. not_listenable
        resp = wire.request("subscribe", path="song", props=["no_such_prop"])
        run.check("not_listenable", not ok(resp)
                  and err_code(resp) in ("not_listenable",),
                  json.dumps(resp)[:200])

    finally:
        # Cleanup: restore tempo, delete the probe track. Best effort.
        try:
            if original_tempo is not None:
                wire.request("set", path="song", props={"tempo": original_tempo})
            if created_track_index is not None:
                wire.request("call", path="song", method="delete_track",
                             args=[created_track_index])
        except Exception as exc:
            print("cleanup problem (finish by hand if needed): %s" % exc)
        wire.close()

    return run.summary()


if __name__ == "__main__":
    sys.exit(main())
