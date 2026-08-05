#!/usr/bin/env python3
"""Empty, extreme and awkward material — the shapes a real set grows into.

Everything so far was tested on well-formed input. This builds the opposite:
tracks with nothing on them, clips with no notes, names full of quotes and
newlines, values at the edge of their range, and locators that make no sense.

    python3 tools/degenerate_probe.py

Works on one scratch track it creates and deletes. Two degenerate cases cannot
be built from here at all — Live exposes neither freezing nor grouping to the
LOM (is_frozen and is_grouped are read-only) — so those need a human and are
reported as skipped.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alberton_mcp import api                      # noqa: E402
from alberton_mcp.bridge import Bridge            # noqa: E402
from alberton_mcp.errors import ToolError         # noqa: E402
import scratch                                   # noqa: E402

SCRATCH = "ZZ degenerate"

# Names that have to survive JSON, the newline-delimited wire, and Live.
AWKWARD_NAMES = [
    ('quotes', 'he said "hello" and \'bye\''),
    ('backslashes', r"C:\Users\test\path"),
    ('json-ish', '{"op": "set", "path": "song"}'),
    ('newline', "first line\nsecond line"),
    ('tab and cr', "a\tb\rc"),
    ('unicode', "Cançó · Ñandú · Ω≈ç√∫"),
    ('emoji', "🥁 kick 🎹 pad"),
    ('cjk', "ベース・トラック"),
    ('rtl', "مسار الجهير"),
    ('long', "x" * 300),
    ('empty', ""),
    ('spaces only', "   "),
]


class Runner:
    def __init__(self):
        self.results = []
        self.skipped = []

    def check(self, name, condition, detail=""):
        self.results.append((name, bool(condition), detail))
        print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                               "" if condition else " — " + str(detail)[:240]))
        return bool(condition)

    def skip(self, name, why):
        self.skipped.append((name, why))
        print("  [SKIP] %s — %s" % (name, why))

    def summary(self):
        failed = [r for r in self.results if not r[1]]
        print("\n%d checks, %d failed, %d skipped"
              % (len(self.results), len(failed), len(self.skipped)))
        for name, _ok, detail in failed:
            print("  FAIL %s — %s" % (name, str(detail)[:240]))
        for name, why in self.skipped:
            print("  SKIP %s — %s" % (name, why))
        return 1 if failed else 0


async def refuses(run, label, coro, codes=("invalid_argument", "not_found",
                                           "type_error", "live_error",
                                           "conflict", "too_large")):
    try:
        await coro
        return run.check(label, False, "it was accepted")
    except ToolError as exc:
        return run.check(label, exc.code in codes,
                         "code %s: %s" % (exc.code, exc.message))


async def main():
    run = Runner()
    session = api.Session(Bridge())
    index = None
    tempo = None
    try:
        overview = await api.session_overview(session, detail="minimal")
        tempo = overview["tempo"]
        await scratch.sweep(session, api)
        created = await api.create_midi_track(session, name=SCRATCH)
        index = created["track"]["index"]
        await api.set_track(session, track=index, volume={"db": -70})

        # ---------------------------------------------------------------- empty
        print("\nEMPTY THINGS")
        track = await api.get_track(session, track=index)
        run.check("a track with no devices and no clips reads cleanly",
                  track["devices"] == [] and track["clips"] == {},
                  json.dumps(track)[:200])

        await api.create_clip(session, track=index, slot=0, length=4.0,
                              name="empty clip")
        clip = {"track": index, "slot": 0}
        notes = await api.get_notes(session, clip=clip)
        run.check("a clip with no notes returns an empty list, not an error",
                  notes["count"] == 0 and notes["notes"] == [],
                  json.dumps(notes))
        summary = await api.get_notes(session, clip=clip, summary=True)
        run.check("summarising nothing says count 0 and stops there",
                  summary["summary"] == {"count": 0}, json.dumps(summary))
        await api.quantize_clip(session, clip=clip, grid=0.25)
        run.check("quantizing an empty clip is harmless", True)
        # Live refuses the whole removal unless every id is present; the
        # server must say which ones were not.
        try:
            await api.edit_notes(session, clip=clip, remove_ids=[1, 2, 3])
            run.check("removing unknown note ids", False, "it was accepted")
        except ToolError as exc:
            run.check("removing unknown note ids names them",
                      exc.code == "not_found" and "[1, 2, 3]" in exc.message,
                      "%s / %s" % (exc.message, exc.hint))

        arrangement = await api.list_arrangement_clips(session, track=index)
        run.check("no Arrangement clips lists as empty",
                  arrangement["count"] == 0, json.dumps(arrangement))
        nothing = await api.browse(session, query="zzzznosuchdevicezzz")
        run.check("a browse with no matches returns an empty list",
                  nothing["matches"] == [] and nothing["total_matches"] == 0,
                  json.dumps(nothing)[:200])
        changes = await api.get_changes(session)
        run.check("get_changes with no watches",
                  changes["events"] == [] and changes["active_watches"] == {},
                  json.dumps(changes))

        # ---------------------------------------------------------------- names
        print("\nAWKWARD NAMES (write, read back, compare)")
        for label, name in AWKWARD_NAMES:
            try:
                written = await api.set_clip(session, clip=clip, name=name)
            except ToolError as exc:
                run.check("name %s" % label, False, exc.message)
                continue
            back = written["values"].get("name")
            same = back == name
            # Live is entitled to sanitise; what matters is that it round-trips
            # to something stable and the connection survives
            again = await api.get_clip(session, clip=clip)
            stable = again["name"] == back
            run.check("name %-12s %s" % (label, "kept" if same else "sanitised"),
                      stable, "wrote %r, read %r, then %r"
                              % (name[:40], back, again["name"]))
        await api.set_clip(session, clip=clip, name="empty clip")

        # ------------------------------------------------------------- extremes
        print("\nEXTREME VALUES")
        for value in (20.0, 999.0):
            song = await api.set_song(session, tempo=value)
            run.check("tempo at its %s limit (%g)"
                      % ("lower" if value == 20 else "upper", value),
                      abs(song["values"]["tempo"] - value) < 0.01,
                      json.dumps(song))
        await refuses(run, "tempo below the limit is refused",
                      api.set_song(session, tempo=19.0))
        await refuses(run, "tempo above the limit is refused",
                      api.set_song(session, tempo=1000.0))
        await api.set_song(session, tempo=tempo)

        for pan in (-1.0, 1.0):
            written = await api.set_track(session, track=index, pan=pan)
            run.check("pan hard %s" % ("left" if pan < 0 else "right"),
                      abs(written["values"]["panning.value"] - pan) < 1e-6,
                      json.dumps(written))
        await refuses(run, "pan beyond ±1 is refused",
                      api.set_track(session, track=index, pan=1.5))

        extremes = [
            {"pitch": 0, "start": 0.0, "duration": 1.0 / 128, "velocity": 1},
            {"pitch": 127, "start": 0.25, "duration": 0.03125, "velocity": 127},
            {"pitch": 60, "start": 0.5, "duration": 0.25, "velocity": 100,
             "probability": 0.0},
            {"pitch": 61, "start": 0.75, "duration": 0.25, "velocity": 100,
             "probability": 1.0, "velocity_deviation": 127.0},
        ]
        added = await api.edit_notes(session, clip=clip, add=extremes)
        read = await api.get_notes(session, clip=clip)
        by_pitch = {n["pitch"]: n for n in read["notes"]}
        run.check("notes at the edges of every field survive",
                  len(added["added_ids"]) == 4
                  and by_pitch[0]["velocity"] == 1
                  and by_pitch[127]["velocity"] == 127
                  and abs(by_pitch[0]["duration"] - 1.0 / 128) < 1e-9
                  and by_pitch[60]["probability"] == 0.0,
                  json.dumps(read)[:300])

        await refuses(run, "pitch 128 is refused",
                      api.edit_notes(session, clip=clip,
                                     add=[{"pitch": 128, "start": 0,
                                           "duration": 1}]))
        await refuses(run, "a negative start is refused",
                      api.edit_notes(session, clip=clip,
                                     add=[{"pitch": 60, "start": -1,
                                           "duration": 1}]))
        await refuses(run, "a zero duration is refused",
                      api.edit_notes(session, clip=clip,
                                     add=[{"pitch": 60, "start": 0,
                                           "duration": 0}]))
        await api.edit_notes(session, clip=clip,
                             remove_region={"from_time": 0.0,
                                            "time_span": 1000.0})

        # a note far beyond the clip's own length
        await api.edit_notes(session, clip=clip,
                             add=[{"pitch": 60, "start": 900.0,
                                   "duration": 1.0}])
        far = await api.get_notes(session, clip=clip, summary=True)
        run.check("a note past the clip end is kept and summarised",
                  far["summary"]["count"] == 1
                  and far["summary"]["time"]["first_onset"] == 900.0,
                  json.dumps(far)[:250])

        # ------------------------------------------------------------ locators
        print("\nNONSENSE LOCATORS")
        await refuses(run, "a negative track index",
                      api.get_track(session, track=-5))
        await refuses(run, "a huge track index",
                      api.get_track(session, track=99999))
        await refuses(run, "an empty track name",
                      api.get_track(session, track=""))
        await refuses(run, "a whitespace track name",
                      api.get_track(session, track="   "))
        await refuses(run, "a negative slot",
                      api.get_clip(session, clip={"track": index, "slot": -1}))
        await refuses(run, "a clip locator with no slot or time",
                      api.get_clip(session, clip={"track": index}))
        await refuses(run, "a device that is not there",
                      api.set_device_parameter(session, track=index, device=0,
                                               parameter=0, value=1.0))
        await refuses(run, "a rack path into a device with no chains",
                      api.set_device_parameter(session, track=index,
                                               device="0/0/0", parameter=0,
                                               value=1.0))
        await refuses(run, "an Arrangement clip where there is none",
                      api.get_clip(session, clip={"track": index,
                                                  "time": 500.0}))

        # ------------------------------------------------- read-only reporting
        print("\nWHAT ONLY A HUMAN CAN MAKE")
        overview = await api.session_overview(session, detail="standard")
        groups = [t for t in overview["tracks"] if t["type"] == "group"]
        if groups:
            group = await api.get_track(session, track=groups[0]["index"])
            # arm is null on a group: Live has nothing to arm there
            run.check("a group track reads as a group",
                      group["type"] == "group" and group["arm"] is None,
                      json.dumps(group)[:200])
            props = await api.lom_get(session, path=group["path"],
                                      props=["is_foldable", "fold_state",
                                             "can_be_armed"])
            run.check("a group folds and cannot be armed",
                      props["values"]["is_foldable"] is True
                      and props["values"]["can_be_armed"] is False,
                      json.dumps(props["values"]))
            children = [t for t in overview["tracks"]
                        if t["index"] > groups[0]["index"]]
            if children:
                child = await api.lom_get(
                    session, path="song.tracks.%d" % children[0]["index"],
                    props=["is_grouped", "group_track"])
                stub = child["values"].get("group_track") or {}
                run.check("a grouped child points at its parent by identity",
                          child["values"]["is_grouped"] is True
                          and isinstance(stub.get("$obj", {}).get("ptr"), int),
                          json.dumps(child["values"])[:200])
        else:
            run.skip("group track", "none in this set, and Live exposes no way "
                                    "to create one from the LOM (Cmd-G by hand)")
        frozen = [t for t in overview["tracks"] if t.get("frozen")]
        if frozen:
            target = frozen[0]["index"]
            notes = await api.get_notes(session,
                                        clip={"track": target, "slot": 0},
                                        summary=True)
            run.check("a frozen track still reads", notes["summary"]["count"] >= 0,
                      json.dumps(notes)[:200])
            await refuses(run, "creating a clip on a frozen track is refused",
                          api.create_clip(session, track=target, slot=6,
                                          length=4.0, name="nope"))
            written = await api.edit_notes(
                session, clip={"track": target, "slot": 0},
                add=[{"pitch": 60, "start": 0.0, "duration": 0.25}])
            run.check("writing notes to a frozen clip warns that it is silent",
                      "unfrozen" in written.get("warning", ""),
                      json.dumps(written))
            await api.edit_notes(session, clip={"track": target, "slot": 0},
                                 remove_ids=written["added_ids"])
        else:
            run.skip("frozen track", "none in this set; Live exposes no freeze "
                                     "method, so freeze one by hand to cover it")
        state = await api.lom_get(session, path="song.tracks.%d" % index,
                                  props=["is_frozen", "can_be_frozen"])
        run.check("freeze state is readable even though it cannot be set",
                  state["values"]["is_frozen"] is False,
                  json.dumps(state))

        # -------------------------------------------------------------- health
        ping = await api.session_overview(session, detail="minimal")
        run.check("Live is healthy after all of that",
                  abs(ping["tempo"] - tempo) < 0.01, json.dumps(ping["counts"]))
    except Exception as exc:
        run.check("unexpected failure", False, repr(exc))
    finally:
        try:
            if tempo is not None:
                await api.set_song(session, tempo=tempo)
            if index is not None:
                await api.delete_track(session, track=index)
        except Exception as exc:
            print("  cleanup problem: %r" % exc)
        await session.bridge.close()
    return run.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
