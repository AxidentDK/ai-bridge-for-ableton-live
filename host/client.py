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
        self.type = error.get("type")
        self.detail = error.get("detail")


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

    # --- observers ----------------------------------------------------------------
    def observe(self, path: str, prop: str) -> int:
        """Subscribe to property changes. Returns the subscription id."""
        return self.request("observe", path=path, prop=prop)["sub"]

    def unobserve(self, sub: int):
        return self.request("unobserve", sub=sub)
