#!/usr/bin/env python3
"""Calls shaped the way a *model* gets them wrong, not the way a wire does.

Everything else tests good calls carrying bad values: pan at its limit, slot
-1, a note starting before zero. A language model fails differently. It sends
the wrong *type* — a list where a name goes, "4 bars" where a float goes,
"C3" where a MIDI number goes, a key it misspelled — and it does so on its
first attempt, before it has read anything back.

We know the failure mode is real: the stringified-locator bug (`track: 0`
arriving as `"0"`) was exactly this, and it was found by using the server, not
by testing it.

Aimed at Layer B on purpose. The tool schemas leave the polymorphic parameters
untyped — `track`, `clip`, `device`, `value` — so nothing validates them before
they reach this code, and `song_batch` hands its inner calls straight through.
Those are the doors this walks through.

    python3 tools/malformed_probe.py

A malformed call may do exactly one of two things:

  * be refused with a structured ToolError carrying a known code, or
  * be accepted *deliberately*, as a documented coercion.

Anything else is a finding: a bare Python exception reaches the model as prose
and breaks the contract's promise that failure is machine-readable. So does a
call that quietly acts on the wrong object.

Works on one scratch track it creates and deletes, and restores the tempo.
"""

import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "src"))

from alberton_mcp import api                      # noqa: E402
from alberton_mcp.bridge import Bridge            # noqa: E402
from alberton_mcp.errors import ToolError         # noqa: E402

SCRATCH = "ZZ malformed"

KNOWN_CODES = ("invalid_argument", "not_found", "type_error", "live_error",
               "conflict", "too_large", "unsupported")


class Runner:
    def __init__(self):
        self.results = []
        self.raw = []          # calls that escaped as a bare Python exception

    def check(self, name, condition, detail=""):
        self.results.append((name, bool(condition), detail))
        print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                               "" if condition else " — " + str(detail)[:220]),
              flush=True)
        return bool(condition)

    def summary(self):
        failed = [r for r in self.results if not r[1]]
        print("\n%d checks, %d failed" % (len(self.results), len(failed)))
        for name, _ok, detail in failed:
            print("  FAIL %s — %s" % (name, str(detail)[:220]))
        if self.raw:
            print("\n%d call(s) escaped as a bare Python exception — the model "
                  "would see prose, not a code:" % len(self.raw))
            for name, exc in self.raw:
                print("    %s -> %s: %s" % (name, type(exc).__name__, str(exc)[:140]))
        return 1 if failed else 0


async def rejects(run, label, call):
    """The call must fail, and fail in the contract's shape.

    `call` is a thunk, not a coroutine: building the call has to happen inside
    the try as well, because leaving out a required argument is one of the
    mistakes this probe is about.
    """
    try:
        result = await call()
    except ToolError as exc:
        ok = exc.code in KNOWN_CODES
        return run.check(label, ok, "unknown code %r: %s" % (exc.code, exc.message))
    except Exception as exc:                       # noqa: BLE001 — that IS the finding
        run.raw.append((label, exc))
        return run.check(label, False,
                         "escaped as %s: %s" % (type(exc).__name__, exc))
    return run.check(label, False, "accepted: %s" % json.dumps(result)[:160])


async def accepts(run, label, call, verify=None):
    """A documented coercion: it must work, and land where it says."""
    try:
        result = await call()
    except Exception as exc:                       # noqa: BLE001
        return run.check(label, False, "%s: %s" % (type(exc).__name__, exc))
    if verify is not None:
        return run.check(label, verify(result), json.dumps(result)[:160])
    return run.check(label, True)


class BridgeWedged(Exception):
    """The bridge stopped answering. Nothing after this means anything."""


async def still_alive(run, session, label):
    """A malformed call must not cost us the connection.

    If it did, stop here. Carrying on only adds one 15-second timeout per
    remaining check and buries the call that actually did it.
    """
    try:
        out = await api.session_overview(session, detail="minimal")
    except Exception as exc:                       # noqa: BLE001
        run.check(label, False, "%s: %s" % (type(exc).__name__, exc))
        raise BridgeWedged(label)
    tempo = out.get("tempo")
    ok = isinstance(tempo, float) and math.isfinite(tempo)
    run.check(label, ok, "tempo is now %r" % (tempo,))
    if not ok:
        raise BridgeWedged(label)
    return True


async def main():
    run = Runner()
    session = api.Session(Bridge())
    index = tempo = None
    try:
        overview = await api.session_overview(session, detail="minimal")
        tempo = overview["tempo"]
        created = await api.create_midi_track(session, name=SCRATCH)
        index = created["track"]["index"]
        await api.set_track(session, track=index, volume={"db": -70})
        clip = {"track": index, "slot": 0}
        await api.create_clip(session, track=index, slot=0, length=4.0,
                              name="ZZ m", notes=[{"pitch": 60, "start": 0.0,
                                                   "duration": 1.0}])

        # ------------------------------------------------ locators of the wrong type
        print("\nLOCATORS OF THE WRONG TYPE")
        await rejects(run, "track as a list",
                      lambda: api.get_track(session, track=[index]))
        await rejects(run, "track as a dict",
                      lambda: api.get_track(session, track={"index": index}))
        await rejects(run, "track as None",
                      lambda: api.get_track(session, track=None))
        await rejects(run, "track as a float",
                      lambda: api.get_track(session, track=float(index) + 0.5))
        await rejects(run, "track as an empty string",
                      lambda: api.get_track(session, track=""))
        await rejects(run, "track name that does not exist",
                      lambda: api.get_track(session, track="ZZ nothing is called this"))
        # True == 1 in Python: a bool must not quietly mean track 1
        await rejects(run, "track as a bool never means track 1",
                      lambda: api.get_track(session, track=True))
        await accepts(run, "track as a digit string is a documented coercion",
                      lambda: api.get_track(session, track=str(index)),
                      lambda r: r["index"] == index)
        await still_alive(run, session, "connection survives bad locators")

        # ------------------------------------------------------- values of the wrong type
        print("\nVALUES OF THE WRONG TYPE")
        await rejects(run, "volume as a word",
                      lambda: api.set_track(session, track=index, volume="loud"))
        await rejects(run, "volume dict carrying a word",
                      lambda: api.set_track(session, track=index, volume={"db": "loud"}))
        await rejects(run, "pan as a list",
                      lambda: api.set_track(session, track=index, panning=[0.5]))
        await rejects(run, "tempo as a word",
                      lambda: api.set_song(session, tempo="fast"))
        await rejects(run, "tempo as None",
                      lambda: api.set_song(session, tempo=None))
        await rejects(run, "length as '4 bars'",
                      lambda: api.create_clip(session, track=index, slot=1,
                                      length="4 bars", name="ZZ x"))
        await still_alive(run, session, "connection survives bad values")

        # ---------------------------------------------------------------- non-numbers
        print("\nNUMBERS THAT ARE NOT NUMBERS")
        # These are the ones that cost a wedged Live on 2026-08-04. Each is
        # checked on its own and the tempo is put back straight away, so if one
        # of them ever gets through again we know which, and the set does not
        # keep the damage.
        for label, value in (("NaN", float("nan")),
                             ("infinity", float("inf")),
                             ("negative infinity", float("-inf")),
                             ("1e308", 1e308)):
            await rejects(run, "tempo as %s" % label,
                          lambda v=value: api.set_song(session, tempo=v))
            await still_alive(run, session,
                              "  bridge still answers after %s" % label)
            await api.set_song(session, tempo=tempo)
        await rejects(run, "a non-finite reaching lom_set, where no range guards it",
                      lambda: api.lom_set(session, path="song",
                                          props={"tempo": float("nan")}))
        await still_alive(run, session, "connection survives non-numbers")

        # ------------------------------------------------------ structures with the wrong shape
        print("\nSTRUCTURES WITH THE WRONG SHAPE")
        await rejects(run, "clip locator missing its slot",
                      lambda: api.get_clip(session, clip={"track": index}))
        await rejects(run, "clip locator with a misspelled key",
                      lambda: api.get_clip(session, clip={"trak": index, "slot": 0}))
        await rejects(run, "clip locator as a string",
                      lambda: api.get_clip(session, clip="%d,0" % index))
        await rejects(run, "clip locator as a list",
                      lambda: api.get_clip(session, clip=[index, 0]))
        await rejects(run, "clip locator as an empty dict",
                      lambda: api.get_clip(session, clip={}))
        await still_alive(run, session, "connection survives bad structures")

        # ------------------------------------------------------------------ notes
        print("\nNOTES A MODEL WOULD WRITE")
        await rejects(run, "note missing start and duration",
                      lambda: api.edit_notes(session, clip=clip, add=[{"pitch": 60}]))
        await rejects(run, "notes as one dict instead of a list",
                      lambda: api.edit_notes(session, clip=clip,
                                     add={"pitch": 60, "start": 0.0,
                                          "duration": 1.0}))
        await rejects(run, "pitch as a note name",
                      lambda: api.edit_notes(session, clip=clip,
                                     add=[{"pitch": "C3", "start": 0.0,
                                           "duration": 1.0}]))
        await rejects(run, "start as a bar count string",
                      lambda: api.edit_notes(session, clip=clip,
                                     add=[{"pitch": 60, "start": "bar 2",
                                           "duration": 1.0}]))
        await rejects(run, "duration as a note-value name",
                      lambda: api.edit_notes(session, clip=clip,
                                     add=[{"pitch": 60, "start": 0.0,
                                           "duration": "1/4"}]))
        await rejects(run, "a note that is not a dict",
                      lambda: api.edit_notes(session, clip=clip, add=[60]))
        notes = await api.get_notes(session, clip=clip)
        run.check("nothing malformed was written to the clip",
                  len(notes["notes"]) == 1,
                  json.dumps(notes)[:200])
        await still_alive(run, session, "connection survives bad notes")

        # ------------------------------------------------------------ enums off-domain
        print("\nENUM VALUES THAT DO NOT EXIST")
        await rejects(run, "detail='verbose'",
                      lambda: api.get_track(session, track=index, detail="verbose"))
        await rejects(run, "session_overview detail='everything'",
                      lambda: api.session_overview(session, detail="everything"))

        # ------------------------------------------------------------------ batches
        print("\nBATCHES A MODEL WOULD ASSEMBLE")
        await rejects(run, "calls as a string",
                      lambda: api.song_batch(session, calls="set_song"))
        await rejects(run, "a call with no params",
                      lambda: api.song_batch(session, calls=[{"tool": "set_song"}]))
        await rejects(run, "a call keyed 'name' instead of 'tool'",
                      lambda: api.song_batch(session,
                                     calls=[{"name": "set_song",
                                             "params": {"tempo": 120}}]))
        await rejects(run, "a tool that does not exist",
                      lambda: api.song_batch(session,
                                     calls=[{"tool": "make_it_funky",
                                             "params": {}}]))
        await rejects(run, "a call that is not a dict",
                      lambda: api.song_batch(session, calls=["set_song"]))
        await rejects(run, "a batched call missing a required param",
                      lambda: api.song_batch(
                          session, calls=[{"tool": "create_clip",
                                           "params": {"track": index}}]))
        await rejects(run, "a batchable tool given a malformed param",
                      lambda: api.song_batch(session,
                                     calls=[{"tool": "set_song",
                                             "params": {"tempo": "fast"}}]))
        await still_alive(run, session, "connection survives bad batches")

        # ------------------------------------------------------------- the LOM tools
        print("\nDIRECT LOM TOOLS")
        await rejects(run, "path that is empty",
                      lambda: api.lom_get(session, path="", props=["name"]))
        await rejects(run, "path off the end of a vector",
                      lambda: api.lom_get(session, path="song.tracks.999999",
                                  props=["name"]))
        await rejects(run, "props as a string",
                      lambda: api.lom_get(session, path="song", props="name"))
        await rejects(run, "props as an empty list",
                      lambda: api.lom_get(session, path="song", props=[]))
        await rejects(run, "lom_set given a list instead of a mapping",
                      lambda: api.lom_set(session, path="song",
                                  props=["tempo", 120.0]))
        await rejects(run, "lom_set writing a read-only property",
                      lambda: api.lom_set(session, path="song",
                                  props={"is_playing_bogus": True}))
        await still_alive(run, session, "connection survives bad LOM paths")

        # --------------------------------------------------------------- the set is intact
        print("\nNOTHING MOVED")
        after = await api.session_overview(session, detail="minimal")
        run.check("tempo is untouched after every refused write",
                  abs(after["tempo"] - tempo) < 1e-3,
                  "was %r, now %r" % (tempo, after["tempo"]))
        track = await api.get_track(session, track=index)
        run.check("the scratch track still has exactly one clip",
                  list(track["clips"]) == ["0"], json.dumps(track["clips"])[:160])

    except BridgeWedged as wedged:
        print("\n*** THE BRIDGE STOPPED ANSWERING after: %s" % wedged)
        print("*** Live is probably stuck. Reload the Control Surface slot, or"
              " force-quit Live.")
        print("*** Scratch track %r may be left behind." % SCRATCH, flush=True)
        run.check("the bridge survived the whole probe", False, str(wedged))
    finally:
        try:
            if index is not None:
                await api.delete_track(session, track=index)
            if tempo is not None:
                await api.set_song(session, tempo=tempo)
        except Exception as exc:                    # noqa: BLE001
            print("  cleanup failed (%s): remove %r by hand"
                  % (type(exc).__name__, SCRATCH), flush=True)
        finally:
            await session.bridge.close()

    return run.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
