# host/

**Runs outside Live.** The client library + MCP server that connect to the remote
script's socket and expose the bridge to agents and scripts.

- `live_client.py` — minimal bridge client seed (`rpc()` over TCP). Grows into the
  full host client in Phase 1.
- _MCP server_ — thin layer exposing the convenience commands as tools (Phase 3).

See `../DESIGN.md` §4 and the build plan §10.
