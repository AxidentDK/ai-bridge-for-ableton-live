"""Core logic tests — lom + dispatch + a real loopback server round-trip.

No Live, no pytest. Run:  python tests/test_core.py
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_script import dispatch, framing  # noqa: E402
from remote_script.server import SocketServer  # noqa: E402


# --- fakes that mimic the shape of the LOM ------------------------------------

class FakeTrack:
    def __init__(self, name, ptr):
        self.name = name
        self.mute = False
        self._live_ptr = ptr


class FakeSong:
    def __init__(self):
        self.tempo = 120.0
        self.is_playing = False
        self.tracks = [FakeTrack("Deep", 101), FakeTrack("Pad", 102)]
        self.master_track = FakeTrack("Master", 100)

    def stop_playing(self):
        self.is_playing = False

    def start_playing(self):
        self.is_playing = True


class FakeApp:
    def get_major_version(self):
        return 12

    def get_minor_version(self):
        return 1

    def get_bugfix_version(self):
        return 0


def make_context():
    song = FakeSong()
    return song, {
        "roots": {"live_set": song, "live_app": FakeApp()},
        "versions": {"live": "12.1.0", "python": "3.11.0"},
        "capabilities": ["get", "set", "call", "resolve", "children"],
    }


def call(ctx, method, **params):
    return dispatch.handle(ctx, {"id": 1, "method": method, "params": params})


# --- dispatch / lom tests ------------------------------------------------------

def test_ping_and_hello():
    _, ctx = make_context()
    assert call(ctx, "ping")["result"] == "pong"
    hello = call(ctx, "hello")["result"]
    assert hello["capabilities"] and hello["bridge_version"] == dispatch.BRIDGE_VERSION
    assert hello["live_version"] == "12.1.0"


def test_get_and_set_scalar():
    song, ctx = make_context()
    assert call(ctx, "get", path="live_set", prop="tempo")["result"] == 120.0
    assert call(ctx, "set", path="live_set", prop="tempo", value=130.0)["result"] is True
    assert song.tempo == 130.0
    assert call(ctx, "get", path="live_set", prop="tempo")["result"] == 130.0


def test_get_list_and_nested_ref():
    _, ctx = make_context()
    tracks = call(ctx, "get", path="live_set", prop="tracks")["result"]
    assert [t["name"] for t in tracks] == ["Deep", "Pad"]
    assert tracks[0]["type"] == "FakeTrack" and tracks[0]["id"] == 101
    assert call(ctx, "get", path="live_set tracks 0", prop="name")["result"] == "Deep"
    master = call(ctx, "get", path="live_set", prop="master_track")["result"]
    assert master["name"] == "Master"


def test_call_mutates():
    song, ctx = make_context()
    song.is_playing = True
    assert call(ctx, "call", path="live_set", func="stop_playing")["result"] is None
    assert song.is_playing is False
    call(ctx, "call", path="live_set", func="start_playing")
    assert song.is_playing is True


def test_resolve_ref():
    _, ctx = make_context()
    ref = call(ctx, "resolve", path="live_set tracks 1")["result"]
    assert ref["$ref"] == "live_set tracks 1" and ref["name"] == "Pad"
    assert call(ctx, "resolve", path="live_set tracks 9")["result"] is None


def test_children_introspection():
    _, ctx = make_context()
    kids = call(ctx, "children", path="live_set")["result"]
    assert "tracks" in kids["properties"]
    assert "tempo" in kids["properties"]
    assert "stop_playing" in kids["functions"]
    assert "start_playing" in kids["functions"]


def test_errors_are_structured():
    _, ctx = make_context()
    bad_path = call(ctx, "get", path="live_set tracks 9", prop="name")
    assert bad_path["ok"] is False and bad_path["error"]["type"] == "no_such_path"
    bad_prop = call(ctx, "get", path="live_set", prop="nope")
    assert bad_prop["ok"] is False and bad_prop["error"]["type"] == "no_such_property"
    bad_method = call(ctx, "frobnicate")
    assert bad_method["ok"] is False and bad_method["error"]["type"] == "bad_request"


# --- real loopback server round-trip ------------------------------------------

def test_server_roundtrip():
    _, ctx = make_context()
    server = SocketServer(lambda req, client: dispatch.handle(ctx, req, client), port=0)
    server.start()
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
        client.settimeout(2.0)
        with client:
            framing.write_frame(client, {"id": 7, "method": "get",
                                         "params": {"path": "live_set", "prop": "tempo"}})
            resp = framing.read_frame(client)
            assert resp == {"id": 7, "ok": True, "result": 120.0}
            # a second call on the same connection
            framing.write_frame(client, {"id": 8, "method": "ping", "params": {}})
            assert framing.read_frame(client)["result"] == "pong"
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
