"""Asyncio client for the Alberton bridge (CONTRACT Layer A).

NDJSON over TCP; requests correlated by id; event frames (subscriptions) land
in a ChangeFeed ring buffer. Reconnects lazily on the next request after a
drop. All timeouts per CONTRACT A.9.
"""

import asyncio
import json
import os
from collections import deque

DEFAULT_HOST = os.environ.get("ALBERTON_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("ALBERTON_PORT", "17853"))
CONTRACT_VERSION = "1.1"
CONTRACT_MAJOR = "1"
LINE_MAX = 16 * 1024 * 1024          # what the bridge accepts (CONTRACT A.9)
LINE_LIMIT = LINE_MAX + 1024         # our reader's buffer, a little above it
REQUEST_TIMEOUT = 15.0
FEED_MAXLEN = 10000
INF = float("inf")


class WireError(Exception):
    """A structured error frame from the bridge (CONTRACT A.7)."""

    def __init__(self, error):
        error = error or {}
        super().__init__(error.get("message", "unknown wire error"))
        self.code = error.get("code", "internal")
        self.message = error.get("message", "unknown wire error")
        self.path = error.get("path")
        self.prop = error.get("prop")
        self.method = error.get("method")
        self.raw = error


class BridgeUnreachable(Exception):
    """Transport-level failure: cannot reach or lost the bridge."""


def _nonfinite(value, path="", found=None):
    """Where the NaNs and infinities are, so the error can name them."""
    found = [] if found is None else found
    if isinstance(value, float) and (value != value or value in (INF, -INF)):
        found.append(path or "value")
    elif isinstance(value, dict):
        for key, item in value.items():
            _nonfinite(item, "%s.%s" % (path, key) if path else str(key), found)
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _nonfinite(item, "%s[%d]" % (path, i), found)
    return found[:6]


class ChangeFeed:
    """Server-side ring buffer of subscription events (CONTRACT B.6)."""

    def __init__(self, maxlen=FEED_MAXLEN):
        self._events = deque(maxlen=maxlen)
        self._seq = 0
        self.dropped_total = 0

    def ingest(self, frame):
        self._seq += 1
        event = dict(frame)
        event["feed_seq"] = self._seq
        if len(self._events) == self._events.maxlen:
            self.dropped_total += 1
        self._events.append(event)

    def since(self, seq):
        return [e for e in self._events if e["feed_seq"] > seq]

    @property
    def latest_seq(self):
        return self._seq


class Bridge:
    def __init__(self, host=None, port=None):
        self.host = host or DEFAULT_HOST
        self.port = port or DEFAULT_PORT
        self.feed = ChangeFeed()
        self._reader_task = None
        self._writer = None
        self._pending = {}
        self._next_id = 1
        self._connect_lock = asyncio.Lock()
        self.remote_versions = None
        # The last error the bridge sent with no id to attach it to.
        self._last_wire_complaint = None
        # Bumped on every successful handshake. Anything the caller cached
        # about the far side — subscription ids above all — belongs to one
        # epoch and is void in the next.
        self.epoch = 0

    @property
    def connected(self):
        return self._writer is not None and not self._writer.is_closing()

    async def _ensure_connected(self):
        if self.connected:
            return
        async with self._connect_lock:
            if self.connected:
                return
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, limit=LINE_LIMIT),
                    timeout=5.0)
            except Exception as exc:
                raise BridgeUnreachable(
                    "cannot connect to the bridge at %s:%d (%s)"
                    % (self.host, self.port, exc))
            self._writer = writer
            self._reader_task = asyncio.get_running_loop().create_task(
                self._read_loop(reader))
            versions = await self.request("ping")
            # Minor versions are additive by definition (CONTRACT 1.1), so an
            # older script still works — it just cannot offer what it lacks.
            spoken = str(versions.get("contract", ""))
            if spoken.split(".")[0] != CONTRACT_MAJOR:
                await self.close()
                raise BridgeUnreachable(
                    "contract mismatch: the Remote Script speaks %r, this "
                    "server needs %s.x — reinstall remote_script/Alberton_MCP "
                    "and restart Live" % (spoken, CONTRACT_MAJOR))
            self.contract = spoken
            self.remote_versions = versions
            self.epoch += 1

    async def request(self, op, timeout=REQUEST_TIMEOUT, **params):
        await self._ensure_connected()
        request_id = self._next_id
        frame = {"id": request_id, "op": op}
        frame.update(params)
        # JSON has no NaN and no Infinity. Python writes them as bare words
        # anyway, which is not JSON, and Live does not survive being handed
        # one: the bridge's main-thread pump stops answering and nothing short
        # of reloading the Control Surface revives it. Refuse here, the one
        # place every path goes through — tools, lom_set, the inner calls of a
        # batch — and refuse before any state is registered for this request.
        # Cost of learning this: a wedged Live. 2026-08-04.
        try:
            line = json.dumps(frame, separators=(",", ":"),
                              allow_nan=False) + "\n"
        except ValueError:
            raise WireError({
                "code": "invalid_argument",
                "message": "%s cannot be sent: %s is not a number JSON can "
                           "carry" % (op, ", ".join(_nonfinite(frame)) or "a value"),
                "hint": "NaN and infinity have no JSON form. Send a real "
                        "number, or leave the property out."})
        # Over the limit the bridge answers `too_large` with no id and then
        # drops the connection, so the caller learns only that the connection
        # went — the diagnosis is lost with it. Say it here instead, exactly,
        # and keep the connection.
        size = len(line.encode("utf-8"))
        if size > LINE_MAX:
            raise WireError({
                "code": "too_large",
                "message": "%s would send %d bytes; the bridge accepts %d per "
                           "line" % (op, size, LINE_MAX),
                "hint": "Split it — a batch into smaller batches, a note write "
                        "into several calls."})
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self._writer.write(line.encode("utf-8"))
            await self._writer.drain()
        except Exception as exc:
            self._pending.pop(request_id, None)
            self._drop_connection()
            raise BridgeUnreachable("send failed: %s" % exc)
        try:
            response = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise BridgeUnreachable("no response to '%s' within %.0fs" % (op, timeout))
        if response.get("ok"):
            return response.get("result", {})
        raise WireError(response.get("error"))

    async def _read_loop(self, reader):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if "event" in frame:
                    self.feed.ingest(frame)
                    continue
                if frame.get("id") is None and not frame.get("ok", True):
                    # No id to correlate it with — the line it refers to never
                    # parsed far enough to have one. Keep it: the bridge drops
                    # the connection after some of these, and its reason is
                    # more use than "connection lost".
                    self._last_wire_complaint = frame.get("error") or {}
                    continue
                future = self._pending.pop(frame.get("id"), None)
                if future is not None and not future.done():
                    future.set_result(frame)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                ConnectionError, OSError):
            pass
        finally:
            self._drop_connection()

    def _drop_connection(self):
        writer, self._writer = self._writer, None
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        complaint = self._last_wire_complaint
        self._last_wire_complaint = None
        why = "connection to the bridge lost"
        if complaint:
            why += " after it refused a frame: %s — %s" % (
                complaint.get("code"), complaint.get("message"))
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(BridgeUnreachable(why))

    async def close(self):
        self._drop_connection()
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
