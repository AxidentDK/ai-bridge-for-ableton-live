# Porting status

What's been copied in from the MIT `ableton-live-mcp` fork, and its state. The
fork stays intact and functional; these are **copies**, staged for adaptation to
this project's clean-room core as each build phase reaches them.

| Path | Origin (MIT) | Status | Wired in at |
|---|---|---|---|
| `m4l/` (AgentAudioTap, m4l host) | m4l/ | **Ready** — self-contained (socket/file comms) | Phase 4 |
| `reused/audio_analysis.py` | src/ | **Ready** — numpy-only, standalone | Phase 4 |
| `reused/ableton_paths.py` | src/ | **Ready** — path detection, standalone | Phase 0/1 |
| `reused/visual_capture.py`, `ocr.py` | src/ | **Ready-ish** — standalone capture utils | Phase 4 |
| `reused/install_remote_script.py` | src/ | **Adapt** — retarget to this repo's `remote_script/` | Phase 1 |
| `reused/export_set.py`, `save_set.py` | src/ | **Adapt** — UI automation; re-wire to new host | Phase 4 |
| `host/live_client.py` | examples/live.py | **Seed** — grows into the host client | Phase 1 |
| `tests/` | tests/ | **Adapt** — assert the fork's API; the fake-Live simulator (`test_remote_bridge_fake_live.py`) is the reusable jewel, the rest get retargeted | Phase 2+ |

**Rule:** nothing in `reused/` or `tests/` is assumed working against our core
until its phase explicitly wires and tests it. Treat as reference until then.
