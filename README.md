# AI Bridge for Ableton Live

*(short name: **AI Bridge**)*

> **More than a connection — let an AI compose, mix, automate, render, and *listen* inside your Live set.**

AI Bridge is a small add-on for Ableton Live. Once it's enabled, an AI
assistant — or any program you trust — can reach the controls in your Live set:
write melodies into clips, move faders, change the tempo, load instruments,
turn knobs. You talk to the AI; the AI does it in Live, while you watch and
hear everything happen. You keep full control the whole time — nothing leaves
your computer, and nothing happens behind your back.

It's free and open source (Apache-2.0).

## What can the AI actually do?

- **Write and edit music** — put notes into clips, read them back, change
  them: melodies, chords, basslines, drum patterns.
- **Play the set** — start and stop playback, fire clips, set the tempo.
- **Mix** — volume, pan, sends, on every track.
- **Load Live's built-in instruments and effects** — Operator, Wavetable,
  Drift, Analog, reverbs, delays, EQs… and **turn every knob on them**. Almost
  everything that ships with Live can be used.
- **Watch for changes** — the AI can subscribe to things like the tempo or a
  fader and get told the moment they change, instead of asking over and over.
- **See your whole set** — tracks, clips, devices, settings — so it can answer
  questions about your project and make sensible decisions.

## What about third-party plugins? (Kontakt, Opus, Serum…)

Honest answer: **half-open.**

The AI *can* put a third-party plugin on a track, play notes into it, and move
the controls the plugin makes public. What it *cannot* do is reach inside the
plugin's own window — for example, it can't browse Kontakt's library and pick
an instrument in there for you. You choose the sound inside the plugin, and
from that moment the AI can play it, automate it, and mix it like anything
else.

That limit comes from how plugins themselves are built (they don't offer an
outside way in — not to us, not to anyone). There are good workarounds — for
example, saving a plugin *with your chosen sound* as a Live preset once, after
which the AI can recall that exact setup onto any track, any time.

## What you need

1. **Ableton Live 12** (tested on 12.4, Windows — macOS should work, untested).
2. **This bridge, installed as a Control Surface** (2 minutes, below).
3. **Something that talks to it** — an AI app that speaks MCP (like Claude
   Code / Claude Desktop), or a few lines of Python.

## Install

1. **Install the bridge into Live** — one command, no dependencies:
   ```bash
   python install.py
   ```
   (It finds your Ableton User Library automatically and copies the remote
   script in. `python install.py --status` shows where; `--uninstall` removes
   it; `--user-library <path>` overrides a custom location.)
2. **Restart Live**, then **Preferences → Link, Tempo & MIDI → Control
   Surface** — pick **AI Bridge** in an empty slot (leave its Input/Output on
   *None*).
3. That's it. The bridge listens on your own computer only
   (`127.0.0.1:8766`) whenever Live is running.

**Connect an AI via MCP** (Claude Code example):

```bash
claude mcp add ai-bridge -- python /path/to/ai-bridge-for-ableton-live/host/mcp_server.py
```

**Or plain Python:**

```python
from client import Bridge   # host/client.py — no dependencies

with Bridge() as live:
    print(live.get("live_set", "tempo"))       # read the tempo
    live.set("live_set", "tempo", 110.0)       # change it
    live.call("live_set", "start_playing")     # press play
```

---

## For the technically curious

Everything below is for readers who want the how. Musicians can stop here. 🙂

### Architecture

Two halves over a local socket:

- **Remote script** (inside Live, Python 3.11, stdlib-only) — a Control
  Surface that owns a TCP server on `127.0.0.1:8766`. Every request is
  marshaled onto Live's main thread (`schedule_message` + Event) — the Live API
  is single-threaded and is never touched off-thread.
- **Host** (outside Live) — a dependency-free Python client
  (`host/client.py`), an ergonomic layer (`host/api.py`), and a zero-dependency
  **MCP stdio server** (`host/mcp_server.py`, 10 tools).

Wire protocol: length-prefixed JSON frames (4-byte big-endian length + UTF-8
JSON). Request/response with ids, plus unsolicited event frames for
subscriptions. Full spec: [docs/PROTOCOL.md](docs/PROTOCOL.md).

### Full LOM coverage via five primitives

There is no hand-written method per feature. Five generic primitives reach the
**entire Live Object Model**: `get` / `set` / `call` / `resolve` / `children`
(plus `observe`/`unobserve`). Objects serialize lazily as
`{"$ref": path, "type", "name"}` — never a deep graph walk. Object-valued
arguments are passed as `{"$path": "live_set tracks 1"}`. Even browser loading
works through the generic layer alone (`call('live_app browser', 'load_item',
{'$path': 'live_app browser instruments children 18'})` → Operator lands on
the selected track).

The one exception that must live in-process: **clip notes**
(`clip_get_notes` / `clip_add_notes` / `clip_remove_notes`) — Live's note API
deals in `MidiNoteSpecification` objects that JSON arguments can't express.

### Observers built for stability

- Every listener is recorded in a registry; teardown is **guaranteed** on
  unobserve, on client disconnect, and on bridge shutdown. No leaked listeners,
  no Live-bloating zombie callbacks.
- Events are enqueued to a per-client outbox drained by a dedicated writer
  thread — **Live's main thread never blocks on a socket**. A consumer that
  stops reading gets disconnected; Live never stalls.

### Tests

`python tests/test_*.py` — five suites, no pytest or any other dependency
required, no Live required (fakes + a real loopback socket). The Live-facing
glue is validated against a real Live 12.4 instance.

### License & contributions

**Apache-2.0** — free to use, modify, redistribute. Reused MIT-licensed
portions are credited in [NOTICE](NOTICE). The project runs a
**single-maintainer model** (think SQLite): bug reports welcome, pull requests
at the maintainer's discretion — see [CONTRIBUTING.md](CONTRIBUTING.md).

Design blueprint: [DESIGN.md](DESIGN.md) · Wire protocol:
[docs/PROTOCOL.md](docs/PROTOCOL.md) · Reuse provenance:
[PORTING.md](PORTING.md)
