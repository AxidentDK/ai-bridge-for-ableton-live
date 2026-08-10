# host/

**Runs outside Live.** The client library + MCP server that connect to the remote
script's socket and expose the bridge to agents and scripts.

- `client.py` — the bridge client (`Bridge`): request/response, events, and
  typed helpers (`get`/`set`/`call`/`resolve`/`children`/`observe`/…).
- `api.py` — ergonomic layer (`Live`): transport, tracks, clips, notes, devices,
  mixer, `save()`/`export()`, `summary()`.
- `render.py` + `winui.py` — beyond-LOM tools: save-set and arrangement export
  by driving Live's UI (focus grab + keystrokes), with verification.
- `audio_analysis.py` — BS.1770-4 LUFS / peak / band analysis (needs numpy).
- `mcp_server.py` — zero-dependency MCP stdio server exposing 13 `live_*` tools.

See `../DESIGN.md` and the wire spec `../docs/PROTOCOL.md`.
