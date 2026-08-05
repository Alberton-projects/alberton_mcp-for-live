#!/usr/bin/env python3
"""Both hands on the machine at once: the server hammering while a human plays.

Every other suite assumes Live is otherwise idle. This one runs with audio
rolling and expects the user to be dragging faders, switching views and
loading devices at the same time — the only way to reach the subscription
overflow path, and the only test of what a real session looks like.

    python3 tools/stress_probe.py [seconds]

Watches the fastest-moving properties in Live (the playhead above all),
storms the connection with concurrent reads and writes, and samples ping
latency throughout to see whether Live's main thread ever stalls. Writes only
to a scratch track it creates and deletes.
"""

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alberton_mcp import api                      # noqa: E402
from alberton_mcp.bridge import (Bridge,          # noqa: E402
                                 BridgeUnreachable)

SCRATCH = "ZZ stress"
DEFAULT_SECONDS = 90


class Stats:
    def __init__(self):
        self.pings = []
        self.events = 0
        self.changes = 0
        self.overflows = 0
        self.dropped = 0
        self.gone = 0
        self.reads = 0
        self.writes = 0
        self.errors = []
        self.disconnects = 0
        self.watches_lost = 0


async def ping_sampler(session, stats, until):
    """Main-thread health, sampled from outside: a stall shows up here."""
    while time.time() < until:
        started = time.time()
        try:
            await session.bridge.request("ping")
            stats.pings.append((time.time() - started) * 1000)
        except BridgeUnreachable as exc:
            stats.disconnects += 1
            stats.errors.append("ping: %s" % exc)
            await asyncio.sleep(1.0)
        await asyncio.sleep(0.25)


async def read_storm(session, stats, until, track):
    while time.time() < until:
        try:
            await asyncio.gather(
                api.session_overview(session, detail="minimal"),
                api.get_track(session, track=track),
                api.lom_get(session, path="song",
                            props=["current_song_time", "tempo", "is_playing"]),
            )
            stats.reads += 3
        except Exception as exc:
            stats.errors.append("read: %r" % exc)
        await asyncio.sleep(0.15)


async def write_storm(session, stats, until, track):
    step = 0
    while time.time() < until:
        step += 1
        try:
            await api.set_track(session, track=track,
                                name="%s %d" % (SCRATCH, step % 7),
                                pan=((step % 21) - 10) / 10.0)
            stats.writes += 1
        except Exception as exc:
            stats.errors.append("write: %r" % exc)
        await asyncio.sleep(0.3)


async def change_puller(session, stats, until):
    seen = 0
    while time.time() < until:
        try:
            changes = await api.get_changes(session, since=seen)
        except Exception as exc:
            stats.errors.append("changes: %r" % exc)
            await asyncio.sleep(0.5)
            continue
        for event in changes["events"]:
            seen = event["seq"]
            stats.events += 1
            kind = event.get("kind")
            if kind == "change":
                stats.changes += 1
            elif kind == "overflow":
                stats.overflows += 1
                stats.dropped += event.get("dropped", 0)
            elif kind == "gone":
                stats.gone += 1
        if changes.get("watches_dropped") or changes.get("watches_died"):
            stats.watches_lost += 1
        await asyncio.sleep(0.5)


async def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS
    stats = Stats()
    session = api.Session(Bridge())
    index = None
    try:
        overview = await api.session_overview(session, detail="minimal")
        print("set: %d tracks, tempo %s, playing=%s"
              % (overview["counts"]["tracks"], overview["tempo"],
                 overview["is_playing"]))
        await scratch.sweep(session, api)
        created = await api.create_midi_track(session, name=SCRATCH)
        index = created["track"]["index"]

        # The playhead moves every tick while the transport rolls: this is
        # what finally exercises coalescing and the overflow path.
        watches = []
        for path, props in (("song", ["current_song_time", "tempo",
                                      "is_playing", "signature_numerator"]),
                            ("song.tracks.%d" % index, ["name", "mute",
                                                        "solo", "color"]),
                            ("song.master_track", ["name"])):
            watched = await api.watch(session, path=path, props=props)
            watches.append(watched["watch_id"])
        print("watching %d paths (%d props); storming for %d s"
              % (len(watches), 9, seconds))
        print("→ now: play something audible and move things around in Live\n")

        until = time.time() + seconds
        await asyncio.gather(
            ping_sampler(session, stats, until),
            read_storm(session, stats, until, index),
            write_storm(session, stats, until, index),
            change_puller(session, stats, until),
        )

        for watch_id in watches:
            try:
                await api.unwatch(session, watch_id=watch_id)
            except Exception:
                pass
    except Exception as exc:
        stats.errors.append("fatal: %r" % exc)
    finally:
        try:
            if index is not None:
                await api.delete_track(session, track=index)
        except Exception as exc:
            print("cleanup problem: %r" % exc)
        await session.bridge.close()

    print("RESULT after %d s" % seconds)
    print("  wire      : %d reads, %d writes, %d disconnects"
          % (stats.reads, stats.writes, stats.disconnects))
    print("  events    : %d total — %d change, %d overflow (%d dropped), %d gone"
          % (stats.events, stats.changes, stats.overflows, stats.dropped,
             stats.gone))
    print("  watches   : %d reports of a watch being lost" % stats.watches_lost)
    if stats.pings:
        ordered = sorted(stats.pings)
        print("  ping (ms) : median %.0f  p90 %.0f  max %.0f  over %d samples"
              % (statistics.median(ordered),
                 ordered[int(len(ordered) * 0.9)], ordered[-1], len(ordered)))
        stalls = [p for p in stats.pings if p > 1000]
        print("  stalls    : %d samples over 1 s%s"
              % (len(stalls), (" — %s" % [round(s) for s in stalls[:5]])
                 if stalls else ""))
    print("  errors    : %d" % len(stats.errors))
    for error in stats.errors[:8]:
        print("      %s" % error)
    verdict = (not stats.disconnects and not stats.errors)
    print("\n%s" % ("connection held throughout" if verdict
                    else "SOMETHING BROKE — see above"))
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
