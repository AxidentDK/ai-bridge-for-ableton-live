# remote_script/

**The heart — clean-room.** The Control Surface remote script that runs *inside*
Live's embedded Python. This is built fresh (not copied) to best-option design.

Responsibilities (the wire format is specified in `../docs/PROTOCOL.md`):
- Load as a Control Surface, hold the LOM root handle.
- Socket server (background thread) speaking length-framed JSON on 127.0.0.1.
- **Main-thread marshaling:** every LOM operation scheduled onto Live's own
  thread — never touch the API off-thread.
- **Generic proxy:** `get` / `set` / `call` / `observe` / `resolve` = full LOM.
- **Observer registry:** track every listener; guaranteed teardown on disconnect,
  object deletion, and shutdown (the memory-leak fix).
- **Lazy, path-based serialization** of Live objects.

_Empty for now — implemented in Phase 1–2._
