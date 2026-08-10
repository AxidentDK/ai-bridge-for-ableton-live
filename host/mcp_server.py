"""MCP server for AI Bridge — exposes the bridge as MCP tools over stdio.

Zero dependencies: a minimal, hand-rolled JSON-RPC 2.0 / MCP stdio loop
(newline-delimited UTF-8 JSON). Register in an MCP client (e.g. Claude Code)
with:  python host/mcp_server.py

The bridge connection (127.0.0.1:8766) is opened lazily on first tool call and
re-opened automatically if Live restarts.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import Bridge, BridgeError  # noqa: E402

SERVER_NAME = "ai-bridge-for-ableton-live"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"


# --- tool definitions -----------------------------------------------------------

def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}

_PATH = {"type": "string", "description":
         "LOM path, space-separated (e.g. 'live_set tracks 0 clip_slots 0 clip')"}

TOOLS = [
    {"name": "live_ping",
     "description": "Liveness check of the AI Bridge inside Ableton Live.",
     "inputSchema": _schema({}, [])},
    {"name": "live_summary",
     "description": "Overview of the open Live set: version, tempo, playing state, tracks.",
     "inputSchema": _schema({}, [])},
    {"name": "live_get",
     "description": "Read any property of any Live Object Model object.",
     "inputSchema": _schema({"path": _PATH, "prop": {"type": "string"}}, ["path", "prop"])},
    {"name": "live_set",
     "description": "Write any writable property of any Live Object Model object.",
     "inputSchema": _schema({"path": _PATH, "prop": {"type": "string"}, "value": {}},
                            ["path", "prop", "value"])},
    {"name": "live_call",
     "description": "Call any function on any Live Object Model object (args are JSON scalars).",
     "inputSchema": _schema({"path": _PATH, "func": {"type": "string"},
                             "args": {"type": "array"}}, ["path", "func"])},
    {"name": "live_children",
     "description": "Introspect a Live object: its type and available properties/functions.",
     "inputSchema": _schema({"path": _PATH}, ["path"])},
    {"name": "live_resolve",
     "description": "Check whether a LOM path exists; returns its $ref or null.",
     "inputSchema": _schema({"path": _PATH}, ["path"])},
    {"name": "live_clip_notes",
     "description": "Read the MIDI notes of a clip (optionally windowed by time/pitch).",
     "inputSchema": _schema({"path": _PATH,
                             "from_time": {"type": "number"}, "time_span": {"type": "number"},
                             "from_pitch": {"type": "integer"}, "pitch_span": {"type": "integer"}},
                            ["path"])},
    {"name": "live_clip_add_notes",
     "description": ("Add MIDI notes to a clip. Each note: {pitch, start_time, duration, "
                     "velocity?, mute?}. Times in beats."),
     "inputSchema": _schema({"path": _PATH, "notes": {"type": "array", "items": {
         "type": "object", "properties": {
             "pitch": {"type": "integer"}, "start_time": {"type": "number"},
             "duration": {"type": "number"}, "velocity": {"type": "number"},
             "mute": {"type": "boolean"}},
         "required": ["pitch", "start_time", "duration"]}}}, ["path", "notes"])},
    {"name": "live_clip_remove_notes",
     "description": "Remove MIDI notes from a clip (whole clip by default, or a time/pitch window).",
     "inputSchema": _schema({"path": _PATH,
                             "from_time": {"type": "number"}, "time_span": {"type": "number"},
                             "from_pitch": {"type": "integer"}, "pitch_span": {"type": "integer"}},
                            ["path"])},
    {"name": "live_save_set",
     "description": ("Save the current Live set (Ctrl+S to the Live window, verified via the "
                     ".als file's mtime). Refuses on a never-saved set. Briefly moves focus "
                     "to Live."),
     "inputSchema": _schema({"timeout": {"type": "number"}}, [])},
    {"name": "live_export",
     "description": ("Render the arrangement to a WAV by driving Live's export dialog "
                     "(inherits last-used export settings; file type must be WAV). Give "
                     "start_beats+length_beats to set the render range; the rendered duration "
                     "is verified against it. Briefly takes over keyboard focus."),
     "inputSchema": _schema({"output_path": {"type": "string"},
                             "start_beats": {"type": "number"},
                             "length_beats": {"type": "number"},
                             "timeout": {"type": "number"}}, ["output_path"])},
    {"name": "live_analyze_wav",
     "description": ("Measure a WAV file: integrated loudness (LUFS, ITU-R BS.1770-4), sample "
                     "peak dBFS, and band energies. Requires numpy."),
     "inputSchema": _schema({"path": {"type": "string", "description": "path to a .wav file"}},
                            ["path"])},
    {"name": "live_cleanup_tracks",
     "description": ("Delete unused tracks from the set — a track with NO clips (session or "
                     "arrangement) AND NO devices. Conservative: never removes a track with a "
                     "loaded instrument or any clip. Set dry_run=true to preview only."),
     "inputSchema": _schema({"dry_run": {"type": "boolean"}}, [])},
]


# --- bridge connection (lazy, self-healing) ------------------------------------------

_bridge: Bridge | None = None
_PORT = int(os.environ.get("AI_BRIDGE_PORT", "8766"))  # overridable for tests


def bridge() -> Bridge:
    global _bridge
    if _bridge is None:
        _bridge = Bridge(port=_PORT)
    try:
        _bridge.ping()
    except Exception:
        try:
            _bridge.close()
        except Exception:
            pass
        _bridge = Bridge(port=_PORT)  # Live restarted — reconnect
    return _bridge


def run_tool(name: str, args: dict):
    b = bridge()
    if name == "live_ping":
        return b.ping()
    if name == "live_summary":
        tracks = b.get("live_set", "tracks")
        return {
            "live_version": b.hello().get("live_version"),
            "tempo": b.get("live_set", "tempo"),
            "is_playing": b.get("live_set", "is_playing"),
            "tracks": [{"index": i, "name": t.get("name")} for i, t in enumerate(tracks)],
        }
    if name == "live_get":
        return b.get(args["path"], args["prop"])
    if name == "live_set":
        return b.set(args["path"], args["prop"], args["value"])
    if name == "live_call":
        return b.call(args["path"], args["func"], *(args.get("args") or []))
    if name == "live_children":
        return b.children(args["path"])
    if name == "live_resolve":
        return b.resolve(args["path"])
    if name == "live_clip_notes":
        return b.request("clip_get_notes", **args)
    if name == "live_clip_add_notes":
        return b.request("clip_add_notes", **args)
    if name == "live_clip_remove_notes":
        return b.request("clip_remove_notes", **args)
    if name == "live_save_set":
        import render
        return render.save_set(b, **args)
    if name == "live_export":
        import render
        return render.export_set(b, args["output_path"],
                                 args.get("start_beats"), args.get("length_beats"),
                                 timeout=float(args.get("timeout", 240.0)))
    if name == "live_analyze_wav":
        try:
            from audio_analysis import analyze_wav
        except ImportError as exc:
            raise ValueError(f"live_analyze_wav requires numpy ({exc})")
        return analyze_wav(args["path"])
    if name == "live_cleanup_tracks":
        from api import Live
        return Live(b).cleanup_unused_tracks(dry_run=bool(args.get("dry_run", False)))
    raise ValueError(f"unknown tool {name!r}")


# --- MCP stdio loop -------------------------------------------------------------------

def _reply(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(msg: dict):
    rid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        _reply(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method in ("notifications/initialized", "notifications/cancelled"):
        pass  # notifications get no response
    elif method == "ping":
        _reply(rid, {})
    elif method == "tools/list":
        _reply(rid, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = run_tool(name, args)
            text = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
            _reply(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except (BridgeError, ConnectionError, OSError, ValueError) as exc:
            _reply(rid, {"content": [{"type": "text", "text": f"error: {exc}"}],
                         "isError": True})
    elif rid is not None:
        _reply(rid, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    # Windows: force UTF-8 + LF on the stdio pipes (cp1252 mangles music-note
    # names like 'Vöcal åäö'; \r\n framing confuses strict clients)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerate garbage lines rather than dying
        try:
            handle(msg)
        except Exception as exc:  # the loop must survive anything
            if msg.get("id") is not None:
                _reply(msg["id"], error={"code": -32000, "message": str(exc)})


if __name__ == "__main__":
    main()
