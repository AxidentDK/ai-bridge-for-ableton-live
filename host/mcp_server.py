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
SERVER_VERSION = "0.3.0"
PROTOCOL_VERSION = "2024-11-05"


# --- tool definitions -----------------------------------------------------------

def _schema(props: dict, required: list[str] | None = None) -> dict:
    """JSON Schema for a tool's inputs.

    ``required`` defaults to empty: a tool whose arguments are ALL optional is
    perfectly valid (live_meters, live_tap_discover). Making it mandatory meant
    such a tool raised at import time, which took the whole MCP server down at
    startup — every tool vanished, not just the offending one.
    """
    return {"type": "object", "properties": props, "required": list(required or [])}

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
    {"name": "live_batch",
     "description": ("Run MANY LOM operations in ONE round-trip — vastly faster than "
                     "separate calls (each call costs a hop onto Live's main thread; a "
                     "batch pays that once). Use whenever reading/writing more than a few "
                     "properties. ops = [{method: 'get'|'set'|'call'|'resolve'|'children', "
                     "params: {...same params as the matching live_* tool}}]. Returns a "
                     "per-op list of {ok, result|error}; one failing op never aborts the "
                     "rest. Max 500 ops per batch."),
     "inputSchema": _schema({"ops": {"type": "array", "items": {
         "type": "object", "properties": {
             "method": {"type": "string",
                        "enum": ["get", "set", "call", "resolve", "children"]},
             "params": {"type": "object"}},
         "required": ["method", "params"]}}}, ["ops"])},
    {"name": "live_device_parameters",
     "description": ("All parameters of a device (index, name, value, min, max) in one "
                     "fast batched read — use this instead of many live_get calls."),
     "inputSchema": _schema({"track": {"type": "integer"}, "device": {"type": "integer"}},
                            ["track", "device"])},
    {"name": "live_transform_notes",
     "description": ("Musical surgery on a clip's notes — things Live's stock MIDI devices "
                     "cannot express because they act per-note on the live stream with no "
                     "context. operation: transpose (chromatic, or by scale DEGREES so it "
                     "stays in key) | scale_fold (snap a phrase into a key) | harmonize "
                     "(add a diatonic voice — play one line, get harmony) | invert | "
                     "retrograde | quantize (partial via amount, plus swing) | humanize "
                     "(seeded, repeatable) | velocity_ramp | duration_scale. "
                     "preview=true returns the result without writing."),
     "inputSchema": _schema({"clip_path": _PATH, "operation": {"type": "string"},
                             "semitones": {"type": "integer"},
                             "root": {"type": "string"}, "scale": {"type": "string"},
                             "degrees": {"type": "array", "items": {"type": "integer"}},
                             "intervals": {"type": "array", "items": {"type": "integer"}},
                             "amount": {"type": "number"}, "grid": {"type": "number"},
                             "swing": {"type": "number"}, "timing_ms": {"type": "number"},
                             "velocity_range": {"type": "integer"},
                             "seed": {"type": "integer"}, "axis": {"type": "integer"},
                             "preview": {"type": "boolean"}},
                            ["clip_path", "operation"])},
    {"name": "live_modulation_matrix",
     "description": ("Read a device's modulation matrix — which sources modulate which "
                     "parameters and by how much. Lists only non-zero routings (the grid "
                     "is mostly empty). Works on Live's modern instruments (Wavetable, "
                     "Drift, Meld). Sources are by index; Live exposes no source names."),
     "inputSchema": _schema({"track": {"type": "integer"}, "device": {"type": "integer"},
                             "include_zero": {"type": "boolean"}}, ["track", "device"])},
    {"name": "live_modulate",
     "description": ("Route a modulation source to a parameter at a given amount — e.g. "
                     "make an LFO move the filter cutoff. Names the parameter (or its "
                     "index) and adds it to the modulation matrix automatically if it "
                     "isn't a target yet. This shapes what MOVES, not just where a knob "
                     "sits."),
     "inputSchema": _schema({"track": {"type": "integer"}, "device": {"type": "integer"},
                             "parameter": {"type": "string"},
                             "source": {"type": "integer"}, "amount": {"type": "number"}},
                            ["track", "device", "parameter", "source", "amount"])},
    {"name": "live_meters",
     "description": ("Read every track's output level. With duration_ms > 0 it samples "
                     "repeatedly and keeps the PEAK per track — use that, since a single "
                     "read catches one instant and can miss a hit entirely. Reports "
                     "left/right, an estimated dB, and a silent flag; good for gain "
                     "staging and for finding which track is actually making sound."),
     "inputSchema": _schema({"duration_ms": {"type": "number"},
                             "interval_ms": {"type": "number"},
                             "include_return_tracks": {"type": "boolean"}})},
    {"name": "live_load_device",
     "description": ("Load a device onto a track BY NAME from Live's browser — instruments, "
                     "MIDI effects, audio effects, plugins, Max for Live. No UI needed. "
                     "Live places it correctly for its type (MIDI effects land before the "
                     "instrument). Verified by the track's device count changing. Searches "
                     "NESTED folders too (max_depth, default 3) — necessary because the "
                     "plugins category opens on vendor folders, not plugins."),
     "inputSchema": _schema({"name": {"type": "string"}, "track": {"type": "integer"},
                             "category": {"type": "string"},
                             "max_depth": {"type": "integer"}}, ["name"])},
    {"name": "live_browse",
     "description": ("List/filter loadable items in a browser category (instruments, "
                     "midi_effects, audio_effects, plugins, max_for_live, drums, sounds) — "
                     "use it to find the exact name to pass to live_load_device. Raise "
                     "max_depth to see nested items: the plugins category's top level is "
                     "VENDOR FOLDERS, so depth 1 shows no plugins at all."),
     "inputSchema": _schema({"category": {"type": "string"}, "query": {"type": "string"},
                             "limit": {"type": "integer"},
                             "max_depth": {"type": "integer"}}, ["category"])},
    {"name": "live_meters_observed",
     "description": ("Peak-hold metering: a polled baseline PLUS every level change Live "
                     "pushes during duration_ms, so a transient between polls is still "
                     "caught. Live's meter listener fires on change (a decay emits a burst, "
                     "a steady tone almost nothing), which is why both are combined. Use "
                     "live_meters for a cheap instantaneous glance."),
     "inputSchema": _schema({"duration_ms": {"type": "number"},
                             "include_return_tracks": {"type": "boolean"}})},
    {"name": "live_watch",
     "description": ("Subscribe to a BUNDLE of things worth noticing, so Live pushes them to "
                     "you: transport | performance (which clip is playing) | edits (clips "
                     "appearing/disappearing) | mixer | structure (tracks/devices added) | "
                     "key (Live 12 scale settings) | focus (selected device) | dialogs. "
                     "Collect with live_events, stop with live_unobserve."),
     "inputSchema": _schema({"preset": {"type": "string"},
                             "tracks": {"type": "array", "items": {"type": "integer"}},
                             "max_slots": {"type": "integer"}}, ["preset"])},
    {"name": "live_dialog",
     "description": ("Is Live showing a modal dialog, and what does it say? A modal blocks "
                     "everything else (a save prompt, a warning), and this is the only way "
                     "to see one without a screenshot. Optionally press a button by index — "
                     "not done unless asked, since dismissing can discard work."),
     "inputSchema": _schema({"press": {"type": "integer"}})},
    {"name": "live_observe",
     "description": ("SUBSCRIBE to a property so Live pushes changes instead of you polling "
                     "— e.g. watch the playing clip, a device parameter, or the tempo. "
                     "Returns a subscription id; collect what arrived with live_events, and "
                     "stop with live_unobserve. Subscriptions are torn down automatically if "
                     "the connection drops."),
     "inputSchema": _schema({"path": _PATH, "prop": {"type": "string"}}, ["path", "prop"])},
    {"name": "live_unobserve",
     "description": "Cancel a subscription created by live_observe.",
     "inputSchema": _schema({"subscription": {"type": "integer"}}, ["subscription"])},
    {"name": "live_events",
     "description": ("Collect property-change events that have arrived since the last call "
                     "(from live_observe subscriptions). Non-blocking by default; give "
                     "timeout to wait for the next one."),
     "inputSchema": _schema({"timeout": {"type": "number"}})},
    {"name": "live_scenes",
     "description": "List every scene: name, whether it is empty, whether it is triggered.",
     "inputSchema": _schema({})},
    {"name": "live_scene",
     "description": ("Act on scenes: create | delete | duplicate | fire | rename | capture. "
                     "'capture' is Live's Capture and Insert Scene — it grabs whatever is "
                     "playing right now into a new scene."),
     "inputSchema": _schema({"action": {"type": "string"}, "index": {"type": "integer"},
                             "name": {"type": "string"}}, ["action"])},
    {"name": "live_routing",
     "description": ("Read a track's input/output routing, monitoring state and arm — plus "
                     "the AVAILABLE routing choices, whose names are Live's own strings and "
                     "cannot be guessed."),
     "inputSchema": _schema({"track": {"type": "integer"}}, ["track"])},
    {"name": "live_set_routing",
     "description": ("Set a track's input/output routing by display name (as shown in Live), "
                     "and/or its monitoring state (0=In, 1=Auto, 2=Off) and arm."),
     "inputSchema": _schema({"track": {"type": "integer"}, "input_type": {"type": "string"},
                             "output_type": {"type": "string"},
                             "monitoring": {"type": "integer"}, "arm": {"type": "boolean"}},
                            ["track"])},
    {"name": "live_preview",
     "description": ("Audition a browser item WITHOUT loading it — the browser's headphone "
                     "button. Pairs with live_similar_sounds: find something similar, hear "
                     "it, then load it. Call with stop=true to silence."),
     "inputSchema": _schema({"name": {"type": "string"}, "path": {"type": "string"},
                             "category": {"type": "string"}, "stop": {"type": "boolean"}})},
    {"name": "live_edit_notes",
     "description": ("Change or delete SPECIFIC notes by note_id, leaving every other note "
                     "untouched — unlike live_transform_notes, which rewrites a whole "
                     "window. Get ids from live_clip_notes. Each edit: {note_id, pitch?, "
                     "start_time?, duration?, velocity?, mute?, probability?}; omitted "
                     "fields keep their current value."),
     "inputSchema": _schema({"clip_path": _PATH,
                             "edits": {"type": "array", "items": {"type": "object"}},
                             "delete_ids": {"type": "array", "items": {"type": "integer"}}},
                            ["clip_path"])},
    {"name": "live_envelope_curve",
     "description": ("Write a SHAPED automation sweep into a clip envelope — linear, "
                     "ease_in/out/in_out, exponential, logarithmic, sine, cosine, s_curve, "
                     "triangle, saw, square, random. Live stores points, so the curve is "
                     "sampled into `steps` of them (32 over a bar looks continuous)."),
     "inputSchema": _schema({"clip_path": _PATH, "parameter_path": {"type": "string"},
                             "start_value": {"type": "number"}, "end_value": {"type": "number"},
                             "start_time": {"type": "number"}, "length": {"type": "number"},
                             "curve": {"type": "string"}, "steps": {"type": "integer"},
                             "clear_first": {"type": "boolean"}},
                            ["clip_path", "parameter_path", "start_value", "end_value"])},
    {"name": "live_midi_cc",
     "description": ("Send a MIDI CC on a virtual port — reaches plugin parameters the LOM "
                     "cannot, without the per-parameter Configure click. Give cc directly, "
                     "or parameter+map_name. REQUIRES the optional 'midi' extra (mido, "
                     "python-rtmidi), that the plugin has learned the CC, and that the "
                     "track's MIDI From points at this port. Call with ports=true to list "
                     "available MIDI outputs and CC maps."),
     "inputSchema": _schema({"cc": {"type": "integer"}, "value": {"type": "number"},
                             "channel": {"type": "integer"}, "parameter": {"type": "string"},
                             "map_name": {"type": "string"}, "port_name": {"type": "string"},
                             "ports": {"type": "boolean"}})},
    {"name": "live_undo",
     "description": "Undo or redo the last action in Live (action: undo | redo).",
     "inputSchema": _schema({"action": {"type": "string"}})},
    {"name": "live_similar_sounds",
     "description": ("Find sounds similar to a given one, using LIVE 12'S OWN trained audio "
                     "embeddings (64-d, read from its analysis database). Real audio "
                     "similarity with no model to download and no dependency — Live already "
                     "analysed the library and keeps it current. Pick the base by `query` "
                     "(filename substring) or `path`. Smaller distance = more similar."),
     "inputSchema": _schema({"query": {"type": "string"}, "path": {"type": "string"},
                             "limit": {"type": "integer"},
                             "db_path": {"type": "string"},
                             "include_self": {"type": "boolean"}})},
    {"name": "live_find_sound",
     "description": ("Find a clip or one-shot BY MEANING — genre, mood, instrument — using the "
                     "listening sidecar if it is installed. `query` is free text and "
                     "searches both what a file was HEARD as and what it is CALLED; each "
                     "result says which in `matched_by`, so a filename hit is never "
                     "mistaken for a verdict. Use genre/mood/instrument/event/tag to "
                     "restrict a term to the heads that can answer it. If the sidecar is "
                     "NOT installed this "
                     "degrades automatically to live_similar_sounds: a filename match, "
                     "expanded by Live 12's acoustic embeddings. That still returns real "
                     "results, but the words then come from whoever named the file rather "
                     "than from anything that listened, and the reply says which path was "
                     "used. Absence of the sidecar is never an error. Files shorter "
                     "than ~2s are treated as ONE-SHOTS: genre/mood/style verdicts are "
                     "suppressed for them, because those models were trained on full "
                     "tracks and answer a snare from their training priors rather than "
                     "from the sound. Audio-event and NSynth verdicts are kept, being "
                     "trained on events and single notes. Set include_unreliable to "
                     "see the suppressed tags anyway."),
     "inputSchema": _schema({"query": {"type": "string"}, "genre": {"type": "string"},
                             "mood": {"type": "string"}, "instrument": {"type": "string"},
                             "event": {"type": "string"},
                             "tag": {"type": "string"},
                             "min_confidence": {"type": "number"},
                             "limit": {"type": "integer"},
                             "include_unreliable": {"type": "boolean"},
                             "db_path": {"type": "string"}})},
    {"name": "live_sidecar_status",
     "description": ("Is the listening sidecar installed, and what has it analysed? Reports "
                     "the database it found, how many files carry tags, and which tag "
                     "namespaces exist. 'available: false' is a normal state — the sidecar is "
                     "optional and the bridge falls back to Live's own embeddings."),
     "inputSchema": _schema({"db_path": {"type": "string"}})},
    {"name": "live_sound_index",
     "description": ("Status of Live's audio-analysis database: how many files are analysed, "
                     "broken down per browser Place. Use it to find folders that are NOT "
                     "indexed — Live only analyses folders added as Places in its browser "
                     "(Add Folder...), so anything else is invisible to similarity search."),
     "inputSchema": _schema({"db_path": {"type": "string"}})},
    {"name": "live_describe_audio",
     "description": ("Describe a WAV's measurable CHARACTER: bright/dark, tonal/noisy, "
                     "percussive/sustained, stereo width and phase, dynamics, a "
                     "chroma-based key estimate and a tempo estimate (with a confidence — "
                     "tempo is accurate on rhythmic material and unreliable on sustained). "
                     "It cannot judge genre, era or mood — that needs a model that hears "
                     "the audio. Pair with live_tap_capture or live_export to describe "
                     "anything in the set."),
     "inputSchema": _schema({"path": {"type": "string"}}, ["path"])},
    {"name": "live_describe_clip",
     "description": ("Describe a MIDI clip musically from its notes: detected key (with a "
                     "confidence margin, and flagged when genuinely ambiguous), texture "
                     "(monophonic/chordal), named chords, register, density, and "
                     "step-vs-leap contour. Structure only — it does not claim style or "
                     "mood, which note data cannot support."),
     "inputSchema": _schema({"clip_path": _PATH}, ["clip_path"])},
    {"name": "live_describe_clips",
     "description": ("Describe EVERY MIDI clip in the set — a searchable index of your own "
                     "material, so 'Idea 12' becomes 'F minor, chordal, sparse, low "
                     "register'. Audio clips are listed but not described (that needs a "
                     "model that hears the waveform)."),
     "inputSchema": _schema({"include_arrangement": {"type": "boolean"},
                             "limit": {"type": "integer"}})},
    {"name": "live_browser_index",
     "description": ("Disk cache of Live's browser tree, so device/preset search is a file "
                     "read instead of a multi-second walk (scanning 'sounds' live costs "
                     "~15s). No args = status. refresh=true rebuilds it (slow by nature). "
                     "live_browse and live_load_device use it automatically when it covers "
                     "the category deeply enough. It is a SNAPSHOT — rebuild after "
                     "installing plugins or packs."),
     "inputSchema": _schema({"refresh": {"type": "boolean"},
                             "categories": {"type": "array", "items": {"type": "string"}},
                             "max_depth": {"type": "integer"}})},
    {"name": "live_plugin_parameters",
     "description": ("Search a VST/AU plugin's FULL parameter list, including the ones Live "
                     "hides from the LOM (a fresh Vital reports 1 of its 2855). Use `query` "
                     "to filter by name — the unfiltered list is far too large to return. "
                     "`controllable` marks parameters whose values can actually be read/set "
                     "today; the rest are name-only until configured on the device in Live."),
     "inputSchema": _schema({"track": {"type": "integer"}, "device": {"type": "integer"},
                             "query": {"type": "string"},
                             "limit": {"type": "integer"}, "offset": {"type": "integer"}},
                            ["track", "device"])},
    {"name": "live_tap_discover",
     "description": ("Locate the AgentAudioTap Max for Live device's command/status files "
                     "(it writes a status file when loaded, which reveals where Max put "
                     "them). Run this once after loading the device onto a track; pass the "
                     "returned command_file to the other tap tools."),
     "inputSchema": _schema({"extra_dirs": {"type": "array", "items": {"type": "string"}}})},
    {"name": "live_tap_status",
     "description": ("Liveness check of the audio-tap device: returns its last reported "
                     "state (recording, path, seq). A timeout means the device is not "
                     "loaded or is a stale instance that needs reloading."),
     "inputSchema": _schema({"command_file": {"type": "string"}}, ["command_file"])},
    {"name": "live_tap_capture",
     "description": ("Record audio AT THE TAP'S INSERTION POINT to a WAV — the one thing "
                     "the LOM cannot do (it has no audio at all, and live_export only "
                     "renders the finished arrangement). Put the device anywhere in a "
                     "chain to measure that exact point, then analyze with "
                     "live_analyze_wav. Records what is PLAYING, so start the transport "
                     "around the call or the capture is legitimately silent."),
     "inputSchema": _schema({"command_file": {"type": "string"},
                             "output_path": {"type": "string"},
                             "duration_ms": {"type": "number"}},
                            ["command_file", "output_path", "duration_ms"])},
    {"name": "live_snapshot",
     "description": ("Capture EVERY parameter of a device or rack as a recallable snapshot "
                     "(stateless — you hold the returned object and pass it to live_snapshot_apply "
                     "or live_morph). One batched read."),
     "inputSchema": _schema({"track": {"type": "integer"}, "device": {"type": "integer"}},
                            ["track", "device"])},
    {"name": "live_snapshot_apply",
     "description": ("Recall a snapshot: set every parameter back to its captured value on the "
                     "device at track/device. Verifies the parameter count matches the target "
                     "device first; read-only parameters are reported, not fatal."),
     "inputSchema": _schema({"track": {"type": "integer"}, "device": {"type": "integer"},
                             "snapshot": {"type": "object"}},
                            ["track", "device", "snapshot"])},
    {"name": "live_morph",
     "description": ("Interpolate between two snapshots and apply the blend to the device at "
                     "track/device. amount 0..1 (0 = snapshot_a, 1 = snapshot_b); quantized "
                     "parameters snap to the nearest step. One batched write."),
     "inputSchema": _schema({"track": {"type": "integer"}, "device": {"type": "integer"},
                             "snapshot_a": {"type": "object"}, "snapshot_b": {"type": "object"},
                             "amount": {"type": "number"}},
                            ["track", "device", "snapshot_a", "snapshot_b", "amount"])},
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
    {"name": "live_clip_envelope",
     "description": ("Write parameter automation into a clip: insert envelope steps for one "
                     "device/mixer parameter (filter sweeps, fades — movement over time). "
                     "path = the clip; parameter = the DeviceParameter's LOM path (e.g. "
                     "'live_set tracks 0 devices 0 parameters 5' or 'live_set tracks 0 "
                     "mixer_device volume'). Each step: {time (beats), value, length? "
                     "(beats)} — a step HOLDS its value over [time, time+length); omit "
                     "length to auto-fill to the next step (staircase; the last runs to "
                     "the clip end). Approximate a smooth sweep with ~16 steps. "
                     "clear_first=true wipes that parameter's envelope before writing."),
     "inputSchema": _schema({"path": _PATH, "parameter": _PATH,
                             "steps": {"type": "array", "items": {
                                 "type": "object", "properties": {
                                     "time": {"type": "number"},
                                     "value": {"type": "number"},
                                     "length": {"type": "number"}},
                                 "required": ["time", "value"]}},
                             "clear_first": {"type": "boolean"}},
                            ["path", "parameter", "steps"])},
    {"name": "live_clip_envelope_read",
     "description": ("Sample a clip envelope's value at given times (beats) — verify what "
                     "automation a clip actually holds for a parameter."),
     "inputSchema": _schema({"path": _PATH, "parameter": _PATH,
                             "times": {"type": "array", "items": {"type": "number"}}},
                            ["path", "parameter", "times"])},
    {"name": "live_record_takes",
     "description": ("Comping: loop-record N passes over a section of the arrangement — each "
                     "pass lands on its own take lane of the armed track (Live 11+). The "
                     "musician plays along with the loop; afterwards audition with live_takes "
                     "and promote the keeper with live_choose_take. BLOCKS for the real "
                     "recording duration (passes x loop length). Count-in and launch "
                     "quantization are disabled during, restored after."),
     "inputSchema": _schema({"track": {"type": "integer"},
                             "start_beats": {"type": "number"},
                             "length_beats": {"type": "number"},
                             "passes": {"type": "integer"}},
                            ["track", "start_beats", "length_beats"])},
    {"name": "live_print_sequence",
     "description": ("Print what a track's arpeggiator/sequencer PLAYS into editable MIDI "
                     "notes on a NEW track. Captures the post-MIDI-effects note stream by "
                     "routing + loop-recording one pass — the generated pattern becomes "
                     "ordinary notes to view/edit ('tweak the pattern like you tweak the "
                     "sound'). Give source_slot to auto-fire a session clip that feeds the "
                     "generator (e.g. the held note an arp needs). REAL-TIME: N beats take "
                     "N beats. Works for Live MIDI effects; VST-internal sequencers only if "
                     "the plugin sends its MIDI back to Live."),
     "inputSchema": _schema({"source_track": {"type": "integer"},
                             "length_beats": {"type": "number"},
                             "start_beats": {"type": "number"},
                             "source_slot": {"type": "integer"},
                             "name": {"type": "string"}},
                            ["source_track", "length_beats"])},
    {"name": "live_takes",
     "description": "List a track's take lanes and the take clips on them (comping).",
     "inputSchema": _schema({"track": {"type": "integer"}}, ["track"])},
    {"name": "live_choose_take",
     "description": ("Promote a MIDI take-lane clip onto the track's main lane — the comp "
                     "pick (notes are copied into a fresh main-lane clip over the same "
                     "span). lane/clip indices come from live_takes. MIDI takes only; "
                     "audio takes must be comped in Live's own UI."),
     "inputSchema": _schema({"track": {"type": "integer"}, "lane": {"type": "integer"},
                             "clip": {"type": "integer"}}, ["track", "lane"])},
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
    {"name": "live_export_stems",
     "description": ("Render EVERY audio-producing track to its own aligned WAV (stems) in "
                     "ONE offline pass — the studio-interchange move for handing tracks to "
                     "another DAW. Drives the export dialog with 'Rendered Track: All "
                     "Individual Tracks'. output_dir must be an existing folder with no "
                     ".wav files in it; files land as '<base_name> <track>.wav' plus "
                     "'<base_name>.wav' for the main mix, every track included. Briefly "
                     "takes over keyboard focus (hands off during it)."),
     "inputSchema": _schema({"output_dir": {"type": "string"},
                             "base_name": {"type": "string"},
                             "start_beats": {"type": "number"},
                             "length_beats": {"type": "number"},
                             "timeout": {"type": "number"}}, ["output_dir"])},
    {"name": "live_analyze_wav",
     "description": ("Measure a WAV file: integrated loudness (LUFS, ITU-R BS.1770-4), sample "
                     "peak dBFS, and band energies. Requires numpy."),
     "inputSchema": _schema({"path": {"type": "string", "description": "path to a .wav file"}},
                            ["path"])},
    {"name": "live_show_view",
     "description": ("Switch Live's main view. name = Arranger | Session | Browser | "
                     "Detail | Detail/Clip | Detail/DeviceChain."),
     "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},
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
    if name == "live_batch":
        return b.batch(args["ops"])
    if name == "live_device_parameters":
        from api import Live
        return Live(b).parameters(int(args["track"]), int(args["device"]))
    if name == "live_transform_notes":
        from api import Live
        opts = {k: v for k, v in args.items() if k not in ("clip_path", "operation")}
        return Live(b).transform_notes(args["clip_path"], args["operation"], **opts)
    if name == "live_modulation_matrix":
        from api import Live
        return Live(b).modulation_matrix(int(args["track"]), int(args["device"]),
                                         bool(args.get("include_zero", False)))
    if name == "live_modulate":
        from api import Live
        return Live(b).modulate(int(args["track"]), int(args["device"]),
                                args["parameter"], int(args["source"]),
                                float(args["amount"]))
    if name == "live_meters":
        from api import Live
        return Live(b).meters(float(args.get("duration_ms", 0.0)),
                              float(args.get("interval_ms", 25.0)),
                              bool(args.get("include_return_tracks", False)))
    if name == "live_load_device":
        from api import Live
        return Live(b).load_device(args["name"], args.get("track"), args.get("category"), int(args.get("max_depth", 3)))
    if name == "live_browse":
        from api import Live
        return Live(b).browse(args["category"], args.get("query"),
                              int(args.get("limit", 60)),
                              int(args.get("max_depth", 1)))
    if name == "live_meters_observed":
        from api import Live
        return Live(b).meters_observed(float(args.get("duration_ms", 1000.0)),
                                       bool(args.get("include_return_tracks", False)))
    if name == "live_watch":
        from api import Live
        return Live(b).watch(args["preset"], args.get("tracks"),
                             int(args.get("max_slots", 8)))
    if name == "live_dialog":
        from api import Live
        return Live(b).dialog(args.get("press"))
    if name == "live_observe":
        return {"subscription": b.observe(args["path"], args["prop"]),
                "watching": f"{args['path']} {args['prop']}",
                "note": "collect changes with live_events; stop with live_unobserve"}
    if name == "live_unobserve":
        return {"cancelled": b.unobserve(int(args["subscription"]))}
    if name == "live_events":
        timeout = float(args.get("timeout", 0.0))
        events = b.drain_events(timeout) if hasattr(b, "drain_events") else []
        return {"count": len(events), "events": events}
    if name == "live_scenes":
        from api import Live
        return Live(b).scenes()
    if name == "live_scene":
        from api import Live
        return Live(b).scene(args["action"], args.get("index"), args.get("name"))
    if name == "live_routing":
        from api import Live
        return Live(b).routing(int(args["track"]))
    if name == "live_set_routing":
        from api import Live
        return Live(b).set_routing(int(args["track"]), args.get("input_type"),
                                   args.get("output_type"), args.get("monitoring"),
                                   args.get("arm"))
    if name == "live_preview":
        from api import Live
        return Live(b).preview(args.get("name"), args.get("path"),
                               bool(args.get("stop", False)), args.get("category"))
    if name == "live_edit_notes":
        from api import Live
        return Live(b).edit_notes(args["clip_path"], args.get("edits"),
                                  args.get("delete_ids"))
    if name == "live_envelope_curve":
        from api import Live
        return Live(b).envelope_curve(
            args["clip_path"], args["parameter_path"],
            float(args["start_value"]), float(args["end_value"]),
            float(args.get("start_time", 0.0)), float(args.get("length", 4.0)),
            args.get("curve", "linear"), int(args.get("steps", 32)),
            bool(args.get("clear_first", True)))
    if name == "live_midi_cc":
        import midi_cc
        if args.get("ports"):
            return midi_cc.ports()
        return midi_cc.send_cc(args.get("cc"), float(args.get("value", 0.0)),
                               int(args.get("channel", 0)), args.get("parameter"),
                               args.get("map_name"),
                               args.get("port_name", midi_cc.PORT_NAME))
    if name == "live_undo":
        action = str(args.get("action", "undo")).lower()
        if action not in ("undo", "redo"):
            raise ValueError("action must be undo or redo")
        b.call("live_set", action)
        return {"action": action,
                "can_undo": b.get("live_set", "can_undo"),
                "can_redo": b.get("live_set", "can_redo")}
    if name == "live_similar_sounds":
        import similar
        return similar.find_similar(args.get("query"), args.get("path"),
                                    int(args.get("limit", 10)), args.get("db_path"),
                                    bool(args.get("include_self", False)))
    if name == "live_sidecar_status":
        import sidecar
        return sidecar.status(args.get("db_path"))
    if name == "live_find_sound":
        # The ONE place the sidecar-vs-fallback policy lives, so it is visible rather
        # than scattered through the reader.
        import sidecar
        try:
            return sidecar.find(args.get("query"), args.get("genre"), args.get("mood"),
                                args.get("instrument"),
                                float(args.get("min_confidence", 0.0)),
                                int(args.get("limit", 20)), args.get("db_path"),
                                args.get("event"), args.get("tag"),
                                bool(args.get("include_unreliable", False)))
        except LookupError as exc:
            import similar
            asked_by_meaning = [k for k in ("genre", "mood", "instrument") if args.get(k)]
            if not args.get("query"):
                return {"source": "unavailable", "reason": str(exc),
                        "note": ("Without the sidecar a search needs a `query` (filename "
                                 "substring) to seed from — acoustic similarity starts "
                                 "from a sound, not from a word.")}
            out = similar.find_similar(args.get("query"), None,
                                       int(args.get("limit", 20)), None, True)
            out["source"] = "fallback: live_similar_sounds"
            out["sidecar"] = str(exc)
            if asked_by_meaning:
                out["ignored"] = asked_by_meaning
                out["note"] = ("The sidecar is not installed, so "
                               f"{', '.join(asked_by_meaning)} could NOT be honoured — "
                               "nothing here has listened to these files. These are "
                               "filename matches expanded by acoustic similarity.")
            return out
    if name == "live_sound_index":
        import similar
        return similar.index_status(args.get("db_path"))
    if name == "live_describe_audio":
        import audio_features
        return audio_features.describe(args["path"])
    if name == "live_describe_clip":
        from api import Live
        return Live(b).describe_clip(args["clip_path"])
    if name == "live_describe_clips":
        from api import Live
        return Live(b).describe_clips(bool(args.get("include_arrangement", True)),
                                      int(args.get("limit", 200)))
    if name == "live_browser_index":
        from api import Live
        live = Live(b)
        if args.get("refresh"):
            return live.build_browser_index(args.get("categories"),
                                            int(args.get("max_depth", 2)))
        return live.browser_index_status()
    if name == "live_plugin_parameters":
        from api import Live
        return Live(b).plugin_parameters(
            int(args["track"]), int(args["device"]), args.get("query"),
            int(args.get("limit", 60)), int(args.get("offset", 0)))
    if name == "live_tap_discover":
        import tap
        return tap.discover(args.get("extra_dirs"))
    if name == "live_tap_status":
        import tap
        return tap.AudioTap(args["command_file"]).status()
    if name == "live_tap_capture":
        import tap
        return tap.AudioTap(args["command_file"]).capture(
            args["output_path"], float(args["duration_ms"]))
    if name == "live_snapshot":
        from api import Live
        return Live(b).snapshot(int(args["track"]), int(args["device"]))
    if name == "live_snapshot_apply":
        from api import Live
        return Live(b).apply_snapshot(int(args["track"]), int(args["device"]), args["snapshot"])
    if name == "live_morph":
        from api import Live
        return Live(b).morph(int(args["track"]), int(args["device"]),
                             args["snapshot_a"], args["snapshot_b"], float(args["amount"]))
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
    if name == "live_clip_envelope":
        if args.get("clear_first"):
            b.request("clip_envelope_clear", path=args["path"],
                      parameter=args["parameter"])
        return b.request("clip_envelope_insert", path=args["path"],
                         parameter=args["parameter"], steps=args["steps"])
    if name == "live_clip_envelope_read":
        return b.request("clip_envelope_read", path=args["path"],
                         parameter=args["parameter"], times=args["times"])
    if name == "live_save_set":
        import render
        return render.save_set(b, **args)
    if name == "live_export":
        import render
        return render.export_set(b, args["output_path"],
                                 args.get("start_beats"), args.get("length_beats"),
                                 timeout=float(args.get("timeout", 240.0)))
    if name == "live_export_stems":
        import render
        kw = {}
        if "base_name" in args:
            kw["base_name"] = args["base_name"]
        if "timeout" in args:
            kw["timeout"] = float(args["timeout"])
        return render.export_stems(b, args["output_dir"],
                                   start_beats=args.get("start_beats"),
                                   length_beats=args.get("length_beats"), **kw)
    if name == "live_analyze_wav":
        try:
            from audio_analysis import analyze_wav
        except ImportError as exc:
            raise ValueError(f"live_analyze_wav requires numpy ({exc})")
        return analyze_wav(args["path"])
    if name == "live_cleanup_tracks":
        from api import Live
        return Live(b).cleanup_unused_tracks(dry_run=bool(args.get("dry_run", False)))
    if name == "live_record_takes":
        from api import Live
        return Live(b).record_takes(int(args["track"]), float(args["start_beats"]),
                                    float(args["length_beats"]),
                                    passes=int(args.get("passes", 2)))
    if name == "live_print_sequence":
        from api import Live
        return Live(b).print_sequence(
            int(args["source_track"]), float(args["length_beats"]),
            start_beats=float(args.get("start_beats", 0.0)),
            source_slot=(int(args["source_slot"]) if "source_slot" in args else None),
            name=args.get("name"))
    if name == "live_takes":
        from api import Live
        return Live(b).takes(int(args["track"]))
    if name == "live_choose_take":
        from api import Live
        return Live(b).choose_take(int(args["track"]), int(args["lane"]),
                                   int(args.get("clip", 0)))
    if name == "live_show_view":
        from api import Live
        Live(b).show_view(args["name"])
        return f"showing {args['name']}"
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
