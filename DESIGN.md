# Ableton Live Bridge — Design Blueprint

> Name: **AI Bridge for Ableton Live** (repo slug `ai-bridge-for-ableton-live`;
> settled 2026-08-10 — availability verified on GitHub/PyPI/npm; the
> "X for Ableton Live" form follows Ableton's tolerated compatibility naming).
> The control-surface entry inside Live displays as "Live Bridge".
> Status: **blueprint / pre-code.** This is a living document; each build phase
> may feed changes back into it. License: **Apache-2.0**. Maintainer: **Kim
> (AxidentDK), sole maintainer** — free to use, contributions at discretion
> (SQLite governance model).

---

## 1. Purpose

A clean, robust bridge that exposes the **complete Ableton Live Object Model
(LOM)** to external programs — AI agents, scripts, tooling — over a simple local
RPC protocol. Where the LOM stops, optional higher rungs (UI automation, Max for
Live) extend reach, kept isolated so their fragility never touches the core.

**Design values, in priority order:** stability (never crash or leak inside
Live) → completeness (all of the released LOM) → clarity → ergonomics.

## 2. Goals / Non-goals

**Goals**
- 100% reach of the officially released LOM (read, write, call, observe).
- Rock-solid stability: no crashes, no listener/memory leaks, clean disconnects.
- One-command install; graceful behaviour across Live versions.
- A test suite that runs without Live (a fake-Live simulator).

**Non-goals (v1)**
- Other DAWs. A GUI. Accepting outside code contributions.
- Reaching *inside* hosted plugins (Opus/Kontakt patch browsers) — that's the
  fragile UI-automation rung, optional and later.

## 3. License & governance

- **Apache-2.0** — free to use/modify/redistribute, plus an explicit patent
  grant. `NOTICE` file credits any reused MIT portions (see Reuse Manifest).
- **Solo maintainer.** Issues open for bug reports; PRs are *suggestions* merged
  at the maintainer's sole discretion (or not at all). Stated plainly in
  `CONTRIBUTING.md`. This is the SQLite posture: maximally free to use, closed
  to the canonical code.

## 4. Architecture — two halves and a wire

```
  ┌─────────────────────────┐        TCP 127.0.0.1        ┌──────────────────────┐
  │      Ableton Live        │      length-framed JSON      │   Host (outside Live) │
  │  ┌───────────────────┐   │  <══════════════════════>   │  ┌────────────────┐   │
  │  │  Remote Script    │   │                              │  │ Python client   │   │
  │  │  (the HEART)      │   │                              │  │  + MCP server   │   │
  │  │  • LOM handle     │   │                              │  └────────────────┘   │
  │  │  • main-thread    │   │                              │  exposes tools to      │
  │  │    marshaling     │   │                              │  agents / scripts      │
  │  │  • observer reg.  │   │                              └──────────────────────┘
  │  │  • serialization  │   │
  │  └───────────────────┘   │
  └─────────────────────────┘
```

- **Remote Script (inside Live) — the heart.** Runs in Live's embedded Python as
  a Control Surface. Holds the LOM root handle, receives RPC, executes it *on
  Live's main thread*, serializes results, manages observers. This is the part we
  **clean-room to best-option design.**
- **Host (outside Live).** A small Python client library + an MCP server that
  exposes the bridge as tools. Talks to the remote script over the socket.
- **Wire.** Length-prefixed JSON messages over TCP bound to **127.0.0.1 only**
  (local machine). No network exposure by default.

## 5. Core design decisions (the "best option" choices)

1. **Threading / main-thread marshaling.** Live's API is single-threaded and may
   only be touched on Live's own thread. The socket server runs on a background
   thread and *schedules every LOM operation onto Live's task loop*, blocking for
   the result. No LOM call ever happens off-thread. → kills race crashes.
2. **Observer registry = the memory-leak fix.** Every `add_*_listener` is
   recorded in a central registry keyed by (object, event, client). Guaranteed
   teardown on: client disconnect, object deletion, server shutdown. Deleted-
   object access is caught defensively. → this is the single most important
   stability decision; leaked listeners are what bloat and crash Live.
3. **Lazy, path-based serialization.** Live objects serialize to `{path, type,
   key props}` — never a deep graph walk (the LOM is huge and cyclic). Callers
   fetch deeper by following paths on demand.
4. **Length-prefixed framing.** Each message is prefixed with its byte length —
   no newline-delimited truncation hacks, so large payloads (e.g. 1000-note
   clips) stream whole.
5. **Error model.** Every handler wrapped: a bad request returns a structured
   error object; nothing is allowed to take Live down.
6. **Security posture.** Local-bind only. `eval`/`exec` power kept, but gated
   behind the local socket; documented as a local-trust tool.
7. **Version / capability handshake.** On connect, exchange bridge version +
   detected Live version + a capability list, so clients degrade gracefully when
   Ableton changes the LOM.
8. **Structured logging** (optional local file; SQLite call-log only if we ever
   want queryable debugging).

## 6. LOM coverage strategy — proxy, don't enumerate

Full coverage does **not** mean a hand-written method per LOM node. A **generic
proxy** of five primitives reaches 100% of the LOM:

- `get(path, prop)` · `set(path, prop, value)` · `call(path, func, args)`
- `observe(path, event)` / `unobserve(...)` · `resolve(path)` (+ list children)

On top of that generic core sits an **ergonomic convenience layer** for common
workflows (thin wrappers, not new capability):

- Transport (play/stop/tempo/loop/record/metronome)
- Clips & notes (add/read/update/duplicate, envelopes, warp)
- Devices & parameters (incl. rack chains), mixer (vol/pan/sends)
- Browser (search/load), scenes, cue points, grooves

## 7. Reuse manifest (MIT source → keep NOTICE)

| Reuse as-is (proven, MIT) | Clean-room rebuild (ownership + quality) |
|---|---|
| Fake-Live **test harness** (~8.7k lines) | The **remote-script core** (§5) |
| **M4L devices** (audio tap, m4l host) | The **host server / client** layer |
| `ableton_paths`, `install_remote_script` | RPC framing + serialization |
| `visual_capture` / `ocr`, `audio_analysis` | Observer registry |
| our `export_set` / `save_set` (focus fix) | |

Reused files keep their MIT header + are credited in `NOTICE`.

## 8. Beyond-LOM capabilities — isolated optional rungs

Each is a **separate module** so its fragility can't infect the core:
- **Export / render** → UI automation (keystrokes + focus grab). *(port ours)*
- **Save set** → UI automation. *(port ours)*
- **Audio capture / metering** → Max for Live device. *(reuse tap)*
- **`.als` file surgery** (offline, gzipped XML) → e.g. preset injection. *(future)*
- **Plugin-internal control** (e.g. pick a patch inside Opus) → pixel-level UI
  automation. *(experimental, far future)*

## 9. Testing

- Reuse the **fake-Live simulator** → the suite runs with no Live open (CI-able).
- Unit tests for the risky cores: serialization round-trips, observer
  add/teardown (assert zero leaked listeners), framing.
- Integration smoke against a real Live instance.
- For automation/audio features: **verify by measuring audio**, never by reading
  back a parameter (hard-won lesson — reads lie during playback).

## 10. Build plan (phased, with built-in flexibility)

Each phase ends with a working, testable increment and a **checkpoint** where we
re-read this doc and adjust. The plan is a spine, not a straitjacket.

- **Phase 0 — Scaffold.** Repo, Apache-2.0 `LICENSE` + `NOTICE`, `CONTRIBUTING`,
  folder structure, CI running the reused test harness. *(small)*
- **Phase 1 — Core round-trip.** Remote script: socket, length framing, main-
  thread marshaling, `get`/`set`/`call`, path resolution, serialization. Minimal
  client. Prove `ping` + read tempo + set tempo + read a clip's notes. *(the real
  engineering starts here)*
- **Phase 2 — Observers.** The memory-safe registry: `observe`/`unobserve`,
  teardown on disconnect/deletion, a test that proves no listener leaks.
- **Phase 3 — Convenience layer.** The ergonomic wrappers (§6) + MCP server
  exposing them as tools.
- **Phase 4 — Beyond-LOM.** Port export/save/tap from the reused modules; keep
  them isolated (§8).
- **Phase 5 — Polish.** Version handshake, install flow, docs, packaging.
- **Phase 6 — Release.** Hardening pass, README, first tagged Apache-2.0 release.

**Flexibility rule:** if reality contradicts the blueprint mid-phase (a LOM quirk,
a threading surprise), we stop, update this doc, then continue. The doc is the
source of truth and it's allowed to change.

## 11. Open decisions (need Kim / need research)

- ~~**Name**~~ — **RESOLVED 2026-08-10: "AI Bridge for Ableton Live"**
  (`ai-bridge-for-ableton-live`). Kim's rationale: name for the searcher, not
  the geek — musicians search "AI … Ableton Live", and "AI Bridge…" also sorts
  to the top of alphabetical tool lists.
- ~~**Live's bundled Python version**~~ — **RESOLVED 2026-08-09: Python 3.11**
  (verified from `.pyc` bytecode magic `3495` across all 1,372 Remote Scripts in
  `C:\ProgramData\Ableton\Live 12 Suite`). Remote script targets **3.11,
  stdlib-only**; the host side may use any Python.
- **MCP-native vs plain-TCP-first**: build the raw bridge first, add the MCP
  server as a thin layer on top (recommended) — confirm.
- Package/distribution: PyPI? A one-click installer for the remote script?

## 12. Risks & mitigations

- *Ableton changes the LOM* → version handshake + the test suite catch it; we
  maintain.
- *UI-automation fragility* → isolated modules, never in the core path.
- *Listener leaks / crashes* → the observer registry + leak tests (§5.2, §9).
- *Off-thread API calls* → strict main-thread marshaling (§5.1).
