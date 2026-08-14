"""Host-side client for the bridge (see docs/PROTOCOL.md).

Usage::

    from client import Bridge

    with Bridge() as live:
        print(live.hello())
        print(live.get("live_set", "tempo"))
        live.set("live_set", "tempo", 100.0)
        live.call("live_set", "start_playing")

Stdlib-only, any Python 3.9+.
"""
from __future__ import annotations

import itertools
import os
import socket
import sys

# the wire codec is shared with the remote script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from remote_script import framing  # noqa: E402

DEFAULT_ADDR = ("127.0.0.1", 8766)


class BridgeError(RuntimeError):
    """A structured error response from the bridge."""

    def __init__(self, error: dict):
        super().__init__(f"[{error.get('type')}] {error.get('message')}")
        self.error = error or {}
        self.type = self.error.get("type")
        self.message = self.error.get("message")
        self.detail = self.error.get("detail")


class Bridge:
    def __init__(self, host: str = DEFAULT_ADDR[0], port: int = DEFAULT_ADDR[1],
                 timeout: float = 20.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._ids = itertools.count(1)
        self.events: list[dict] = []  # events received while waiting for responses

    # --- lifecycle -------------------------------------------------------------
    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- core ------------------------------------------------------------------
    def request(self, method: str, **params):
        rid = next(self._ids)
        framing.write_frame(self._sock, {"id": rid, "method": method, "params": params})
        while True:
            msg = framing.read_frame(self._sock)
            if msg is None:
                raise ConnectionError("bridge closed the connection")
            if msg.get("event"):
                self.events.append(msg)  # event arrived mid-request — keep it
                continue
            if msg.get("id") != rid:
                raise ConnectionError(f"response id {msg.get('id')} != request id {rid}")
            if not msg.get("ok"):
                raise BridgeError(msg.get("error") or {})
            return msg.get("result")

    def wait_event(self, timeout: float = 5.0):
        """Block until the next event arrives (or return a buffered one).

        Returns the event dict, or ``None`` on timeout.
        """
        if self.events:
            return self.events.pop(0)
        previous = self._sock.gettimeout()
        self._sock.settimeout(timeout)
        try:
            msg = framing.read_frame(self._sock)
        except (TimeoutError, socket.timeout):
            return None
        finally:
            self._sock.settimeout(previous)
        if msg is None:
            raise ConnectionError("bridge closed the connection")
        if msg.get("event"):
            return msg
        raise ConnectionError(f"unexpected non-event frame while idle: {msg}")

    def drain_events(self, timeout: float = 0.0) -> list[dict]:
        """Take every event buffered so far, without blocking.

        ``wait_event`` returns one and blocks; a caller polling "what changed since
        last time?" wants the whole backlog and no wait. With ``timeout`` > 0 and an
        empty buffer, waits that long for the first event, then drains the rest.
        """
        drained = list(self.events)
        self.events.clear()
        if not drained and timeout > 0:
            first = self.wait_event(timeout)
            if first:
                drained.append(first)
        # Sweep up anything that landed while we were waiting.
        previous = self._sock.gettimeout()
        self._sock.settimeout(0.01)
        try:
            while True:
                try:
                    msg = framing.read_frame(self._sock)
                except (TimeoutError, socket.timeout):
                    break
                if msg is None:
                    break
                (drained if msg.get("event") else self.events).append(msg)
        finally:
            self._sock.settimeout(previous)
        return drained

    # --- the generic primitives --------------------------------------------------
    def hello(self):
        return self.request("hello")

    def ping(self):
        return self.request("ping")

    def get(self, path: str, prop: str):
        return self.request("get", path=path, prop=prop)

    def set(self, path: str, prop: str, value):
        return self.request("set", path=path, prop=prop, value=value)

    def call(self, path: str, func: str, *args):
        return self.request("call", path=path, func=func, args=list(args))

    def resolve(self, path: str):
        return self.request("resolve", path=path)

    def children(self, path: str):
        return self.request("children", path=path)

    # --- batch: many ops, one round-trip -----------------------------------------
    def batch(self, ops: list) -> list:
        """Run many ops in ONE round-trip (one hop onto Live's main thread).

        ``ops`` is a list of ``{"method": ..., "params": {...}}``. Returns a
        per-op list of ``{"ok": True, "result": ...}`` / ``{"ok": False,
        "error": ...}`` — one bad op never aborts the rest.
        """
        return self.request("batch", ops=ops)

    def get_many(self, pairs: list) -> list:
        """Read many ``(path, prop)`` pairs in one round-trip; returns values.

        Strict: raises ``BridgeError`` (naming the failing op) if ANY read
        failed — use ``batch`` directly for per-op error handling. Requests
        beyond the per-batch cap are chunked transparently.
        """
        ops = [{"method": "get", "params": {"path": p, "prop": pr}} for p, pr in pairs]
        return self._batched(ops)

    def set_many(self, triples: list) -> bool:
        """Write many ``(path, prop, value)`` triples in one round-trip (strict)."""
        ops = [{"method": "set", "params": {"path": p, "prop": pr, "value": v}}
               for p, pr, v in triples]
        self._batched(ops)
        return True

    _BATCH_CHUNK = 500  # mirrors remote_script.dispatch.MAX_BATCH_OPS

    def _batched(self, ops: list) -> list:
        out = []
        for at in range(0, len(ops), self._BATCH_CHUNK):
            chunk = ops[at:at + self._BATCH_CHUNK]
            out.extend(_unwrap(self.batch(chunk), chunk, offset=at))
        return out

    # --- observers ----------------------------------------------------------------
    def observe(self, path: str, prop: str) -> int:
        """Subscribe to property changes. Returns the subscription id."""
        return self.request("observe", path=path, prop=prop)["sub"]

    def unobserve(self, sub: int):
        return self.request("unobserve", sub=sub)


def _unwrap(results: list, ops: list, offset: int = 0) -> list:
    """Turn per-op batch results into plain values; raise on the first failure."""
    out = []
    for i, r in enumerate(results):
        if not r.get("ok"):
            error = dict(r.get("error") or {})
            op = ops[i] if i < len(ops) else {}
            where = f"{op.get('method')} {op.get('params', {}).get('path', '')}".strip()
            error["message"] = f"batch op {offset + i} ({where}): {error.get('message')}"
            raise BridgeError(error)
        out.append(r.get("result"))
    return out
