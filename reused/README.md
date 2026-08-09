# reused/

Host-side utilities **copied from the MIT-licensed `ableton-live-mcp`** fork
(https://github.com/bschoepke/ableton-live-mcp, © 2026 Ableton Live MCP
contributors). Their MIT terms still apply; see the repo `NOTICE`.

These are staged here as reference and will be adapted into this project's `host/`
during the build (see `../PORTING.md`). Until a file is explicitly wired in and
tested against our core, do not assume it runs unchanged — several import the
fork's module layout.

- `audio_analysis.py` — BS.1770 LUFS / peak / band energy (numpy-only). Ready.
- `ableton_paths.py` — locate the Live install / User Library.
- `visual_capture.py`, `ocr.py` — Windows screen capture + OCR for UI verification.
- `install_remote_script.py` — installs a remote script into Live (retarget).
- `export_set.py`, `save_set.py` — drive Live's Export/Save via UI automation,
  including the focus-grab fix (`AttachThreadInput` + verify foreground).
