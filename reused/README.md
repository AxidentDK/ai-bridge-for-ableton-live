# reused/

Staging area for MIT-licensed code copied from the
[ableton-live-mcp](https://github.com/bschoepke/ableton-live-mcp) fork
(© 2026 Ableton Live MCP contributors — MIT; see the repo `NOTICE`), pending
adaptation into this project.

Most originals have already been ported (see `../PORTING.md`). What remains here
is staged for the **audio tap (Phase 4b / v1.1)**:

- `visual_capture.py` — Windows screen capture, for verifying Max for Live
  device UIs during the tap build.
- `ocr.py` — OCR over those captures.

The Max for Live devices themselves live in `../m4l/`. Until the tap phase wires
these in and tests them, treat them as reference, not working code.
