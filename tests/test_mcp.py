"""End-to-end MCP test: real mcp_server.py subprocess <-> fake Live bridge.

Spawns a fake bridge (SocketServer + dispatch + fake song) on a random port,
then runs host/mcp_server.py as a subprocess with AI_BRIDGE_PORT pointed at it,
speaking actual MCP JSON-RPC over its stdio. No Live, no pytest.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from remote_script import dispatch  # noqa: E402
from remote_script.observers import Registry  # noqa: E402
from remote_script.server import SocketServer  # noqa: E402
from tests.test_notes import FakeSong, FakeSpec  # noqa: E402


class MCP:
    """Minimal MCP stdio client for the test."""

    def __init__(self, port: int):
        env = dict(os.environ, AI_BRIDGE_PORT=str(port), PYTHONUTF8="1")
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "host", "mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, encoding="utf-8",
        )
        self._id = 0

    def rpc(self, method, params=None):
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, f"no response to {method} (stderr: {self.proc.stderr.read()})"
        return json.loads(line)

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def tool(self, name, arguments=None):
        resp = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        result = resp["result"]
        text = result["content"][0]["text"]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = text
        return result.get("isError", False), payload

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)


def test_mcp_end_to_end():
    song = FakeSong()
    song.tempo = 120.0
    song.is_playing = False
    ctx = {
        "roots": {"live_set": song},
        "versions": {"live": "12.4.3", "python": "3.11.0"},
        "capabilities": ["get", "set", "call", "clip_get_notes", "clip_add_notes"],
        "registry": Registry(),
        "note_spec_factory": FakeSpec,
    }
    server = SocketServer(lambda req, client: dispatch.handle(ctx, req, client), port=0)
    server.start()
    mcp = MCP(server.port)
    try:
        # handshake
        init = mcp.rpc("initialize", {"protocolVersion": "2024-11-05",
                                      "capabilities": {}, "clientInfo": {"name": "test"}})
        assert init["result"]["serverInfo"]["name"] == "ai-bridge-for-ableton-live"
        mcp.notify("notifications/initialized")

        # tools list
        tools = mcp.rpc("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        assert {"live_ping", "live_summary", "live_get", "live_set",
                "live_clip_add_notes"} <= names

        # tool calls against the fake bridge
        err, pong = mcp.tool("live_ping")
        assert not err and pong == "pong"

        err, tempo = mcp.tool("live_get", {"path": "live_set", "prop": "tempo"})
        assert not err and tempo == 120.0

        err, ok = mcp.tool("live_set", {"path": "live_set", "prop": "tempo", "value": 99.0})
        assert not err and song.tempo == 99.0

        # batch: many ops, one round-trip, per-op isolation
        err, r = mcp.tool("live_batch", {"ops": [
            {"method": "get", "params": {"path": "live_set", "prop": "tempo"}},
            {"method": "set", "params": {"path": "live_set", "prop": "tempo",
                                         "value": 101.0}},
            {"method": "get", "params": {"path": "live_set", "prop": "nope"}},
            {"method": "get", "params": {"path": "live_set", "prop": "tempo"}},
        ]})
        assert not err and song.tempo == 101.0
        assert r[0] == {"ok": True, "result": 99.0}
        assert r[2]["ok"] is False and r[2]["error"]["type"] == "no_such_property"
        assert r[3] == {"ok": True, "result": 101.0}

        clip = "live_set tracks 0 clip_slots 0 clip"
        err, n = mcp.tool("live_clip_add_notes", {"path": clip, "notes": [
            {"pitch": 64, "start_time": 0.0, "duration": 1.0, "velocity": 90}]})
        assert not err and n == 1
        err, notes = mcp.tool("live_clip_notes", {"path": clip})
        assert not err and notes[0]["pitch"] == 64

        # structured error surfaces as isError, not a crash
        err, msg = mcp.tool("live_get", {"path": "live_set tracks 9", "prop": "name"})
        assert err and "no_such_path" in str(msg)

        # unknown method -> JSON-RPC error, loop survives
        bad = mcp.rpc("definitely/not/a/method")
        assert "error" in bad
        err, pong = mcp.tool("live_ping")
        assert not err and pong == "pong"
    finally:
        mcp.close()
        server.stop()


def test_every_tool_schema_is_wellformed():
    """Guard the whole tool table, not just the few exercised above.

    A single malformed entry raises at IMPORT time and takes the entire server
    down — every tool disappears, not just the bad one (field-hit 2026-08-14: a
    tool whose arguments are all optional was declared without a `required`
    list, and the server had been failing to start ever since). Importing the
    module here is itself the check; the assertions then pin the shape.
    """
    sys.path.insert(0, os.path.join(ROOT, "host"))
    import mcp_server  # noqa: E402  (import IS the regression test)

    names = set()
    for tool in mcp_server.TOOLS:
        name = tool.get("name", "")
        assert name.startswith("live_"), f"odd tool name {name!r}"
        assert name not in names, f"duplicate tool {name!r}"
        names.add(name)
        assert tool.get("description"), f"{name} has no description"
        schema = tool.get("inputSchema") or {}
        assert schema.get("type") == "object", f"{name} schema is not an object"
        assert isinstance(schema.get("properties"), dict), f"{name} has no properties"
        assert isinstance(schema.get("required"), list), f"{name} has no required list"
        for key in schema["required"]:
            assert key in schema["properties"], \
                f"{name} requires {key!r} which is not in properties"
    assert len(names) > 20, "tool table looks truncated"
    return len(names)


if __name__ == "__main__":
    import traceback

    failures = 0
    try:
        count = test_every_tool_schema_is_wellformed()
        print(f"  PASS  test_every_tool_schema_is_wellformed ({count} tools)")
    except Exception:
        print("  FAIL  test_every_tool_schema_is_wellformed")
        traceback.print_exc()
        failures += 1
    try:
        test_mcp_end_to_end()
        print("  PASS  test_mcp_end_to_end")
    except Exception:
        print("  FAIL  test_mcp_end_to_end")
        traceback.print_exc()
        failures += 1
    print(f"\n{2 - failures}/2 passed")
    if failures:
        sys.exit(1)
