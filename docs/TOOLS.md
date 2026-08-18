# What the bridge can do — all 62 tools

Every tool below is reachable from any MCP client connected to the bridge. You do not
call them by name: you ask for what you want in plain language and the assistant picks.
This page is here so you can see what is *possible*, and so you know a thing exists before
you think to ask for it.

**Two of them are worth knowing about before you start**, because they do something you
would not guess an assistant could do:

- **`live_find_sound`** searches your library by how something SOUNDS — "a crisp clap", "a
  dark pad" — rather than by filename. It needs the listening sidecar installed; see the
  guide. Without it, search falls back to Live 12's own audio embeddings and says so.
- **`live_snapshot` / `live_morph`** capture every parameter of a device or rack and
  interpolate between two of them, which is how you get "halfway between these two
  patches".

A note on the generic four — `live_get`, `live_set`, `live_call` and `live_batch`. They
reach **any** property or function in Live's object model, not a curated list. So if
something is missing from this page but exists in Live's API, it can still be done; the
named tools are the ergonomic layer on top, not the limit.

### Connection & session

| Tool | What it does |
|---|---|
| `live_ping` | Is the bridge alive inside Live? |
| `live_summary` | Version, tempo, playing state, track list |
| `live_show_view` | Switch Live's main view |
| `live_dialog` | Is a modal dialog blocking everything, and what does it say |
| `live_undo` | Undo or redo the last action |
| `live_save_set` | Save the set (Ctrl+S, verified via the .als file) |
| `live_cleanup_tracks` | Delete unused tracks |

### Raw LOM (the generic proxy)

| Tool | What it does |
|---|---|
| `live_get` | Read any property of any LOM object |
| `live_set` | Write any writable property |
| `live_call` | Call any function on any LOM object |
| `live_batch` | Many LOM operations in ONE round-trip (max 500) |
| `live_children` | Introspect an object: type, properties, functions |
| `live_resolve` | Does this LOM path exist? |

### Clips & notes

| Tool | What it does |
|---|---|
| `live_clip_notes` | Read a clip's MIDI notes |
| `live_clip_add_notes` | Add MIDI notes to a clip |
| `live_clip_remove_notes` | Remove notes (whole clip, or a time/pitch window) |
| `live_edit_notes` | Change or delete SPECIFIC notes by id, leaving the rest alone |
| `live_transform_notes` | Musical surgery: transpose, quantise, humanise, invert… |
| `live_print_sequence` | Render what an arp/sequencer PLAYS into editable notes |
| `live_describe_clip` | Describe a MIDI clip musically: key, with a confidence |
| `live_describe_clips` | The same for every MIDI clip in the set |
| `live_clip_warp_markers` | Read or edit an audio clip's warp markers and warp mode |

### Automation & envelopes

| Tool | What it does |
|---|---|
| `live_clip_envelope` | Write parameter automation into a clip |
| `live_clip_envelope_read` | Sample an envelope's value at given beats |
| `live_envelope_curve` | Write a SHAPED automation sweep (not just steps) |
| `live_clip_velocity_envelope` | Drive a parameter from the clip's own note velocities |

### Devices & parameters

| Tool | What it does |
|---|---|
| `live_device_parameters` | Every parameter of a device, batched |
| `live_load_device` | Load a device onto a track by name |
| `live_plugin_parameters` | A VST/AU's FULL parameter list, incl. ones Live hides |
| `live_modulation_matrix` | Read a device's modulation matrix |
| `live_modulate` | Route a modulation source to a parameter |
| `live_snapshot` | Capture EVERY parameter of a device or rack |
| `live_snapshot_apply` | Recall a snapshot |
| `live_morph` | Interpolate between two snapshots and apply the blend |

### Browser & sound search

| Tool | What it does |
|---|---|
| `live_browse` | List/filter loadable items in a browser category |
| `live_browser_index` | Disk cache of the browser tree (search = a file read) |
| `live_preview` | Audition a browser item WITHOUT loading it |
| `live_find_sound` | Find a clip or one-shot BY MEANING (the listening sidecar) |
| `live_similar_sounds` | Similar sounds via Live 12's own audio embeddings |
| `live_sidecar_status` | Is the sidecar installed, and what has it analysed |
| `live_sound_index` | Status of Live's own audio-analysis database |

### Observation & metering

| Tool | What it does |
|---|---|
| `live_observe` | Subscribe to a property; Live pushes changes |
| `live_unobserve` | Cancel a subscription |
| `live_events` | Collect the change events that have arrived |
| `live_watch` | Subscribe to a BUNDLE of things worth noticing |
| `live_meters` | Read every track's output level |
| `live_meters_observed` | Peak-hold metering over a window |

### Scenes & routing

| Tool | What it does |
|---|---|
| `live_scenes` | List every scene: name, empty, triggered |
| `live_scene` | create / delete / duplicate / fire / rename / capture |
| `live_routing` | Read a track's routing, monitoring and arm state |
| `live_set_routing` | Set routing by the display name Live shows |

### Recording & comping

| Tool | What it does |
|---|---|
| `live_record_takes` | Comping: loop-record N passes over a section |
| `live_takes` | List a track's take lanes and take clips |
| `live_choose_take` | Promote a take onto the track's main lane |

### Audio out & measurement

| Tool | What it does |
|---|---|
| `live_export` | Render the arrangement to WAV via Live's export dialog |
| `live_export_stems` | EVERY audio track to its own aligned WAV, one offline pass |
| `live_analyze_wav` | Measure a WAV: LUFS (BS.1770-4), peak, more |
| `live_describe_audio` | A WAV's character: bright/dark, tonal/noisy, percussive |
| `live_tap_discover` | Find the AgentAudioTap M4L device's command/status files |
| `live_tap_status` | The tap's last reported state |
| `live_tap_capture` | Record audio AT THE TAP'S INSERTION POINT to a WAV |

### MIDI out

| Tool | What it does |
|---|---|
| `live_midi_cc` | Send a MIDI CC on a virtual port |
