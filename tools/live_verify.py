#!/usr/bin/env python3
"""End-to-end verification of the MCP server's api against a REAL Live.

Exercises the Layer B implementations (the same code the MCP tools call)
over the real bridge. Polite and silent: nothing is fired or played, tempo is
restored, and everything created lives on one temp track deleted at the end.

    python3 tools/live_verify.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "src"))

from alberton_mcp import api                      # noqa: E402
from alberton_mcp.bridge import Bridge            # noqa: E402
from alberton_mcp.errors import ToolError         # noqa: E402


class Runner:
    def __init__(self):
        self.results = []

    def check(self, name, condition, detail=""):
        self.results.append((name, bool(condition), detail))
        print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                               ("" if condition else " — " + str(detail)[:300])))
        return bool(condition)

    def summary(self):
        failed = [r for r in self.results if not r[1]]
        print("\n%d checks, %d failed" % (len(self.results), len(failed)))
        for name, _ok, detail in failed:
            print("  FAIL %s — %s" % (name, str(detail)[:300]))
        return 1 if failed else 0


async def main():
    run = Runner()
    session = api.Session(Bridge())
    bridge = session.bridge
    track_index = None
    original_tempo = None
    try:
        overview = await api.session_overview(session)
        original_tempo = overview["tempo"]
        run.check("session_overview", isinstance(overview["tempo"], float)
                  and overview["counts"]["tracks"] >= 1
                  and len(overview["tracks"]) == overview["counts"]["tracks"],
                  json.dumps(overview)[:300])
        print("    set: %d tracks, %d scenes, tempo %s, %s" % (
            overview["counts"]["tracks"], overview["counts"]["scenes"],
            overview["tempo"], overview["signature"]))

        created = await api.create_midi_track(session,
                                              name="Alberton MCP verify",
                                              color="#FF8800")
        track_index = created["track"]["index"]
        # Live snaps track/clip colors to its palette; read-back shows what
        # it kept (e.g. #FF8800 -> #F66C03). Verified 2026-08-03.
        run.check("create_midi_track (palette snap tolerated)",
                  created["track"]["name"] == "Alberton MCP verify"
                  and isinstance(created["track"]["color"], str)
                  and created["track"]["color"].startswith("#"),
                  json.dumps(created))

        notes = [
            {"pitch": 60, "start": 0.0, "duration": 0.5, "velocity": 100},
            {"pitch": 64, "start": 1.0 + 1.0 / 3.0, "duration": 1.0 / 3.0,
             "velocity": 90, "probability": 0.75},
            {"pitch": 67, "start": 1.0 + 2.0 / 3.0, "duration": 1.0 / 3.0,
             "velocity": 80},
        ]
        clip = await api.create_clip(session, track=track_index, slot=0,
                                     length=8.0, name="verify motif",
                                     color="#00CCAA", notes=notes)
        run.check("create_clip atomic + ids",
                  len(clip["added_note_ids"]) == 3
                  and clip["clip"]["name"] == "verify motif"
                  and clip["clip"]["length"] == 8.0, json.dumps(clip))

        detail = await api.get_clip(session,
                                    clip={"track": track_index, "slot": 0},
                                    include_notes=True)
        triplet = [n for n in detail["notes"] if n["pitch"] == 64]
        run.check("get_clip notes + exact triplet",
                  len(detail["notes"]) == 3 and len(triplet) == 1
                  and abs(triplet[0]["start"] - (1.0 + 1.0 / 3.0)) < 1e-9
                  and abs(triplet[0]["probability"] - 0.75) < 1e-9,
                  json.dumps(detail)[:400])

        first_id = clip["added_note_ids"][0]
        edited = await api.edit_notes(session,
                                      clip={"track": track_index, "slot": 0},
                                      update=[{"id": first_id,
                                               "velocity": 33}])
        got = await api.get_notes(session,
                                  clip={"track": track_index, "slot": 0},
                                  from_pitch=60, pitch_span=1)
        run.check("edit_notes update by id",
                  edited["counts"]["updated"] == 1
                  and got["count"] == 1
                  and abs(got["notes"][0]["velocity"] - 33) < 1e-6,
                  json.dumps(got))

        mixed = await api.set_track(session, track=track_index,
                                    volume={"db": -6.0}, pan=-0.3,
                                    mute=True)
        display = mixed["values"].get("volume.display_value")
        run.check("set_track dB via numeric display_value",
                  isinstance(display, (int, float))
                  and abs(display - (-6.0)) < 0.05,
                  json.dumps(mixed))

        watched = await api.watch(session, path="song", props=["tempo"])
        target = 97.0 if abs(original_tempo - 97.0) > 0.5 else 103.0
        await api.set_song(session, tempo=target)
        await asyncio.sleep(0.6)  # bridge flushes on its ~100 ms tick
        changes = await api.get_changes(session, since=0)
        tempo_events = [e for e in changes["events"]
                        if e["kind"] == "change" and e.get("prop") == "tempo"]
        run.check("watch -> get_changes sees the tempo change",
                  tempo_events and abs(tempo_events[-1]["value"] - target) < 0.01,
                  json.dumps(changes)[:400])
        await api.unwatch(session, watch_id=watched["watch_id"])

        canonical = (await api.set_song(session, tempo=target))["values"]["tempo"]
        batch = await api.song_batch(session, calls=[
            {"tool": "set_song", "params": {"tempo": 66.0}},
            {"tool": "edit_notes",
             "params": {"clip": {"track": track_index, "slot": 0},
                        "update": [{"id": 99999999, "velocity": 1}]}},
        ])
        after = await api.lom_get(session, path="song", props=["tempo"])
        run.check("song_batch rolls back atomically",
                  batch["rolled_back"] is True
                  and batch["calls"][0]["ok"] is True
                  and batch["calls"][1]["ok"] is False
                  and abs(after["values"]["tempo"] - canonical) < 1e-9,
                  json.dumps({"batch": batch, "after": after}))
        run.check("rollback names the step",
                  isinstance(batch.get("undo_hint"), str)
                  and batch["undo_hint"], json.dumps(batch)[:200])

        reference = await api.create_reference_clip(
            session, track=track_index, slot=1, length=16.0, name="form",
            color="#8888FF",
            segments=[{"start": 0.0, "label": "A"},
                      {"start": 8.0, "label": "B"}],
            pulses=[0.0, 4.0, 8.0, 12.0], accents=[0.0, 8.0])
        ref_clip = await api.get_clip(session,
                                      clip={"track": track_index, "slot": 1},
                                      include_notes=True)
        pitches = sorted(set(n["pitch"] for n in ref_clip["notes"]))
        run.check("create_reference_clip lanes + labels",
                  pitches == [36, 37, 38]
                  and ref_clip["name"] == "form [0:A 8:B]",
                  json.dumps(ref_clip)[:300])
        del reference

        placed = await api.duplicate_clip_to_arrangement(
            session, clip={"track": track_index, "slot": 0}, time=32.0)
        arrangement = await api.list_arrangement_clips(session,
                                                       track=track_index)
        run.check("duplicate_clip_to_arrangement + list",
                  arrangement["count"] >= 1
                  and any(abs((c["start"] or -1) - 32.0) < 1e-6
                          for c in arrangement["clips"]),
                  json.dumps({"placed": placed,
                              "arrangement": arrangement})[:400])

        print("    walking the instruments browser (first time takes a moment)…")
        found = await api.browse(session, query="operator",
                                 category="instruments")
        run.check("browse finds Operator",
                  any((m["name"] or "").lower().startswith("operator")
                      for m in found["matches"]),
                  json.dumps(found)[:400])
        loadable = next((m for m in found["matches"]
                         if (m["name"] or "").lower().startswith("operator")),
                        None)
        if loadable:
            loaded = await api.load_device(session, track=track_index,
                                           uri=loadable["uri"])
            run.check("load_device onto the temp track",
                      loaded["device_count_change"] == 1
                      and loaded["devices_now"], json.dumps(loaded))
            parameter = await api.set_device_parameter(
                session, track=track_index, device=0, parameter="Device On",
                value=0.0)
            run.check("set_device_parameter read-back",
                      parameter["parameter"]["value"] == 0.0,
                      json.dumps(parameter))

    except (ToolError, Exception) as exc:
        run.check("unexpected failure", False, repr(exc))
    finally:
        try:
            if original_tempo is not None:
                await api.set_song(session, tempo=original_tempo)
            if track_index is not None:
                await api.delete_track(session, track=track_index)
                print("    cleanup: temp track deleted, tempo restored")
        except Exception as exc:
            print("    cleanup problem (finish by hand): %r" % exc)
        await bridge.close()
    return run.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
