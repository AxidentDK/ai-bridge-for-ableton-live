"""The observer registry — subscriptions with guaranteed teardown.

THE memory-leak fix (DESIGN §5.2): every listener added to a Live object is
recorded here, keyed by subscription id and client, and is *always* removed on:
unobserve, client disconnect, or bridge shutdown. Deleted-object teardown is
tolerated (Live raises when touching a removed object — we swallow that during
removal; the listener is already gone with the object).

Pure logic — no ``Live`` import. Uses the LOM listener convention:
``add_<prop>_listener(fn)`` / ``remove_<prop>_listener(fn)``.

Threading: ``subscribe``/``unsubscribe``/``drop_client``/``teardown_all`` must
run on Live's main thread (the caller marshals). Live fires listeners on its
main thread too; the emit callback must therefore never block (the server's
per-client outbox guarantees that).
"""
from __future__ import annotations

import itertools

from .lom import LomError, resolve, serialize


class _Sub:
    __slots__ = ("sid", "client_id", "obj", "prop", "path", "listener")

    def __init__(self, sid, client_id, obj, prop, path, listener):
        self.sid = sid
        self.client_id = client_id
        self.obj = obj
        self.prop = prop
        self.path = path
        self.listener = listener


class Registry:
    def __init__(self, log=None):
        self._subs: dict[int, _Sub] = {}
        self._ids = itertools.count(1)
        self._log = log or (lambda *a: None)

    # --- introspection ----------------------------------------------------------
    def count(self) -> int:
        return len(self._subs)

    def subs_for(self, client_id) -> list[int]:
        return [s.sid for s in self._subs.values() if s.client_id == client_id]

    # --- lifecycle ---------------------------------------------------------------
    def subscribe(self, roots: dict, path: str, prop: str, client_id, send) -> dict:
        """Attach a listener for ``prop`` on the object at ``path``.

        ``send(msg)`` must be non-blocking (an outbox enqueue). Returns
        ``{"sub": id}``.
        """
        obj = resolve(roots, path)
        adder = getattr(obj, f"add_{prop}_listener", None)
        remover = getattr(obj, f"remove_{prop}_listener", None)
        if not callable(adder) or not callable(remover):
            raise LomError("not_observable", f"{type(obj).__name__}.{prop} has no listener API")

        sid = next(self._ids)

        def listener():
            # fires on Live's main thread; read the fresh value, enqueue, return
            try:
                value = serialize(getattr(obj, prop))
            except Exception:
                value = None  # object may be mid-deletion — still emit the event
            send({"event": True, "sub": sid, "path": path, "prop": prop, "value": value})

        adder(listener)
        self._subs[sid] = _Sub(sid, client_id, obj, prop, path, listener)
        return {"sub": sid}

    def unsubscribe(self, sid: int) -> bool:
        sub = self._subs.pop(sid, None)
        if sub is None:
            raise LomError("bad_request", f"unknown subscription {sid}")
        self._detach(sub)
        return True

    def drop_client(self, client_id) -> int:
        """Remove every subscription belonging to a disconnected client."""
        mine = [sid for sid, s in self._subs.items() if s.client_id == client_id]
        for sid in mine:
            self._detach(self._subs.pop(sid))
        if mine:
            self._log(f"observer registry: dropped {len(mine)} sub(s) for client {client_id}")
        return len(mine)

    def teardown_all(self) -> int:
        """Bridge shutdown: remove every listener we ever added."""
        n = len(self._subs)
        for sub in list(self._subs.values()):
            self._detach(sub)
        self._subs.clear()
        return n

    # --- internal ------------------------------------------------------------------
    def _detach(self, sub: _Sub):
        try:
            remover = getattr(sub.obj, f"remove_{sub.prop}_listener", None)
            if callable(remover):
                remover(sub.listener)
        except Exception as exc:
            # deleted LOM object etc. — the listener died with the object
            self._log(f"observer registry: tolerated detach failure on {sub.path}.{sub.prop}: {exc}")
