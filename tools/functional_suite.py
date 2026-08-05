#!/usr/bin/env python3
"""Every tool, against a real Live, on scratch material it cleans up after.

live_verify.py proves the headline paths work; this proves the catalogue
does. It exercises each tool in server.py at least once, checks write ->
read-back on every writable property it touches, verifies undo integrity over
a long chain, and finishes by reporting which tools it never reached — so
coverage is measured, not claimed.

    python3 tools/functional_suite.py

Works only on tracks and scenes it creates at the end of the set, and puts
tempo, signature, scale and transport back. Scratch tracks are silenced
before anything is fired, so running it makes no noise.
"""

import ast
import asyncio
import json
import re
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from alberton_mcp import api                      # noqa: E402
from alberton_mcp.bridge import Bridge            # noqa: E402
from alberton_mcp.errors import ToolError         # noqa: E402
import scratch                                   # noqa: E402

SCRATCH_MIDI = "ZZ scratch midi"
SCRATCH_AUDIO = "ZZ scratch audio"


def catalogue():
    """Tool names as the MCP client sees them, straight from the decorators."""
    tree = ast.parse((ROOT / "server" / "src" / "alberton_mcp"
                      / "server.py").read_text())
    return {node.name for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and any(isinstance(d, ast.Call)
                    and getattr(d.func, "attr", "") == "tool"
                    for d in node.decorator_list)}


class Runner:
    def __init__(self):
        self.results = []
        self.used = set()

    def used_tools(self, *names):
        self.used.update(names)

    def check(self, name, condition, detail="", tools=()):
        self.used.update(tools)
        self.results.append((name, bool(condition), detail))
        print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                               "" if condition else " — " + str(detail)[:280]))
        return bool(condition)

    def section(self, title):
        print("\n%s" % title)

    def summary(self, expected):
        failed = [r for r in self.results if not r[1]]
        missed = sorted(expected - self.used)
        extra = sorted(self.used - expected)
        print("\n%d checks, %d failed" % (len(self.results), len(failed)))
        for name, _ok, detail in failed:
            print("  FAIL %s — %s" % (name, str(detail)[:280]))
        print("coverage: %d of %d tools exercised" % (len(self.used & expected),
                                                      len(expected)))
        if missed:
            print("  NEVER CALLED: %s" % ", ".join(missed))
        if extra:
            print("  unknown names recorded (typo?): %s" % ", ".join(extra))
        return 1 if (failed or missed or extra) else 0


def make_wav(path, seconds=0.5, rate=44100):
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        frames = int(rate * seconds)
        fh.writeframes(b"".join(struct.pack("<h", 8000 if (i // 200) % 2 else -8000)
                                for i in range(frames)))
    return str(path)


NOTES = [{"pitch": 60, "start": 0.0, "duration": 0.5, "velocity": 100},
         {"pitch": 64, "start": 1.0 / 3.0, "duration": 1.0 / 3.0,
          "velocity": 90, "probability": 0.8},
         {"pitch": 67, "start": 1.5, "duration": 0.25, "velocity": 70,
          "velocity_deviation": 5.0}]


async def main():
    run = Runner()
    session = api.Session(Bridge())
    midi_index = audio_index = None
    scenes_added = 0
    original = {}

    try:
        # ---------------------------------------------------------- orientation
        run.section("ORIENTATION")
        overview = await api.session_overview(session, detail="standard")
        original = {"tempo": overview["tempo"],
                    "signature": overview["signature"],
                    "scale": overview["scale"],
                    "tracks": overview["counts"]["tracks"],
                    "scenes": overview["counts"]["scenes"]}
        run.check("session_overview", overview["counts"]["tracks"] > 0,
                  json.dumps(overview)[:200], tools=["session_overview"])
        print("    set: %d tracks, %d scenes, tempo %s %s"
              % (original["tracks"], original["scenes"], original["tempo"],
                 original["signature"]))

        described = await api.lom_describe(session, path="song")
        run.check("lom_describe", described["class"] == "Song",
                  json.dumps(described)[:200], tools=["lom_describe"])
        got = await api.lom_get(session, path="song", props=["tempo", "nonsense"])
        run.check("lom_get reports a bad prop in its slot",
                  "$error" in got["values"]["nonsense"], json.dumps(got)[:200],
                  tools=["lom_get"])

        # ---------------------------------------------------------------- tracks
        run.section("TRACKS")
        await scratch.sweep(session, api)
        created = await api.create_midi_track(session, name=SCRATCH_MIDI,
                                              color="#00A0A0")
        midi_index = created["track"]["index"]
        run.check("create_midi_track", created["track"]["name"] == SCRATCH_MIDI,
                  json.dumps(created), tools=["create_midi_track"])

        created = await api.create_audio_track(session, name=SCRATCH_AUDIO)
        audio_index = created["track"]["index"]
        run.check("create_audio_track", created["track"]["name"] == SCRATCH_AUDIO,
                  json.dumps(created), tools=["create_audio_track"])

        # silence both before anything is fired
        for index in (midi_index, audio_index):
            await api.set_track(session, track=index, volume={"db": -70})
        written = await api.set_track(session, track=midi_index, pan=-0.4,
                                      mute=False, solo=False, arm=False)
        values = written["values"]
        run.check("set_track read-back matches what was written",
                  abs(values.get("panning.value", 0) + 0.4) < 1e-6,
                  json.dumps(written), tools=["set_track"])

        track = await api.get_track(session, track=SCRATCH_MIDI, detail="full")
        run.check("get_track by name, full detail",
                  track["index"] == midi_index and track["type"] == "midi"
                  and isinstance(track["mixer"]["sends"], list),
                  json.dumps(track)[:200], tools=["get_track"])

        duplicated = await api.duplicate_track(session, track=midi_index)
        dup_index = duplicated["track"]["index"]
        run.check("duplicate_track lands right after the original",
                  dup_index == midi_index + 1, json.dumps(duplicated),
                  tools=["duplicate_track"])
        await api.delete_track(session, track=dup_index)
        after = await api.session_overview(session, detail="minimal")
        run.check("delete_track removes exactly one",
                  after["counts"]["tracks"] == original["tracks"] + 2,
                  json.dumps(after["counts"]), tools=["delete_track"])

        # ----------------------------------------------------------------- clips
        run.section("CLIPS AND NOTES")
        made = await api.create_clip(session, track=midi_index, slot=0,
                                     length=8.0, name="fn clip",
                                     color="#C0C000", notes=NOTES,
                                     signature_numerator=7,
                                     signature_denominator=4)
        ids = made["added_note_ids"]
        run.check("create_clip with notes and signature", len(ids) == 3,
                  json.dumps(made), tools=["create_clip"])

        clip = {"track": midi_index, "slot": 0}
        detail = await api.get_clip(session, clip=clip, note_summary=True)
        run.check("get_clip + note_summary",
                  detail["signature"] == "7/4"
                  and detail["note_summary"]["count"] == 3
                  and detail["note_summary"]["max_polyphony"] >= 1,
                  json.dumps(detail)[:300], tools=["get_clip"])

        changed = await api.set_clip(session, clip=clip, name="fn renamed",
                                     looping=True, loop_start=0.0, loop_end=4.0)
        run.check("set_clip read-back",
                  changed["values"]["name"] == "fn renamed"
                  and changed["values"]["loop_end"] == 4.0,
                  json.dumps(changed), tools=["set_clip"])

        windowed = await api.get_notes(session, clip=clip, from_pitch=64,
                                       pitch_span=1)
        run.check("get_notes windows by pitch",
                  windowed["count"] == 1
                  and abs(windowed["notes"][0]["start"] - 1.0 / 3.0) < 1e-9,
                  json.dumps(windowed), tools=["get_notes"])

        edited = await api.edit_notes(session, clip=clip,
                                      update=[{"id": ids[0], "velocity": 40}],
                                      add=[{"pitch": 72, "start": 3.0,
                                            "duration": 0.5}],
                                      remove_ids=[ids[2]])
        run.check("edit_notes update+add+remove in one step",
                  edited["counts"] == {"added": 1, "updated": 1, "removed": 1},
                  json.dumps(edited), tools=["edit_notes"])

        await api.quantize_clip(session, clip=clip, grid=0.25, amount=1.0)
        after_q = await api.get_notes(session, clip=clip, summary=True)
        run.check("quantize_clip pulls everything onto the grid",
                  after_q["summary"]["grid"]["off_grid"] == 0,
                  json.dumps(after_q)[:300], tools=["quantize_clip"])

        await api.duplicate_clip_to_slot(session, clip=clip,
                                         target={"track": midi_index, "slot": 1})
        copied = await api.get_clip(session, clip={"track": midi_index, "slot": 1})
        run.check("duplicate_clip_to_slot copies name and content",
                  copied["name"] == "fn renamed",
                  json.dumps(copied)[:200], tools=["duplicate_clip_to_slot"])
        await api.delete_clip(session, clip={"track": midi_index, "slot": 1})
        try:
            await api.get_clip(session, clip={"track": midi_index, "slot": 1})
            run.check("delete_clip", False, "the clip is still there",
                      tools=["delete_clip"])
        except ToolError as exc:
            run.check("delete_clip empties the slot", exc.code == "not_found",
                      exc.message, tools=["delete_clip"])

        # ---------------------------------------------------------------- scenes
        run.section("SCENES")
        scene = await api.create_scene(session, index=-1, name="ZZ scratch scene",
                                       color="#404080")
        scenes_added += 1
        scene_index = scene["scene"]["index"]
        run.check("create_scene", scene["scene"]["name"] == "ZZ scratch scene",
                  json.dumps(scene), tools=["create_scene"])
        renamed = await api.set_scene(session, scene=scene_index,
                                      name="ZZ renamed scene")
        run.check("set_scene read-back",
                  renamed["values"]["name"] == "ZZ renamed scene",
                  json.dumps(renamed), tools=["set_scene"])
        await api.fire_scene(session, scene=scene_index)
        run.check("fire_scene on an empty scene is harmless", True, "",
                  tools=["fire_scene"])
        await api.stop_all_clips(session)
        await api.delete_scene(session, scene=scene_index)
        scenes_added -= 1
        counts = (await api.session_overview(session, detail="minimal"))["counts"]
        run.check("delete_scene", counts["scenes"] == original["scenes"],
                  json.dumps(counts), tools=["delete_scene", "stop_all_clips"])

        # ------------------------------------------------------------- launching
        run.section("LAUNCH AND TRANSPORT (silenced)")
        await api.fire_clip(session, clip=clip)
        await asyncio.sleep(0.4)
        await api.stop_clip(session, clip=clip)
        await api.stop_all_clips(session, track=midi_index)
        run.check("fire_clip / stop_clip round trip", True, "",
                  tools=["fire_clip", "stop_clip"])

        # firing a clip starts the transport, and a rolling playhead has
        # already moved on by the time it is read back — stop it first
        await api.transport(session, action="stop")
        moved = await api.transport(session, position=8.0)
        run.check("transport moves the playhead when stopped",
                  abs(moved["position"] - 8.0) < 1e-6
                  and moved["is_playing"] is False, json.dumps(moved),
                  tools=["transport"])
        rolling = await api.transport(session, action="play", position=8.0)
        await api.transport(session, action="stop", position=0.0)
        run.check("a rolling transport says why the position drifted",
                  "rolling" in rolling.get("note", ""), json.dumps(rolling))

        view = await api.show_view(session, view="arrangement")
        await api.show_view(session, view="session")
        run.check("show_view", view["view"] == "arrangement", json.dumps(view),
                  tools=["show_view"])

        # ----------------------------------------------------------------- song
        run.section("SONG")
        song = await api.set_song(session, tempo=101.0, signature_numerator=5,
                                  signature_denominator=4, scale_name="Dorian",
                                  root_note=2, metronome=False)
        run.check("set_song read-back across five fields",
                  abs(song["values"]["tempo"] - 101.0) < 0.01
                  and song["values"]["signature_numerator"] == 5
                  and song["values"]["scale_name"] == "Dorian"
                  and song["values"]["root_note"] == 2,
                  json.dumps(song), tools=["set_song"])

        # ------------------------------------------------------------- devices
        run.section("DEVICES AND BROWSER")
        found = await api.browse(session, query="operator",
                                 category="instruments")
        item = next((m for m in found["matches"]
                     if (m["name"] or "").lower().startswith("operator")), None)
        run.check("browse finds a stock instrument", item is not None,
                  json.dumps(found)[:200], tools=["browse"])
        dropped = await api.refresh_browser_index(session, category="instruments")
        run.check("refresh_browser_index drops the cache",
                  dropped["dropped"] == ["instruments"], json.dumps(dropped),
                  tools=["refresh_browser_index"])
        loaded = await api.load_device(session, track=midi_index,
                                       uri=item["uri"])
        run.check("load_device", loaded["device_count_change"] == 1,
                  json.dumps(loaded), tools=["load_device"])
        param = await api.set_device_parameter(session, track=midi_index,
                                               device=0, parameter="Device On",
                                               value=1.0)
        run.check("set_device_parameter read-back",
                  param["parameter"]["value"] == 1.0, json.dumps(param),
                  tools=["set_device_parameter"])

        # ---------------------------------------------------------- automation
        run.section("AUTOMATION")
        track_full = await api.get_track(session, track=midi_index, detail="full")
        info = track_full["devices"][0]["parameters"]
        # detail='full' has to answer this on its own: a caller cannot choose a
        # legal value without the range, and this suite used to ask Live once
        # per parameter to find out.
        run.check("get_track(detail='full') carries value and range",
                  info and all(isinstance(p, dict) and p.get("name")
                               and p.get("value") is not None
                               and p.get("min") is not None
                               and p.get("max") is not None for p in info),
                  json.dumps(info)[:300], tools=["get_track"])
        run.check("get_track(detail='full') marks the stepped parameters",
                  any(p.get("quantized") for p in info),
                  json.dumps([p["name"] for p in info if p.get("quantized")]),
                  tools=["get_track"])
        # A continuous parameter: quantized ones (Operator's Algorithm, 0-10
        # integers) snap, so a smooth ramp cannot read back exactly.
        sweepable = next((p["name"] for p in info
                          if p["name"] != "Device On" and not p.get("quantized")),
                         None)
        quantized = next((p["name"] for p in info if p.get("quantized")
                          and p["name"] != "Device On"), None)
        if sweepable:
            ramp = await api.automate_parameter(
                session, clip=clip, device=0, parameter=sweepable,
                points=[{"time": 0, "value": 0}, {"time": 4, "value": 1}],
                resolution=0.5)
            # The shape must rise from first probe to last. Exactness is only
            # required when the parameter accepted every value asked of it —
            # some round to their own steps whatever is_quantized says.
            read = [p["value"] for p in ramp["read_back"]]
            run.check("automate_parameter ramps, and says if Live snapped it",
                      ramp["steps"] == 9 and read == sorted(read)
                      and read[-1] > read[0]
                      and (ramp["snapped"]
                           or all(abs(p["value"] - p["wrote"]) < 1e-6
                                  for p in ramp["read_back"])),
                      json.dumps(ramp)[:400], tools=["automate_parameter"])
            if quantized:
                stepped = await api.automate_parameter(
                    session, clip=clip, device=0, parameter=quantized,
                    points=[{"time": 0, "value": 0},
                            {"time": 4, "value": 10}], resolution=0.5)
                run.check("a discrete parameter is flagged, not silently snapped",
                          stepped["quantized"] is True
                          and (not stepped["snapped"]
                               or "nearest ones" in stepped.get("note", "")),
                          json.dumps(stepped)[:300])
            held = await api.automate_parameter(
                session, clip=clip, device=0, parameter=sweepable,
                points=[{"time": 0, "value": 1}, {"time": 4, "value": 0}],
                mode="hold")
            run.check("automate_parameter reshapes an existing envelope",
                      held["steps"] == 2, json.dumps(held)[:200])
            cleared = await api.clear_automation(session, clip=clip)
            has = await api.lom_get(session,
                                    path="song.tracks.%d.clip_slots.0.clip"
                                         % midi_index,
                                    props=["has_envelopes"])
            run.check("clear_automation removes every envelope",
                      has["values"]["has_envelopes"] is False,
                      json.dumps({"cleared": cleared, "has": has}),
                      tools=["clear_automation"])
        else:
            run.check("device exposes a sweepable parameter", False,
                      "only Device On; automation not exercised")

        # ------------------------------------------------------------- watches
        run.section("WATCHES")
        watched = await api.watch(session, path="song", props=["tempo"])
        await api.set_song(session, tempo=102.0)
        await asyncio.sleep(0.6)
        changes = await api.get_changes(session, since=0)
        tempo_events = [e for e in changes["events"]
                        if e.get("prop") == "tempo"]
        run.check("watch -> get_changes sees the change",
                  tempo_events and abs(tempo_events[-1]["value"] - 102.0) < 0.01,
                  json.dumps(changes)[:300],
                  tools=["watch", "get_changes"])
        unwatched = await api.unwatch(session, watch_id=watched["watch_id"])
        run.check("unwatch", unwatched["unwatched"] == watched["watch_id"],
                  json.dumps(unwatched), tools=["unwatch"])

        # --------------------------------------------------------- arrangement
        run.section("ARRANGEMENT")
        placed = await api.create_arrangement_clip(
            session, track=midi_index, time=200.0, length=8.0,
            name="fn arrangement", notes=NOTES)
        run.check("create_arrangement_clip",
                  placed["clip"]["start"] == 200.0 and placed["clip"]["end"] == 208.0,
                  json.dumps(placed), tools=["create_arrangement_clip"])
        arr = {"track": midi_index, "time": 202.0}
        trimmed = await api.set_arrangement_clip(session, clip=arr,
                                                 name="fn trimmed", muted=True,
                                                 start_marker=1.0)
        run.check("set_arrangement_clip trims and mutes",
                  trimmed["values"]["name"] == "fn trimmed"
                  and trimmed["values"]["muted"] is True,
                  json.dumps(trimmed), tools=["set_arrangement_clip"])
        listed = await api.list_arrangement_clips(session, track=midi_index)
        run.check("list_arrangement_clips",
                  any(abs((c["start"] or -1) - 200.0) < 1e-6
                      for c in listed["clips"]),
                  json.dumps(listed)[:200], tools=["list_arrangement_clips"])
        await api.duplicate_clip_to_arrangement(session, clip=clip, time=240.0)
        run.check("duplicate_clip_to_arrangement", True, "",
                  tools=["duplicate_clip_to_arrangement"])
        await api.delete_arrangement_clip(session, clip=arr)
        await api.delete_arrangement_clip(session,
                                          clip={"track": midi_index,
                                                "time": 240.0})
        left = await api.list_arrangement_clips(session, track=midi_index)
        run.check("delete_arrangement_clip clears both",
                  left["count"] == 0, json.dumps(left),
                  tools=["delete_arrangement_clip"])

        # ------------------------------------------------------------- audio
        run.section("AUDIO IMPORT")
        with tempfile.TemporaryDirectory() as folder:
            wav = make_wav(Path(folder) / "fn tone.wav")
            imported = await api.import_audio_clip(session, track=audio_index,
                                                   file_path=wav, slot=0,
                                                   name="fn audio")
            run.check("import_audio_clip into a Session slot",
                      imported["clip"]["file"] == wav,
                      json.dumps(imported), tools=["import_audio_clip"])
            audio_clip = {"track": audio_index, "slot": 0}
            props = await api.get_clip(session, clip=audio_clip)
            run.check("audio clip reports its audio-only properties",
                      props["is_midi"] is False and "audio" in props
                      and props["audio"]["file_path"] == wav,
                      json.dumps(props)[:300])

        # -------------------------------------------------------- reference
        run.section("STRUCTURE")
        reference = await api.create_reference_clip(
            session, track=midi_index, slot=2, length=14.0, name="fn form",
            segments=[{"start": 0.0, "label": "A"}, {"start": 7.0, "label": "B"}],
            pulses=[0.0, 2.0, 4.0, 7.0, 9.0, 11.0],
            accents=[0.0, 2.0, 4.0, 7.0, 9.0, 11.0])
        ref_notes = await api.get_notes(session,
                                        clip={"track": midi_index, "slot": 2})
        pitches = sorted(set(n["pitch"] for n in ref_notes["notes"]))
        run.check("create_reference_clip writes three labelled lanes",
                  pitches == [36, 37, 38]
                  and reference["clip"]["name"] == "fn form [0:A 7:B]",
                  json.dumps(reference)[:300], tools=["create_reference_clip"])

        # ------------------------------------------------------------- batch
        run.section("ATOMICITY")
        before_tempo = (await api.lom_get(session, path="song",
                                          props=["tempo"]))["values"]["tempo"]
        good = await api.song_batch(session, calls=[
            {"tool": "set_song", "params": {"tempo": 105.0}},
            {"tool": "set_track", "params": {"track": midi_index,
                                             "name": "ZZ batched"}},
        ])
        run.check("song_batch success",
                  all(c["ok"] for c in good["calls"]) and not good["rolled_back"],
                  json.dumps(good), tools=["song_batch"])
        bad = await api.song_batch(session, calls=[
            {"tool": "set_song", "params": {"tempo": 60.0}},
            {"tool": "edit_notes", "params": {"clip": clip,
                                              "update": [{"id": 88888888,
                                                          "velocity": 1}]}},
        ])
        now_tempo = (await api.lom_get(session, path="song",
                                       props=["tempo"]))["values"]["tempo"]
        run.check("song_batch rolls back atomically",
                  bad["rolled_back"] and abs(now_tempo - 105.0) < 0.01,
                  json.dumps({"batch": bad, "tempo": now_tempo}))
        await api.set_track(session, track=midi_index, name=SCRATCH_MIDI)
        del before_tempo

        # -------------------------------------------------------- lom hatches
        run.section("LOM HATCHES")
        written = await api.lom_set(session,
                                    path="song.tracks.%d" % midi_index,
                                    props={"name": "ZZ via lom"})
        run.check("lom_set writes and reads back",
                  written["values"]["name"] == "ZZ via lom",
                  json.dumps(written), tools=["lom_set"])
        called = await api.lom_call(session, path="app", method="get_major_version")
        run.check("lom_call", called["value"] == 12, json.dumps(called),
                  tools=["lom_call"])
        # Song.is_playing IS writable in Live 12.4.3 — pick one that is not
        try:
            await api.lom_set(session, path="song", props={"can_undo": False})
            run.check("lom_set guards read-only props", False, "it allowed it")
        except ToolError as exc:
            run.check("lom_set refuses a read-only prop before the wire",
                      exc.code == "property_read_only", exc.message)
        await api.set_track(session, track=midi_index, name=SCRATCH_MIDI)

        # ----------------------------------------------------- undo integrity
        run.section("UNDO INTEGRITY")
        marker = "ZZ undo %d"
        for step in range(12):
            await api.set_track(session, track=midi_index, name=marker % step)
        for _ in range(12):
            await api.lom_call(session, path="song", method="undo")
            await asyncio.sleep(0.12)
        restored = await api.get_track(session, track=midi_index,
                                       detail="standard")
        run.check("12 operations, 12 undos, back to the starting name",
                  restored["name"] == SCRATCH_MIDI,
                  "ended as %r" % restored["name"])

    except Exception as exc:
        run.check("unexpected failure", False, repr(exc))
    finally:
        run.section("TEARDOWN")
        try:
            if original.get("tempo") is not None:
                numerator, denominator = original["signature"].split("/")
                await api.set_song(session, tempo=original["tempo"],
                                   signature_numerator=int(numerator),
                                   signature_denominator=int(denominator),
                                   scale_name=original["scale"]["name"],
                                   root_note=original["scale"]["root_note"])
            await api.transport(session, action="stop", position=0.0)
            for index in sorted([i for i in (audio_index, midi_index)
                                 if i is not None], reverse=True):
                await api.delete_track(session, track=index)
            final = await api.session_overview(session, detail="minimal")
            run.check("the set is back to its original shape",
                      final["counts"]["tracks"] == original["tracks"]
                      and final["counts"]["scenes"] == original["scenes"]
                      and abs(final["tempo"] - original["tempo"]) < 0.01,
                      json.dumps({"now": final["counts"], "was": original}))
        except Exception as exc:
            print("  cleanup problem (check by hand): %r" % exc)
        await session.bridge.close()

    return run.summary(catalogue())


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
