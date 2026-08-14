# Wire Protocol (v0)

The contract between the **host** (outside Live) and the **remote script** (inside
Live). Phase 1 implements the generic core; conveniences and events layer on
later. Target: Python **3.11**, stdlib-only, both ends.

## Transport & framing

- **TCP, bound to `127.0.0.1`** (local only). **Default port `8766`.**
  > Note: the old MIT fork's bridge uses `8765`. We deliberately pick **8766** so
  > both can run side-by-side during the transition.
- **Length-prefixed frames** (no newline hacks): each message is
  `[4-byte big-endian unsigned length N][N bytes UTF-8 JSON]`.
  This lets large payloads (e.g. 1000-note clips) stream whole.

## Messages

Three message kinds, all JSON objects:

### 1. Request (host → remote)
```json
{ "id": 42, "method": "get", "params": { "path": "live_set", "prop": "tempo" } }
```
- `id`: client-chosen integer, echoed in the response. Unique per in-flight call.
- `method`: string (see Methods).
- `params`: object (method-specific).

### 2. Response (remote → host) — always carries the matching `id`
```json
{ "id": 42, "ok": true,  "result": 120.0 }
{ "id": 42, "ok": false, "error": { "type": "no_such_property", "message": "…" } }
```

### 3. Event (remote → host, unsolicited) — Phase 2, no `id`
```json
{ "event": true, "sub": 7, "path": "live_set", "prop": "tempo", "value": 124.0 }
```
- `sub`: the subscription id returned by `observe`.

## Object references & serialization

- **Paths are LOM paths** — space-separated, Max-style, no negative indices:
  `live_set`, `live_set tracks 0`, `live_set tracks 0 clip_slots 0 clip`.
- A **live object** returned as a value serializes lazily (never a deep graph):
  ```json
  { "$ref": "live_set tracks 0", "type": "Track", "name": "Deep ANA2" }
  ```
  `$ref` is the path to fetch more; `type` is the LOM class; a few cheap
  identifying props (`name`, `id`) may be included. Callers follow `$ref` to go
  deeper (`get`/`children`).
- **Scalars** (int/float/str/bool/null) pass through as JSON.
- **Lists** of objects serialize as arrays of `$ref` objects.

## Methods (Phase 1 — the generic core)

| Method | params | result | notes |
|---|---|---|---|
| `hello` | `{}` | `{bridge_version, live_version, python_version, capabilities[]}` | handshake; call first |
| `ping` | `{}` | `"pong"` | liveness |
| `get` | `{path, prop}` | value (scalar or `$ref`) | read a property |
| `set` | `{path, prop, value}` | `true` | write a property |
| `call` | `{path, func, args?}` | value | call a LOM function |
| `resolve` | `{path}` | `$ref` or `null` | does a path exist? |
| `children` | `{path}` | `{prop: count \| $ref …}` | enumerate a node's navigable members |

### Observers (Phase 2 — implemented)

| Method | params | result | notes |
|---|---|---|---|
| `observe` | `{path, prop}` | `{sub: N}` | attach a listener; events flow as unsolicited `{"event": true, "sub": N, "path", "prop", "value"}` frames |
| `unobserve` | `{sub}` | `true` | detach; error `bad_request` on unknown sub |

- Requires the property to have the LOM listener API
  (`add_<prop>_listener` / `remove_<prop>_listener`) — else error
  `not_observable`.
- **Guaranteed teardown:** all of a client's listeners are removed when it
  disconnects; all listeners are removed on bridge shutdown. No leaks.
- **Never blocks Live:** events are enqueued to a per-client outbox drained by
  a writer thread. A consumer that stops reading fills its outbox (1000
  frames) and is disconnected rather than stalling Live.

### Batch (implemented — 0.6.0)

| Method | params | result | notes |
|---|---|---|---|
| `batch` | `{ops: [{method, params}, …]}` | `[{ok: true, result} \| {ok: false, error}, …]` | many ops, ONE round-trip |

- The per-request cost is dominated by the hop onto Live's main thread (one
  `schedule_message` tick per request), not the LOM access — a batch pays that
  hop **once** for all its ops. Reading a 66-parameter device went from ~65 s
  (4 requests per parameter) to a single batched read.
- Ops run **in order**, each isolated: a failing op yields its error entry and
  the remaining ops still run. The response is always index-aligned with `ops`.
- Any method except `batch` itself may appear in `ops` (no nesting).
- **Max 500 ops per batch** (`bad_request` beyond) — keeps one batch from
  monopolizing a whole main-thread tick. The host client's `get_many` /
  `set_many` chunk transparently past the cap.

### Clip notes (Phase 3 — implemented)

The one convenience that must live in-process: Live's note API deals in
`MidiNoteSpecification` / `MidiNote` objects that JSON `call` args can't
express.

| Method | params | result |
|---|---|---|
| `clip_get_notes` | `{path, from_time?, time_span?, from_pitch?, pitch_span?}` | `[{pitch, start_time, duration, velocity, mute, note_id?, probability?}, …]` |
| `clip_add_notes` | `{path, notes: [{pitch, start_time, duration, velocity?, mute?}]}` | count added |
| `clip_remove_notes` | `{path, from_time?, time_span?, from_pitch?, pitch_span?}` | `true` (whole clip when unwindowed) |

All other conveniences (transport, tracks, devices, mixer) compose the generic
primitives **host-side** (`host/api.py`), and `host/mcp_server.py` exposes the
bridge as MCP tools over stdio (zero dependencies).

## Errors (structured `type` strings)

`no_such_path`, `no_such_property`, `no_such_function`, `not_writable`,
`wrong_type`, `deleted_object` (LOM object was removed mid-call), `live_error`
(wrapped Live API exception), `bad_request` (malformed frame/params),
`internal`. Every handler is wrapped — **an error is always a response, never a
crash inside Live.**

## Threading contract (why the protocol is sync request/response)

Every request is executed **on Live's main thread** (marshaled from the socket
thread) and produces exactly one response with the same `id`. The host may
pipeline multiple `id`s, but the remote script serializes LOM access on Live's
thread — there is no parallel Live API access. Events (Phase 2) are the only
messages not tied to a request `id`.
