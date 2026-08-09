# ableton-live-bridge

> Working name — subject to change.

A clean, robust bridge that exposes the **complete Ableton Live Object Model
(LOM)** to external programs — AI agents, scripts, tooling — over a simple local
RPC protocol. Built for stability first (no crashes, no memory leaks inside
Live), then completeness, clarity, and ergonomics.

**Status:** blueprint / Phase 0 scaffold. Not yet functional — see
[DESIGN.md](DESIGN.md) for the architecture and build plan, and
[PORTING.md](PORTING.md) for what's been brought in so far.

## License & governance

- **Apache-2.0** — free to use, modify, and redistribute (see `LICENSE`), with an
  explicit patent grant.
- **Single maintainer.** Issues are welcome for bug reports; pull requests are
  considered at the maintainer's sole discretion. See `CONTRIBUTING.md`.

## How it works (one paragraph)

A Control-Surface **remote script inside Live** holds the LOM and executes
commands on Live's own thread, managing observers safely. It speaks
length-framed JSON over a local TCP socket to a **host** (a Python client + an
MCP server) that exposes the bridge as tools. Full LOM reach comes from a small
**generic proxy** (`get`/`set`/`call`/`observe`/`resolve`), with an ergonomic
convenience layer on top. Full detail in [DESIGN.md](DESIGN.md).

## Credits

Reuses MIT-licensed parts of
[ableton-live-mcp](https://github.com/bschoepke/ableton-live-mcp) — see `NOTICE`.
