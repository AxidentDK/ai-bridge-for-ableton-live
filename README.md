# AI Bridge for Ableton Live

*(short name: **AI Bridge**)*

> ## ✅ Version 1.0 — stable, and ready to be relied on.
> The bridge is feature-complete and its whole test suite is green: **154 tests**, and
> every tool verified against a running Ableton Live 12.4.3. **62 tools** cover the Live
> Object Model, and the four generic ones reach **563 operations across 21 object types**
> — every one listed by name in [docs/LOM_REFERENCE.md](docs/LOM_REFERENCE.md).
>
> 1.0 is a promise about the interface, not a claim that nothing will ever be added:
> the tools and the wire protocol are stable from here, and anything that breaks them
> gets a major version. Build against it.
>
> Tested on **Windows** with Live 12.4. macOS should work and has not been verified —
> if you run it there, say what happened.

> **More than a connection — let an AI compose, mix, automate, render, and *listen* inside your Live set.**

AI Bridge is a small add-on for Ableton Live. Once it's enabled, an AI
assistant — or any program you trust — can reach the controls in your Live set:
write melodies into clips, move faders, change the tempo, load instruments,
turn knobs. You talk to the AI; the AI does it in Live, while you watch and
hear everything happen. You keep full control the whole time — nothing leaves
your computer, and nothing happens behind your back.

It's free and open source (Apache-2.0).

## Two layers — and you only need the first

**The bridge is the light part, and it is all you need to start.** It installs in
minutes, needs nothing but Python, downloads no models, and sends nothing off your
computer. Everything described below works with the bridge on its own.

**The listening module is an optional heavy part you can add later.** It is a
separate program that listens through your sample library once and writes down what
it heard — style, mood, instrument, character — so the AI can find sounds by what
they *sound like* ("a melancholic pad", "a dusty break") instead of by filename.
It's heavier by nature: it runs a trained audio model, so it wants real disk space
and real CPU time.

**It is not a requirement, and nothing breaks without it.** The bridge checks
whether it's there. If it isn't, searching falls back to filenames plus Live 12's
own audio similarity, and the answer tells you which was used — so you always know
whether something actually listened, or just matched a name.

**That said, it is the recommended setup, and the reason is simple.** Without ears,
the AI works from names somebody else typed years ago. A large sample library is
effectively invisible to it — thousands of files it can only guess at. With ears, it
can weigh up what you actually own and pick the *right* sound rather than a
plausibly-named one. If you want the AI to make full and effective use of what is
already on your drive, add the listening module.

> **Status:** the bridge's half is built and shipping — it already detects the module
> and degrades cleanly when it's absent. The listening module lives in its own
> repository, [**ai-bridge-listener**](https://github.com/AxidentDK/ai-bridge-listener),
> and is usable but still in development: it publishes its own measured accuracy
> (82% of files land in the right family, 37% get named specifically) along with the
> reasons for the weak categories.

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

## How you use it — you just ask

Once the bridge is enabled and your AI assistant is connected, there's nothing
to click or configure. You **talk** — plain English, at the start of a session
or any time mid-flow — and you watch it happen in Live. A few things you can say:

> **One habit worth keeping: save first.** As soon as you open or start a new
> project, do a quick **File → Save As** yourself — give it a name and a home on
> disk. From then on the AI can save it for you any time you ask (*"save the
> set"*). It deliberately won't drive that *first* Save-As dialog, so your
> project can never be written somewhere you didn't choose. Same rule as always:
> save early, save often.

> **Hands off during save & export.** Those two are the only things the AI does
> by driving Live's own dialogs with your keyboard — a few seconds for a save,
> as long as the render takes for an export. While that's happening, don't type
> or click: your input lands in the middle of the dialog walk and can break it
> (or end up somewhere you didn't intend). Everything else the AI does goes
> through Live's API and never touches your keyboard — type and play freely.

**Getting around**
- *"Switch to Arranger view."* (or *"show me the session grid"*)
- *"What's in my set right now?"*

**Building**
- *"Add a MIDI track with Operator and call it Lead."*
- *"Write an 8-bar Am–F–C–G progression on the pad track."*
- *"Give me a simple bassline under those chords."*

**Playing & mixing**
- *"Play from the top."* / *"Stop."*
- *"Set the tempo to 90."*
- *"Turn the bass down 3 dB and pan the lead a little left."*

**Finishing & housekeeping**
- *"Clean up the unused tracks."*
- *"Render the arrangement to a WAV and tell me how loud it is in LUFS."*
- *"Save the set."*

**Keeping watch**
- *"Tell me if the tempo changes while I'm working."*

You stay in control the whole time — you see and hear every change as it
happens, you can undo anything in Live, and nothing leaves your computer.

## Which AI? Either. Pick your window.

The bridge does not care which AI is on the other end, but the two connect differently,
so you get a different window:

| | **Claude** | **Gemini** |
|---|---|---|
| Your window | the **Claude desktop app** (or Claude Code) | **Gemini Studio**, included |
| How it connects | MCP — register the bridge as a server | function calling, built in |
| What you need | a Claude subscription | a **free** Gemini API key |

**Gemini Studio** is a chat window that ships with the bridge. Paste an API key once and
Gemini can read your set, search your library by how something *sounds*, audition it, load
it, write clips and move controls — with every tool call shown as it happens, so you can
see what it reached for. The installer puts it on your desktop.

## What you need

1. **Ableton Live 12** (tested on 12.4, Windows — macOS should work, untested).
2. **Python 3.10+** — nothing else. No pip packages.
3. **An AI**: the Claude desktop app, or a free Gemini API key for the Studio window.

## Install

**Windows — one line.** Paste this into PowerShell. It downloads the bridge, installs it
into Live, and puts a Gemini Studio icon on your desktop. No admin rights; everything
lands under your own user profile.

```powershell
irm https://raw.githubusercontent.com/AxidentDK/ai-bridge-for-ableton-live/main/install.ps1 | iex
```

**Or by hand** (any platform, or if you would rather read what you run):

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

**Then open Gemini Studio** — the desktop icon, or:

```bash
python tools/gemini_studio.py
```

First run, choose **Settings → Gemini API key**. The dialog links to Google's page for a
free one, checks the key before saving it, and stores it in your user profile — outside
the program's folder, so it cannot be shared or committed by accident.

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
  **MCP stdio server** (`host/mcp_server.py`, 17 tools).

Wire protocol: length-prefixed JSON frames (4-byte big-endian length + UTF-8
JSON). Request/response with ids, plus unsolicited event frames for
subscriptions. Full spec: [docs/PROTOCOL.md](docs/PROTOCOL.md).

### Full LOM coverage via five primitives

There is no hand-written method per feature. Five generic primitives reach the
**entire Live Object Model**: `get` / `set` / `call` / `resolve` / `children`
(plus `observe`/`unobserve`, and `batch` — many ops in one round-trip, since
the real per-request cost is the hop onto Live's main thread; a 66-parameter
device read went from ~65 s to a single batch). Objects serialize lazily as
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

**Using it:** [docs/GUIDE.md](docs/GUIDE.md) — a short guide to the few things that
aren't obvious (switching views, why similarity search needs folders added as
*Places*, what the descriptions will and won't claim).

**What it can do:** [docs/TOOLS.md](docs/TOOLS.md) — all 62 tools and what each one
does, grouped. You never call them by name, but you cannot ask for something you did
not know was possible.

**How far the coverage goes:** [docs/LOM_REFERENCE.md](docs/LOM_REFERENCE.md) — every
property and function the bridge reaches, by name: **563 operations across 21 object
types**, read from a running Live rather than copied from a manual. The 62 named tools
are an ergonomic layer over this, not a limit on it.

**Wire protocol:** [docs/PROTOCOL.md](docs/PROTOCOL.md) — everything needed to talk
to the bridge directly, or to port it.
