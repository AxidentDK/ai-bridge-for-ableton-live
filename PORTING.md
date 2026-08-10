# Porting status

Provenance of code adapted from the MIT-licensed `ableton-live-mcp` fork (which
stays intact — see `NOTICE`). Ported code was reimplemented into this project's
clean-room structure and tested here; nothing from the fork runs against our
core unmodified.

## Ported / absorbed (done)

| From (MIT fork) | Landed as | Notes |
|---|---|---|
| `export_set.py`, `save_set.py` | `host/render.py` + `host/winui.py` | Upgraded: AttachThreadInput focus grab **with verification**, export duration check vs beats×tempo (the stray-selection trap), and driven via the generic primitives (no `eval`/`exec`). |
| `audio_analysis.py` | `host/audio_analysis.py` | Verbatim (numpy + stdlib); BS.1770-4 LUFS/peak/bands. |
| `ableton_paths.py` (User Library detection) | `install.py` | Reimplemented as the installer's path resolution. |
| `install_remote_script.py` | `install.py` | Clean cross-platform installer (install / update / status / uninstall). |
| fork test suite | own suite in `tests/` | Rewrote clean, pytest-free tests against our modules (framing, core, observers, notes, mcp, render). The fork's fake-Live test targeted the fork's server/tap, not our core — dropped. |

## Still staged for a later phase

| Path | For | Phase |
|---|---|---|
| `m4l/` (AgentAudioTap, m4l host) | real-time audio capture / listening | 4b (v1.1) |
| `reused/visual_capture.py`, `reused/ocr.py` | verifying M4L device UIs during the tap build | 4b (v1.1) |

**Rule:** nothing under `reused/` is assumed working against our core until its
phase explicitly wires and tests it.
