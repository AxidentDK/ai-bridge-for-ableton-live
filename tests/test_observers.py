"""Observer registry tests — the no-leaked-listeners guarantee.

No Live, no pytest. Run:  python tests/test_observers.py
"""
import importlib.util
import os
import socket
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from remote_script import dispatch  # noqa: E402
from remote_script.lom import LomError  # noqa: E402
from remote_script.observers import Registry  # noqa: E402
from remote_script.server import SocketServer  # noqa: E402


# --- a fake with the LOM listener convention ------------------------------------

class FakeSong:
    def __init__(self):
        self.tempo = 120.0
        self._tempo_listeners = []

    def add_tempo_listener(self, fn):
        self._tempo_listeners.append(fn)

    def remove_tempo_listener(self, fn):
        self._tempo_listeners.remove(fn)

    def set_tempo(self, value):  # simulate Live: change + notify
        self.tempo = value
        for fn in list(self._tempo_listeners):
            fn()

    @property
    def listener_count(self):
        return len(self._tempo_listeners)


class BrokenRemove(FakeSong):
    """Simulates a deleted LOM object: removal raises."""

    def remove_tempo_listener(self, fn):
        raise RuntimeError("object deleted")


def make(song=None):
    song = song or FakeSong()
    sent = []
    reg = Registry()
    roots = {"live_set": song}
    return song, reg, roots, sent


# --- registry unit tests ----------------------------------------------------------

def test_subscribe_fires_events():
    song, reg, roots, sent = make()
    sub = reg.subscribe(roots, "live_set", "tempo", client_id=1, send=sent.append)
    assert song.listener_count == 1 and reg.count() == 1
    song.set_tempo(100.0)
    assert sent == [{"event": True, "sub": sub["sub"], "path": "live_set",
                     "prop": "tempo", "value": 100.0}]
    song.set_tempo(90.0)
    assert len(sent) == 2 and sent[1]["value"] == 90.0


def test_unsubscribe_removes_listener():
    song, reg, roots, sent = make()
    sub = reg.subscribe(roots, "live_set", "tempo", 1, sent.append)
    assert reg.unsubscribe(sub["sub"]) is True
    assert song.listener_count == 0 and reg.count() == 0
    song.set_tempo(80.0)
    assert sent == []  # no events after unobserve
    try:
        reg.unsubscribe(sub["sub"])
        assert False, "double-unsubscribe should raise"
    except LomError as e:
        assert e.type == "bad_request"


def test_drop_client_removes_only_theirs():
    song, reg, roots, _ = make()
    a1 = reg.subscribe(roots, "live_set", "tempo", client_id=1, send=lambda m: None)
    reg.subscribe(roots, "live_set", "tempo", client_id=2, send=lambda m: None)
    reg.subscribe(roots, "live_set", "tempo", client_id=1, send=lambda m: None)
    assert song.listener_count == 3
    assert reg.drop_client(1) == 2
    assert song.listener_count == 1 and reg.count() == 1
    assert reg.subs_for(1) == [] and len(reg.subs_for(2)) == 1
    assert a1["sub"] not in [s for s in reg.subs_for(2)]


def test_teardown_all():
    song, reg, roots, _ = make()
    for cid in (1, 2, 3):
        reg.subscribe(roots, "live_set", "tempo", cid, lambda m: None)
    assert song.listener_count == 3
    assert reg.teardown_all() == 3
    assert song.listener_count == 0 and reg.count() == 0


def test_not_observable():
    _, reg, roots, _ = make()
    try:
        reg.subscribe(roots, "live_set", "listener_count", 1, lambda m: None)
        assert False, "should raise not_observable"
    except LomError as e:
        assert e.type == "not_observable"


def test_deleted_object_teardown_tolerated():
    song = BrokenRemove()
    _, reg, roots, _ = make(song)
    sub = reg.subscribe(roots, "live_set", "tempo", 1, lambda m: None)
    assert reg.unsubscribe(sub["sub"]) is True  # remover raised, still cleaned up
    assert reg.count() == 0


# --- end-to-end over a real socket: observe -> event -> unobserve ------------------

def test_events_over_socket():
    song = FakeSong()
    ctx = {
        "roots": {"live_set": song},
        "versions": {"live": "12.1.0", "python": "3.11.0"},
        "capabilities": ["observe"],
        "registry": Registry(),
    }
    disconnected = []
    server = SocketServer(
        lambda req, client: dispatch.handle(ctx, req, client),
        port=0,
        on_disconnect=lambda client: disconnected.append(
            ctx["registry"].drop_client(client.id)),
    )
    server.start()
    try:
        # load the real host client from host/client.py
        spec = importlib.util.spec_from_file_location(
            "abl_client", os.path.join(ROOT, "host", "client.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        live = mod.Bridge(port=server.port)
        sub = live.observe("live_set", "tempo")
        assert isinstance(sub, int)
        assert song.listener_count == 1

        # fire a change from "Live's" side; event must reach the client
        threading.Timer(0.05, song.set_tempo, args=(99.0,)).start()
        event = live.wait_event(timeout=3.0)
        assert event and event["sub"] == sub and event["value"] == 99.0

        live.unobserve(sub)
        assert song.listener_count == 0

        # disconnect triggers drop_client for whatever remains
        live.observe("live_set", "tempo")
        assert song.listener_count == 1
        live.close()
        for _ in range(100):
            if disconnected:
                break
            threading.Event().wait(0.02)
        assert disconnected and song.listener_count == 0  # NO LEAKED LISTENERS
    finally:
        server.stop()


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
