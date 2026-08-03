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
CONTRACT_VERSION = "1.0"
LINE_LIMIT = 16 * 1024 * 1024 + 1024
REQUEST_TIMEOUT = 15.0
FEED_MAXLEN = 10000


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
            if versions.get("contract") != CONTRACT_VERSION:
                await self.close()
                raise BridgeUnreachable(
                    "contract mismatch: bridge speaks %r, server needs %r"
                    % (versions.get("contract"), CONTRACT_VERSION))
            self.remote_versions = versions

    async def request(self, op, timeout=REQUEST_TIMEOUT, **params):
        await self._ensure_connected()
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        frame = {"id": request_id, "op": op}
        frame.update(params)
        try:
            self._writer.write((json.dumps(frame, separators=(",", ":"))
                                + "\n").encode("utf-8"))
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
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(BridgeUnreachable("connection to the bridge lost"))

    async def close(self):
        self._drop_connection()
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
