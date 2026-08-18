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


def test_a_negative_index_says_what_to_do_instead():
    """``track: -1`` for "the master track" used to fail unreadably.

    ``"-1".isdigit()`` is False, so the token was taken for an ATTRIBUTE name and the
    caller got ``'Vector' object has no attribute '-1'`` — which mentions neither indexing
    nor the alternative. An agent reaching for the master track hit this and lost a step
    to it, so the error now names the fix.
    """
    _, ctx = make_context()
    reply = call(ctx, "get", path="live_set tracks -1", prop="name")
    message = reply["error"]["message"]
    assert "negative indices" in message, message
    assert "master_track" in message and "return_tracks" in message, message
    # A real index still resolves, and a plain bad name still reports normally.
    assert call(ctx, "get", path="live_set tracks 1", prop="name")["result"] == "Pad"
    assert "no 'nope'" in call(ctx, "get", path="live_set nope",
                               prop="name")["error"]["message"]


def test_children_introspection():
    _, ctx = make_context()
    kids = call(ctx, "children", path="live_set")["result"]
    assert "tracks" in kids["properties"]
    assert "tempo" in kids["properties"]
    assert "stop_playing" in kids["functions"]
    assert "start_playing" in kids["functions"]


class StrictParam:
    """Mimics a LOM DeviceParameter: its setter accepts only a float."""
    def __init__(self):
        self._v = 0.0

    @property
    def value(self):
        return self._v

    @value.setter
    def value(self, x):
        if type(x) is not float:
            raise TypeError("did not match C++ signature (needs float)")
        self._v = x


def test_set_coerces_stringified_numbers():
    from remote_script import lom
    root = type("Holder", (), {})()
    root.param = StrictParam()
    roots = {"live_set": root}
    # a string number is coerced to float and accepted (the MCP untyped-value case)
    assert lom.set_(roots, "live_set param", "value", "0.82") is True
    assert root.param.value == 0.82
    # a bare int is coerced to float too
    assert lom.set_(roots, "live_set param", "value", 1) is True
    assert root.param.value == 1.0
    # genuinely non-numeric still fails cleanly
    try:
        lom.set_(roots, "live_set param", "value", "loud")
        assert False, "non-numeric string should not coerce"
    except lom.LomError as e:
        assert e.type == "not_writable"


def test_batch_runs_ops_in_order_with_isolation():
    song, ctx = make_context()
    resp = call(ctx, "batch", ops=[
        {"method": "get", "params": {"path": "live_set", "prop": "tempo"}},
        {"method": "set", "params": {"path": "live_set", "prop": "tempo", "value": 99.0}},
        {"method": "get", "params": {"path": "live_set tracks 9", "prop": "name"}},  # bad
        {"method": "call", "params": {"path": "live_set", "func": "start_playing"}},
        {"method": "get", "params": {"path": "live_set", "prop": "tempo"}},
    ])
    assert resp["ok"] is True
    r = resp["result"]
    assert r[0] == {"ok": True, "result": 120.0}
    assert r[1] == {"ok": True, "result": True} and song.tempo == 99.0
    assert r[2]["ok"] is False and r[2]["error"]["type"] == "no_such_path"
    assert r[3]["ok"] is True and song.is_playing is True  # bad op didn't abort the rest
    assert r[4] == {"ok": True, "result": 99.0}


def test_batch_refuses_nesting_unknown_and_oversize():
    _, ctx = make_context()
    r = call(ctx, "batch", ops=[
        {"method": "batch", "params": {"ops": []}},   # no nesting
        {"method": "frobnicate", "params": {}},       # unknown
        "not-a-dict",                                  # malformed op
    ])["result"]
    assert all(x["ok"] is False and x["error"]["type"] == "bad_request" for x in r)
    ping = {"method": "ping", "params": {}}
    over = call(ctx, "batch", ops=[ping] * (dispatch.MAX_BATCH_OPS + 1))
    assert over["ok"] is False and over["error"]["type"] == "bad_request"
    ok = call(ctx, "batch", ops=[ping] * dispatch.MAX_BATCH_OPS)
    assert ok["ok"] is True and len(ok["result"]) == dispatch.MAX_BATCH_OPS


def test_client_get_many_set_many_over_loopback():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "host"))
    from client import Bridge, BridgeError

    _, ctx = make_context()
    server = SocketServer(lambda req, client: dispatch.handle(ctx, req, client), port=0)
    server.start()
    try:
        with Bridge(port=server.port, timeout=2.0) as b:
            values = b.get_many([("live_set", "tempo"),
                                 ("live_set tracks 0", "name"),
                                 ("live_set tracks 1", "name")])
            assert values == [120.0, "Deep", "Pad"]
            assert b.set_many([("live_set", "tempo", 87.5),
                               ("live_set tracks 0", "mute", True)]) is True
            assert b.get_many([("live_set", "tempo"),
                               ("live_set tracks 0", "mute")]) == [87.5, True]
            try:  # strict: a failing op raises, naming the op
                b.get_many([("live_set", "tempo"), ("live_set", "nope")])
                assert False, "get_many must raise on a failed read"
            except BridgeError as e:
                assert e.type == "no_such_property" and "batch op 1" in e.message
    finally:
        server.stop()


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
