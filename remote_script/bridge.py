"""The Control Surface that runs inside Live — the ONLY module that imports Live.

Deliberately thin: it wires the socket server to the dispatcher and marshals
every request onto Live's main thread. All real logic lives in the Live-free,
unit-tested modules (``lom`` / ``dispatch`` / ``server`` / ``framing``).

Cannot be imported outside Live (``Live`` / ``_Framework`` only exist there); it
is loaded lazily by ``create_instance`` in ``__init__.py``.
"""
from __future__ import annotations

import sys
import threading

import Live  # noqa: F401 — provided by Live's embedded interpreter
from _Framework.ControlSurface import ControlSurface

from . import dispatch
from .observers import Registry
from .server import SocketServer

MAIN_THREAD_TIMEOUT = 15.0  # seconds to wait for Live's thread before giving up


class _MainThreadTimeout(Exception):
    pass


class Bridge(ControlSurface):
    def __init__(self, c_instance):
        super().__init__(c_instance)
        self._main_thread_id = threading.current_thread().ident
        self._registry = Registry(log=self.log_message)
        self._context = {
            "roots": {
                "live_set": self.song(),
                "live_app": Live.Application.get_application(),
            },
            "versions": {
                "live": _live_version(),
                "python": "%d.%d.%d" % sys.version_info[:3],
            },
            "capabilities": ["get", "set", "call", "resolve", "children",
                             "observe", "unobserve",
                             "clip_get_notes", "clip_add_notes", "clip_remove_notes"],
            "registry": self._registry,
            "note_spec_factory": Live.Clip.MidiNoteSpecification,
        }
        self._server = SocketServer(
            self._handle_request, log=self.log_message,
            on_disconnect=self._on_client_disconnect,
        )
        self._server.start()
        self.log_message("AI Bridge for Ableton Live: server started on 127.0.0.1:8766")

    def disconnect(self):
        try:
            self._server.stop()
            # we ARE on Live's main thread here — tear every listener down
            n = self._registry.teardown_all()
            if n:
                self.log_message(f"AI Bridge: tore down {n} listener(s) on shutdown")
        finally:
            super().disconnect()

    def _on_client_disconnect(self, client):
        """Socket thread: marshal the registry cleanup onto Live's main thread."""

        def cleanup():
            try:
                self._registry.drop_client(client.id)
            except Exception as exc:  # never raise inside Live's tick
                self.log_message(f"AI Bridge: drop_client({client.id}) failed: {exc}")

        self.schedule_message(0, cleanup)

    # --- request handling ------------------------------------------------------

    def _handle_request(self, request: dict, client) -> dict:
        """Runs on a socket thread. Hop to Live's thread for all LOM access."""
        rid = request.get("id")
        try:
            return self._run_on_main(lambda: dispatch.handle(self._context, request, client))
        except _MainThreadTimeout:
            return _internal(rid, "timed out waiting for Live's main thread")
        except Exception as exc:  # last-resort guard — always answer
            return _internal(rid, f"bridge failure: {exc}")

    def _run_on_main(self, fn):
        """Execute ``fn`` on Live's main thread and return its result.

        If already on the main thread, call directly (avoids self-deadlock).
        Otherwise schedule via ``schedule_message(0, …)`` and block on an Event.
        """
        if threading.current_thread().ident == self._main_thread_id:
            return fn()

        done = threading.Event()
        box = {"value": None, "error": None}
        abandoned = {"v": False}

        def invoke():
            if abandoned["v"]:
                done.set()
                return
            try:
                box["value"] = fn()
            except BaseException as exc:  # never let anything escape Live's tick
                box["error"] = exc
            finally:
                done.set()

        self.schedule_message(0, invoke)
        if not done.wait(MAIN_THREAD_TIMEOUT):
            abandoned["v"] = True
            raise _MainThreadTimeout()
        if box["error"] is not None:
            raise box["error"]
        return box["value"]


def _internal(rid, message):
    return {"id": rid, "ok": False, "error": {"type": "internal", "message": message}}


def _live_version():
    try:
        app = Live.Application.get_application()
        return "%d.%d.%d" % (
            app.get_major_version(),
            app.get_minor_version(),
            app.get_bugfix_version(),
        )
    except Exception:
        return None
