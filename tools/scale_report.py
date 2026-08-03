#!/usr/bin/env python3
"""Measure the server against whatever set is currently open. READ-ONLY.

Answers the questions a synthetic test cannot: how big is an overview of a
real project, how long does the biggest read block Live's main thread, and
does anything approach the declared limits.

    python3 tools/scale_report.py

Touches nothing: no clip, track, parameter or transport state is written.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "src"))

from alberton_mcp import api                       # noqa: E402
from alberton_mcp.bridge import Bridge             # noqa: E402

TOKENS_PER_BYTE = 0.28    # rough English/JSON ratio, for an order of magnitude


class Meter:
    """Wraps bridge.request to count wire calls and time the slowest one."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.original = bridge.request
        self.reset()

    def reset(self):
        self.calls = 0
        self.sub_ops = 0
        self.slowest = 0.0
        self.slowest_what = ""
        self.total = 0.0

    def install(self):
        async def wrapped(op, timeout=15.0, **params):
            started = time.time()
            try:
                return await self.original(op, timeout=timeout, **params)
            finally:
                elapsed = time.time() - started
                self.calls += 1
                self.total += elapsed
                count = len(params.get("ops", [])) if op == "batch" else 1
                self.sub_ops += count
                if elapsed > self.slowest:
                    self.slowest = elapsed
                    self.slowest_what = "%s%s" % (
                        op, " of %d ops" % count if op == "batch" else "")
        self.bridge.request = wrapped

    def restore(self):
        self.bridge.request = self.original


def measure(label, payload, meter, seconds):
    text = json.dumps(payload)
    size = len(text)
    print("  %-28s %7.2f s  %6.1f KB  ~%5d tok  %4d calls / %5d ops  "
          "slowest %.2f s (%s)"
          % (label, seconds, size / 1024, int(size * TOKENS_PER_BYTE),
             meter.calls, meter.sub_ops, meter.slowest, meter.slowest_what))
    return size


async def timed(meter, label, coro):
    meter.reset()
    started = time.time()
    result = await coro
    return measure(label, result, meter, time.time() - started), result


async def main():
    session = api.Session(Bridge())
    bridge = session.bridge
    meter = Meter(bridge)
    try:
        overview = await api.session_overview(session, detail="minimal")
        counts = overview["counts"]
        print("SET: %d tracks, %d scenes, %d returns, tempo %s, %s"
              % (counts["tracks"], counts["scenes"], counts["returns"],
                 overview["tempo"], overview["signature"]))
        meter.install()

        print("\nOVERVIEW COST")
        detailed = None
        for detail in ("minimal", "standard", "full"):
            _size, result = await timed(meter, "session_overview %s" % detail,
                                        api.session_overview(session,
                                                             detail=detail))
            if detail == "standard" and result.get("clips_note"):
                print("    %s" % result["clips_note"])
            if detail == "full":
                detailed = result

        # responsiveness right after the heaviest read
        meter.reset()
        started = time.time()
        await bridge.request("ping")
        print("\n  ping straight after the heaviest read: %.0f ms"
              % ((time.time() - started) * 1000))

        print("\nPER-TRACK")
        busiest = max(detailed["tracks"],
                      key=lambda t: len(t.get("clips") or {}))
        print("    busiest track: %d %r with %d clips, %d devices"
              % (busiest["index"], busiest["name"], len(busiest["clips"] or {}),
                 len(busiest["devices"] or [])))
        await timed(meter, "get_track standard",
                    api.get_track(session, track=busiest["index"]))
        await timed(meter, "get_track full",
                    api.get_track(session, track=busiest["index"],
                                  detail="full"))

        clips = [(t["index"], slot, info)
                 for t in detailed["tracks"]
                 for slot, info in (t.get("clips") or {}).items()
                 if info.get("midi")]
        if clips:
            print("\nNOTES (%d MIDI clips in the set)" % len(clips))
            longest = max(clips, key=lambda c: c[2].get("length") or 0)
            track, slot, info = longest
            locator = {"track": track, "slot": int(slot)}
            print("    longest MIDI clip: track %d slot %s %r, %s beats"
                  % (track, slot, info.get("name"), info.get("length")))
            summary_size, summarised = await timed(
                meter, "get_notes summary",
                api.get_notes(session, clip=locator, summary=True))
            full_size, _full = await timed(
                meter, "get_notes full", api.get_notes(session, clip=locator))
            stats = summarised.get("summary", {})
            if stats.get("count"):
                print("    %d notes, %s per bar, %s"
                      % (stats["count"], stats["time"]["notes_per_bar"][:8],
                         stats["grid"]["verdict"]))
                print("    summary is %.0f%% the size of the full dump"
                      % (100.0 * summary_size / max(full_size, 1)))
        else:
            print("\n(no MIDI clips in this set to summarise)")

        print("\nARRANGEMENT")
        await timed(meter, "list_arrangement_clips",
                    api.list_arrangement_clips(session))

        meter.reset()
        started = time.time()
        await bridge.request("ping")
        print("\nHEALTH: ping %.0f ms, request timeout budget %.0f s"
              % ((time.time() - started) * 1000, 15.0))
    finally:
        meter.restore()
        await bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
