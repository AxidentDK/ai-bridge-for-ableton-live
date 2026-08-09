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
        resp = framing.read_frame(self._sock)
        if resp is None:
            raise ConnectionError("bridge closed the connection")
        if resp.get("id") != rid:
            raise ConnectionError(f"response id {resp.get('id')} != request id {rid}")
        if not resp.get("ok"):
            raise BridgeError(resp.get("error") or {})
        return resp.get("result")

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
