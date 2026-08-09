from server import make_server


class UndoBridge:
    """Fake bridge with an undo/redo history depth."""

    def __init__(self, undo_depth=3, redo_depth=0):
        self.undo_depth = undo_depth
        self.redo_depth = redo_depth
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "eval":
            if params["expr"] == "song.can_undo":
                return self.undo_depth > 0
            if params["expr"] == "song.can_redo":
                return self.redo_depth > 0
            raise AssertionError("unexpected eval %r" % params)
        if method == "exec":
            if "undo" in params["code"]:
                assert self.undo_depth > 0
                self.undo_depth -= 1
                self.redo_depth += 1
            else:
                assert self.redo_depth > 0
                self.redo_depth -= 1
                self.undo_depth += 1
            return True
        raise AssertionError("unexpected method %r" % method)


def _call(server, arguments):
    response = server.handle({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "live_undo", "arguments": arguments},
    })
    return response["result"]["structuredContent"]


def test_undo_multiple_steps():
    bridge = UndoBridge(undo_depth=3)
    result = _call(make_server(bridge), {"steps": 2})
    assert result == {
        "action": "undo", "requested": 2, "performed": 2,
        "exhausted": False, "can_undo": True, "can_redo": True,
    }
    assert bridge.undo_depth == 1


def test_undo_stops_when_history_exhausted():
    bridge = UndoBridge(undo_depth=1)
    result = _call(make_server(bridge), {"steps": 5})
    assert result["performed"] == 1
    assert result["exhausted"] is True
    assert result["can_undo"] is False
    assert result["can_redo"] is True


def test_redo_moves_forward():
    bridge = UndoBridge(undo_depth=0, redo_depth=2)
    result = _call(make_server(bridge), {"steps": 2, "redo": True})
    assert result["action"] == "redo"
    assert result["performed"] == 2
    assert result["can_redo"] is False
    assert result["can_undo"] is True


def test_undo_default_single_step():
    bridge = UndoBridge(undo_depth=2)
    result = _call(make_server(bridge), {})
    assert result["performed"] == 1
    assert bridge.undo_depth == 1
