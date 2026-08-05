#!/usr/bin/env python3
"""The numbers the contract declares, pushed until they bite.

CONTRACT A.9 promises a batch ceiling, a note ceiling, a subscription ceiling
and an event outbox that overflows rather than growing without bound. None of
them had ever been reached: the stress probe ran a real session at ~12
events/s and never came within two orders of magnitude of the outbox. This
provokes each one deliberately, and measures what Live's main thread does
while the big writes land.

    python3 tools/limits_probe.py

Uses one scratch track it creates and deletes, and restores the transport.
"""

import asyncio
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

from alberton_mcp import api                      # noqa: E402
from alberton_mcp.bridge import Bridge, WireError  # noqa: E402
from alberton_mcp.errors import ToolError         # noqa: E402
import scratch                                   # noqa: E402

HOST, PORT = "127.0.0.1", 17853
SCRATCH = "ZZ limits"


class Runner:
    def __init__(self):
        self.results = []

    def check(self, name, condition, detail=""):
        self.results.append((name, bool(condition), detail))
        print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                               "" if condition else " — " + str(detail)[:280]))
        return bool(condition)

    def summary(self):
        failed = [r for r in self.results if not r[1]]
        print("\n%d checks, %d failed" % (len(self.results), len(failed)))
        for name, _ok, detail in failed:
            print("  FAIL %s — %s" % (name, str(detail)[:280]))
        return 1 if failed else 0


async def ping_ms(session):
    """Measured through the session's own connection: opening a second one
    would displace it, which is exactly what the contract promises and what
    this probe learned the hard way."""
    started = time.time()
    await session.bridge.request("ping")
    return (time.time() - started) * 1000


def overflow_check(run):
    """Fill the script's event outbox by subscribing hard and not reading.

    The cap is on the writer's queue, so it can only be reached by a client
    that stops draining its socket — which is exactly what a stalled or slow
    consumer looks like.
    """
    # A tiny receive buffer means the script's writer blocks almost at once,
    # so its outbox — the thing under test — is the only place events can pile
    # up. With a default-sized buffer the kernel absorbs tens of thousands.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.connect((HOST, PORT))
    sock.settimeout(3.0)
    buf = b""

    def send(payload):
        sock.sendall((json.dumps(payload) + "\n").encode())

    def read_frames(seconds):
        nonlocal buf
        sock.settimeout(seconds)
        deadline = time.time() + seconds
        frames = []
        while time.time() < deadline:
            try:
                data = sock.recv(1 << 20)
            except socket.timeout:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    try:
                        frames.append(json.loads(line))
                    except ValueError:
                        pass
        return frames

    try:
        send({"id": 1, "op": "get", "path": "song", "props": ["is_playing"]})
        was_playing = None
        for frame in read_frames(2.0):
            if frame.get("id") == 1:
                was_playing = frame["result"]["values"]["is_playing"]
        if not was_playing:
            send({"id": 2, "op": "call", "path": "song",
                  "method": "start_playing"})
            read_frames(1.0)

        # 120 subscriptions on the playhead: one event each per ~100 ms tick
        subscribed = 0
        for index in range(120):
            send({"id": 100 + index, "op": "subscribe", "path": "song",
                  "props": ["current_song_time"]})
        for frame in read_frames(4.0):
            if frame.get("ok") and "sub" in (frame.get("result") or {}):
                subscribed += 1
        run.check("the subscription ceiling is enforced at 128",
                  subscribed <= 128, "accepted %d" % subscribed)
        print("      %d subscriptions live, ~%d events per tick"
              % (subscribed, subscribed))

        # stop draining: the queue is now the only place events can go
        print("      not reading for 20 s so the outbox fills…")
        time.sleep(20.0)

        # Drain, but bounded: with a 4 KB receive buffer a large backlog
        # takes minutes to come through, and one overflow notice is enough.
        frames = []
        deadline = time.time() + 40.0
        while time.time() < deadline:
            batch = read_frames(3.0)
            frames.extend(batch)
            if any(f.get("event") == "overflow" for f in batch):
                break
            # An empty read is NOT the end: with a 4 KB receive buffer the
            # backlog arrives in bursts, and the notice sits behind ~4096
            # queued frames. The probe used to give up on the first quiet
            # 3 s, a few hundred frames short of the notice it was looking
            # for. Drain to the deadline. Closed 2026-08-05 (open item 2).
        overflows = [f for f in frames if f.get("event") == "overflow"]
        changes = [f for f in frames if f.get("event") == "change"]
        run.check("a stalled consumer gets overflow, not unbounded growth",
                  bool(overflows),
                  "%d change frames, no overflow among %d frames"
                  % (len(changes), len(frames)))
        if overflows:
            dropped = sum(f.get("dropped", 0) for f in overflows)
            print("      %d overflow notices, %d events dropped, %d changes "
                  "still delivered" % (len(overflows), dropped, len(changes)))
            run.check("every overflow notice says how many it dropped",
                      all(isinstance(f.get("dropped"), int) and f["dropped"] > 0
                          for f in overflows),
                      json.dumps(overflows[:3]))
            run.check("overflow carries a subscription and a sequence",
                      all("sub" in f and "seq" in f for f in overflows),
                      json.dumps(overflows[:3]))

        # Contract 1.1: answers are written ahead of events, so even a
        # connection drowning in its own subscriptions still gets replies.
        # Under 1.0 this ping went unanswered indefinitely.
        send({"id": 900, "op": "ping"})
        answered = [f for f in read_frames(6.0) if f.get("id") == 900]
        run.check("answers outrank events: a saturated connection still replies",
                  bool(answered),
                  "no reply within 6 s — the priority queue is not working")
        sock.close()
        fresh = socket.create_connection((HOST, PORT), timeout=5.0)
        fresh.settimeout(5.0)
        try:
            fresh.sendall(b'{"id": 1, "op": "ping"}\n')
            answer = fresh.recv(65536)
            run.check("the script is unharmed: a new connection answers at once",
                      b'"ok":true' in answer.replace(b" ", b""), answer[:120])
            fresh.sendall(b'{"id": 2, "op": "call", "path": "song", '
                          b'"method": "stop_playing"}\n')
            fresh.recv(65536)
        finally:
            fresh.close()
        return
        if not was_playing:
            send({"id": 901, "op": "call", "path": "song",
                  "method": "stop_playing"})
            read_frames(1.0)
    finally:
        sock.close()


async def main():
    run = Runner()
    session = api.Session(Bridge())
    index = None
    try:
        await scratch.sweep(session, api)
        created = await api.create_midi_track(session, name=SCRATCH)
        index = created["track"]["index"]
        # Live arms a new MIDI track, and if session record happens to be on
        # it will capture a clip into the first slot the moment it appears.
        await api.set_track(session, track=index, volume={"db": -70},
                            arm=False)
        track = await api.get_track(session, track=index)
        slot = next(s for s in range(8) if str(s) not in track["clips"])
        await api.create_clip(session, track=index, slot=slot, length=2048.0,
                              name="limits")
        clip = {"track": index, "slot": slot}

        print("\nBATCH CEILING (256 ops)")
        ops = [{"op": "get", "path": "song", "props": ["tempo"]}
               for _ in range(256)]
        result = await session.bridge.request("batch", ops=ops)
        run.check("a batch of exactly 256 ops is accepted",
                  len(result["results"]) == 256
                  and all(r.get("ok") for r in result["results"]),
                  "got %d results" % len(result["results"]))
        try:
            await session.bridge.request("batch", ops=ops + [ops[0]])
            run.check("a batch of 257 ops is refused", False, "it was accepted")
        except WireError as exc:
            run.check("a batch of 257 ops is refused",
                      exc.code == "too_large", "%s: %s" % (exc.code, exc.message))

        print("\nNOTE CEILING (20 000)")
        baseline = min([await ping_ms(session) for _ in range(3)])
        print("      baseline ping %.0f ms" % baseline)
        for count in (2000, 8000, 16000):
            notes = [{"pitch": 36 + (i % 60), "start": i * 0.125,
                      "duration": 0.1, "velocity": 64}
                     for i in range(count)]
            started = time.time()
            await api.edit_notes(session, clip=clip,
                                 remove_region={"from_time": 0.0,
                                                "time_span": 100000.0})
            await api.edit_notes(session, clip=clip, add=notes)
            elapsed = time.time() - started
            after = await ping_ms(session)
            summary = await api.get_notes(session, clip=clip, summary=True)
            run.check("%5d notes written and summarised" % count,
                      summary["summary"]["count"] == count,
                      json.dumps(summary)[:200])
            print("      %5d notes: %.1f s to write, ping right after "
                  "%.0f ms" % (count, elapsed, after))
            run.check("Live still answers promptly after %d notes" % count,
                      after < 2000, "%.0f ms" % after)

        too_many = [{"pitch": 60, "start": i * 0.01, "duration": 0.01}
                    for i in range(20001)]
        try:
            await api.edit_notes(session, clip=clip, add=too_many)
            run.check("more than 20 000 notes is refused", False,
                      "it was accepted")
        except ToolError as exc:
            run.check("more than 20 000 notes is refused",
                      exc.code == "too_large", exc.message)
    except Exception as exc:
        run.check("unexpected failure", False, repr(exc))
    finally:
        try:
            if index is not None:
                await api.delete_track(session, track=index)
        except Exception as exc:
            print("  cleanup problem: %r" % exc)
        await session.bridge.close()

    # Only now: a raw socket would displace the session's connection.
    print("\nEVENT OVERFLOW")
    try:
        overflow_check(run)
    except Exception as exc:
        run.check("overflow probe ran", False, repr(exc))
    return run.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
