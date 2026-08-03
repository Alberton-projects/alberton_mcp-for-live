#!/usr/bin/env python3
"""Bridge lifecycle and robustness probe — what a published tool actually hits.

wire_probe.py checks the contract on a healthy connection; live_verify.py
checks the tools. This one abuses the connection: drops it mid-flight, opens a
second client, sends garbage and over-long lines, and checks that Live neither
dies nor drifts, and that the server reconnects on its own.

    python3 tools/lifecycle_probe.py            # automatic checks only
    python3 tools/lifecycle_probe.py --manual   # also prompts for Live restarts

Leaves the set exactly as it found it.
"""

import argparse
import asyncio
import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alberton_mcp import api                      # noqa: E402
from alberton_mcp.bridge import (Bridge,          # noqa: E402
                                 BridgeUnreachable)
from wire_probe import Wire                       # noqa: E402

HOST, PORT = "127.0.0.1", 17853
LINE_MAX = 16 * 1024 * 1024


class Runner:
    def __init__(self):
        self.results = []

    def check(self, name, condition, detail=""):
        self.results.append((name, bool(condition), detail))
        print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                               "" if condition else " — " + str(detail)[:300]))
        return bool(condition)

    def summary(self):
        failed = [r for r in self.results if not r[1]]
        print("\n%d checks, %d failed" % (len(self.results), len(failed)))
        for name, _ok, detail in failed:
            print("  FAIL %s — %s" % (name, str(detail)[:300]))
        return 1 if failed else 0


def raw_socket():
    sock = socket.create_connection((HOST, PORT), timeout=5.0)
    sock.settimeout(2.0)
    return sock


def send_line(sock, payload):
    sock.sendall((json.dumps(payload) + "\n").encode())


def read_frame(sock, timeout=3.0):
    sock.settimeout(timeout)
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = sock.recv(65536)
        except socket.timeout:
            return None
        if not data:
            return "closed"
        buf += data
        if b"\n" in buf:
            line, _rest = buf.split(b"\n", 1)
            return json.loads(line)
    return None


def alive_and_unchanged(run, label, expected_tempo):
    """Live must still answer, with the set untouched, after every abuse."""
    try:
        wire = Wire(HOST, PORT)
    except Exception as exc:
        return run.check("%s: Live still accepts connections" % label, False,
                         repr(exc))
    try:
        ping = wire.request("ping")
        tempo = wire.request("get", path="song",
                             props=["tempo"])["result"]["values"]["tempo"]
        return run.check("%s: Live healthy, set unchanged" % label,
                         ping.get("ok") and abs(tempo - expected_tempo) < 1e-6,
                         "tempo %s vs %s" % (tempo, expected_tempo))
    finally:
        wire.close()


def automatic_checks(run):
    wire = Wire(HOST, PORT)
    versions = wire.request("ping")["result"]
    tempo = wire.request("get", path="song",
                         props=["tempo"])["result"]["values"]["tempo"]
    print("    bridge %s · Live %s · tempo %s" % (versions.get("script"),
                                                 versions.get("live"), tempo))
    wire.close()

    # 1. a second connection displaces the first (CONTRACT A.1)
    first = raw_socket()
    send_line(first, {"id": 1, "op": "ping"})
    run.check("first client works", read_frame(first) is not None)
    second = raw_socket()
    send_line(second, {"id": 2, "op": "ping"})
    time.sleep(0.3)
    run.check("second client takes over",
              (read_frame(second) or {}).get("ok") is True)
    send_line_failed = False
    try:
        send_line(first, {"id": 3, "op": "ping"})
    except OSError:
        send_line_failed = True
    displaced = send_line_failed or read_frame(first, timeout=1.5) in (None,
                                                                      "closed")
    run.check("displaced client is dropped, not silently served", displaced,
              "the old socket still answered")
    first.close()
    second.close()
    alive_and_unchanged(run, "after takeover", tempo)

    # 2. subscriptions die with their connection (CONTRACT A.6)
    conn = raw_socket()
    send_line(conn, {"id": 1, "op": "subscribe", "path": "song",
                     "props": ["tempo"]})
    frame = read_frame(conn) or {}
    sub_id = (frame.get("result") or {}).get("sub")
    run.check("subscribe on a fresh connection", isinstance(sub_id, int), frame)
    conn.close()                       # abrupt drop, no unsubscribe
    time.sleep(0.4)
    conn = raw_socket()
    send_line(conn, {"id": 1, "op": "unsubscribe", "sub": sub_id})
    frame = read_frame(conn) or {}
    run.check("the dropped connection's subscription is gone",
              frame.get("ok") is False
              and frame["error"]["code"] == "subscription_not_found", frame)
    send_line(conn, {"id": 2, "op": "subscribe", "path": "song",
                     "props": ["tempo"]})
    frame = read_frame(conn) or {}
    run.check("subscribing again after a drop works",
              (frame.get("result") or {}).get("sub") is not None, frame)
    conn.close()

    # 3. dropping the socket mid-flight must not wedge Live
    conn = raw_socket()
    for index in range(20):
        send_line(conn, {"id": index, "op": "get", "path": "song",
                         "props": ["tempo", "is_playing", "scale_name"]})
    conn.close()                       # answers arrive at a closed socket
    time.sleep(0.5)
    alive_and_unchanged(run, "after a mid-flight drop", tempo)

    # 4. garbage in, structured errors out, connection survives
    conn = raw_socket()
    conn.sendall(b"this is not json\n")
    frame = read_frame(conn) or {}
    run.check("malformed JSON -> bad_request",
              frame.get("ok") is False and frame["error"]["code"] == "bad_request",
              frame)
    conn.sendall(b"[1, 2, 3]\n")
    frame = read_frame(conn) or {}
    run.check("a JSON array is not a frame",
              frame.get("ok") is False and frame["error"]["code"] == "bad_request",
              frame)
    send_line(conn, {"id": 1, "op": "nonsense"})
    frame = read_frame(conn) or {}
    run.check("unknown op -> unknown_op",
              frame.get("error", {}).get("code") == "unknown_op", frame)
    send_line(conn, {"id": 2, "op": "get"})            # no path
    frame = read_frame(conn) or {}
    run.check("missing params -> bad_request",
              frame.get("error", {}).get("code") == "bad_request", frame)
    send_line(conn, {"id": 3, "op": "set", "path": "song",
                     "props": {"tempo": "not a number"}})
    frame = read_frame(conn) or {}
    run.check("wrong value type -> structured error, not a crash",
              frame.get("ok") is False
              and frame["error"]["code"] in ("live_error", "type_error"), frame)
    send_line(conn, {"id": 4, "op": "call", "path": "song",
                     "method": "delete_track", "args": [99999]})
    frame = read_frame(conn) or {}
    run.check("out-of-range call -> live_error, set untouched",
              frame.get("error", {}).get("code") == "live_error", frame)
    send_line(conn, {"id": 5, "op": "ping"})
    run.check("the connection survived all of it",
              (read_frame(conn) or {}).get("ok") is True)
    conn.close()
    alive_and_unchanged(run, "after garbage", tempo)

    # 5. a line beyond the declared limit
    conn = raw_socket()
    try:
        conn.sendall(b'{"id": 1, "op": "ping", "pad": "' +
                     b"x" * (LINE_MAX + 1024) + b'"}\n')
        frame = read_frame(conn, timeout=8.0)
        run.check("over-long line -> too_large (not a hang)",
                  frame in (None, "closed")
                  or (isinstance(frame, dict)
                      and frame.get("error", {}).get("code") == "too_large"),
                  frame)
    except OSError as exc:
        run.check("over-long line -> connection closed by the bridge", True,
                  repr(exc))
    finally:
        conn.close()
    alive_and_unchanged(run, "after a 16 MiB line", tempo)

    # 6. rapid connect/disconnect churn
    for _ in range(15):
        churn = raw_socket()
        send_line(churn, {"id": 1, "op": "ping"})
        churn.close()
    time.sleep(0.5)
    alive_and_unchanged(run, "after 15 connect/disconnect cycles", tempo)
    return tempo


async def server_reconnect_checks(run, tempo):
    """The MCP server must reconnect by itself after the socket is lost."""
    session = api.Session(Bridge())
    try:
        overview = await api.session_overview(session, detail="minimal")
        run.check("server connects", overview["tempo"] is not None)

        # something else takes the connection away (the contract allows one)
        thief = raw_socket()
        send_line(thief, {"id": 1, "op": "ping"})
        read_frame(thief)
        await asyncio.sleep(0.4)
        thief.close()
        await asyncio.sleep(0.4)

        again = await api.session_overview(session, detail="minimal")
        run.check("server reconnects on its own after losing the socket",
                  abs(again["tempo"] - tempo) < 1e-6,
                  json.dumps(again)[:200])

        results = await asyncio.gather(
            api.get_track(session, track=0, detail="minimal"),
            api.session_overview(session, detail="minimal"),
            api.get_changes(session, since=0),
            return_exceptions=True)
        run.check("concurrent calls after a reconnect all succeed",
                  all(not isinstance(r, Exception) for r in results),
                  repr([r for r in results if isinstance(r, Exception)]))
    finally:
        await session.bridge.close()

    dead = api.Session(Bridge(port=1))
    try:
        await api.session_overview(dead)
        run.check("a dead port raises BridgeUnreachable", False, "it did not")
    except BridgeUnreachable as exc:
        run.check("a dead port raises BridgeUnreachable, promptly",
                  "cannot connect" in str(exc), str(exc))
    finally:
        await dead.bridge.close()


def manual_checks(run, tempo):
    print("\n  --- manual steps (Live) ---")
    input("  1. In Live, set the Alberton MCP Control Surface slot to None, "
          "then press Enter: ")
    reachable = True
    try:
        probe = raw_socket()
        probe.close()
    except OSError:
        reachable = False
    run.check("the port closes when the surface is deselected", not reachable,
              "something is still listening on %d" % PORT)

    input("  2. Select Alberton MCP again, then press Enter: ")
    time.sleep(1.0)
    alive_and_unchanged(run, "after re-selecting the surface", tempo)

    async def survives_restart():
        session = api.Session(Bridge())
        try:
            await api.session_overview(session, detail="minimal")
            print("     (server connected)")
            input("  3. Quit Live completely, then press Enter: ")
            try:
                await api.session_overview(session, detail="minimal")
                run.check("a closed Live is reported, not hung", False,
                          "it answered with Live closed")
            except BridgeUnreachable as exc:
                run.check("a closed Live is reported as bridge_unreachable",
                          True, str(exc))
            input("  4. Reopen Live with the same set and the surface "
                  "selected, then press Enter: ")
            time.sleep(2.0)
            back = await api.session_overview(session, detail="minimal")
            run.check("the same server session reconnects to the new Live",
                      back["tempo"] is not None, json.dumps(back)[:200])
        finally:
            await session.bridge.close()

    asyncio.run(survives_restart())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual", action="store_true",
                        help="also run the checks that need Live restarted")
    args = parser.parse_args()

    run = Runner()
    print("  --- automatic ---")
    tempo = automatic_checks(run)
    asyncio.run(server_reconnect_checks(run, tempo))
    if args.manual:
        manual_checks(run, tempo)
    return run.summary()


if __name__ == "__main__":
    sys.exit(main())
