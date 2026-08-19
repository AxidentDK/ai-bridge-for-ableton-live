#!/usr/bin/env python3
"""Install (or update / remove) the AI Bridge remote script into Ableton Live.

Copies the ``remote_script/`` package into your Ableton **User Library** as
``Remote Scripts/AI_Bridge``. After running this, restart Live and enable
"AI Bridge" under Preferences -> Link, Tempo & MIDI -> Control Surface.

Usage::

    python install.py            # install or update
    python install.py --status   # show where it is / would go
    python install.py --uninstall
    python install.py --user-library "D:\\Custom\\User Library"   # override

No dependencies. Windows and macOS (Linux only if you pass --user-library).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path

CONTROL_SURFACE = "AI_Bridge"          # -> shows as "AI Bridge" in Live's dropdown
PACKAGE = "remote_script"
HERE = Path(__file__).resolve().parent

# Max for Live audio tap (Phase 4b). Live browses .amxd audio effects from this
# folder of the User Library. The device's [js] is NOT embedded in the .amxd, so
# the .js ships alongside it — Max resolves it from the patch's own folder.
M4L_SOURCE = "m4l"
M4L_DEVICE_SUBDIR = Path("Presets") / "Audio Effects" / "Max Audio Effect"
M4L_FILES = ("AgentAudioTap.amxd", "agent_audio_tap.js")


def user_library_candidates() -> list[Path]:
    home = Path.home()
    env = os.environ.get("ABLETON_USER_LIBRARY")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser())
    if platform.system() == "Darwin":
        candidates += [home / "Music" / "Ableton" / "User Library",
                       home / "Documents" / "Ableton" / "User Library"]
    else:  # Windows (and a sensible guess elsewhere)
        candidates += [home / "Documents" / "Ableton" / "User Library",
                       home / "Music" / "Ableton" / "User Library"]
    # de-dupe, keep order
    seen, out = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def resolve_user_library(override: str | None) -> Path:
    if override:
        p = Path(override).expanduser()
        if not p.is_dir():
            fail(f"--user-library {p} does not exist")
        return p
    for c in user_library_candidates():
        if c.is_dir():
            return c
    fail("Could not find your Ableton User Library. Pass --user-library <path>.\n"
         "Tried:\n  " + "\n  ".join(str(c) for c in user_library_candidates()))


def dest_dir(user_library: Path) -> Path:
    return user_library / "Remote Scripts" / CONTROL_SURFACE


def fail(msg: str):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


def do_install(dest: Path):
    src = HERE / PACKAGE
    if not (src / "__init__.py").is_file():
        fail(f"{src} is not the remote_script package (run install.py from the repo root)")
    action = "Updated" if dest.exists() else "Installed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir()
    n = 0
    for py in sorted(src.glob("*.py")):
        shutil.copy2(py, dest / py.name)
        n += 1
    print(f"{action} AI Bridge -> {dest}  ({n} files)")
    print()
    print("Next:")
    print("  1. Restart Ableton Live (it scans Remote Scripts at startup).")
    print("  2. Preferences -> Link, Tempo & MIDI -> Control Surface: pick "
          '"AI Bridge" in an empty slot (Input/Output = None).')
    print("  3. The bridge then listens on 127.0.0.1:8766 whenever Live runs.")


def m4l_dest(user_library: Path) -> Path:
    return user_library / M4L_DEVICE_SUBDIR


def tap_state_dir() -> Path:
    """Absolute folder for the tap's command/status files (mirrors host/tap.py)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return base / "AI-Bridge" / "tap"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AI-Bridge" / "tap"
    return Path.home() / ".ai-bridge" / "tap"


_JS_DEFAULT_MARKER = '"agent_audio_tap_command.json"'

# `js agent_audio_tap.js <command-file>` inside the .amxd's patcher JSON. That
# second argument is what the device ACTUALLY uses (it overrides the .js
# default), so retargeting it is what decouples us.
_JS_ARG_RE = re.compile(r'(js\s+agent_audio_tap\.js)(\s+[^"]*)?')


def retarget_amxd(data: bytes, command_file: Path) -> tuple[bytes, str | None]:
    """Point the built device at OUR command file, in place.

    The .amxd is a chunked container — 4-byte id, 4-byte little-endian length,
    body — with the patcher JSON in the ``ptch`` chunk. The upstream build
    hardcodes the fork's own folder (``~/.ableton-live-mcp/``), which would make
    this project depend on another one's private directory at runtime.

    A targeted string substitution inside that chunk (plus a corrected chunk
    length) is deliberately used instead of re-serializing the JSON: Max wrote
    that formatting and there is no reason to hand it back something different.
    Returns ``(new_bytes, previous_path)``.
    """
    target = str(command_file).replace("\\", "/")
    out = bytearray()
    previous: str | None = None
    off = 0
    while off + 8 <= len(data):
        cid = data[off:off + 4]
        length = int.from_bytes(data[off + 4:off + 8], "little")
        body = data[off + 8:off + 8 + length]
        if cid == b"ptch":
            text = body.decode("utf-8", "replace")
            match = _JS_ARG_RE.search(text)
            if match:
                previous = (match.group(2) or "").strip() or None
                if previous != target:
                    text = text[:match.start()] + f"js agent_audio_tap.js {target}" \
                        + text[match.end():]
                    body = text.encode("utf-8")
        out += cid + len(body).to_bytes(4, "little") + body
        off += 8 + length
    return bytes(out), previous


def _inject_command_path(js_text: str, command_file: Path) -> str:
    """Point the device's [js] at an ABSOLUTE command file.

    The device ships with a relative default, which Max's ``File`` cannot
    resolve for writing — the device then publishes no status at all and looks
    dead (field-hit 2026-08-13). Forward slashes: Max treats ``\\`` as an escape
    even on Windows.
    """
    literal = json.dumps(str(command_file).replace("\\", "/"))
    if _JS_DEFAULT_MARKER not in js_text:
        return js_text  # upstream changed the default; leave it alone
    return js_text.replace(_JS_DEFAULT_MARKER, literal, 1)


def do_install_m4l(user_library: Path):
    """Install the audio-tap device so Live's browser can find it.

    Requires Max for Live (Live Suite, or Standard + Max). Installing it
    without Max is harmless — the .amxd simply never loads.
    """
    src = HERE / M4L_SOURCE
    dest = m4l_dest(user_library)
    missing = [n for n in M4L_FILES if not (src / n).is_file()]
    if missing:
        # NOT AN ERROR, and it must not read like one. The BUILT .amxd is deliberately
        # not published: Max bakes an absolute path into it, so a released copy would
        # carry the builder's own home directory. Everyone who installs from a download
        # therefore lands here, and the honest thing is to say what it costs — three
        # tools out of 62 — rather than print a filename and leave them guessing.
        print("Max for Live audio tap: not installed (the built device is not shipped).")
        print("  Everything else works. This only affects live_tap_capture, "
              "live_tap_status and live_tap_discover —")
        print("  recording audio from a point INSIDE the device chain. Rendering the "
              "whole set (live_export)")
        print("  and stems (live_export_stems) do not need it.")
        print(f"  To build it yourself you need Max: open {src / 'AgentAudioTap.maxpat'}"
              " and freeze it as AgentAudioTap.amxd, then run this installer again.")
        return
    dest.mkdir(parents=True, exist_ok=True)
    state = tap_state_dir()
    state.mkdir(parents=True, exist_ok=True)
    command_file = state / "agent_audio_tap_command.json"

    retargeted_from = None
    for name in M4L_FILES:
        if name.endswith(".js"):
            text = (src / name).read_text(encoding="utf-8")
            (dest / name).write_text(_inject_command_path(text, command_file),
                                     encoding="utf-8")
        elif name.endswith(".amxd"):
            data, previous = retarget_amxd((src / name).read_bytes(), command_file)
            (dest / name).write_bytes(data)
            if previous and previous != str(command_file).replace("\\", "/"):
                retargeted_from = previous
        else:
            shutil.copy2(src / name, dest / name)
    print(f"Installed AgentAudioTap -> {dest}  ({len(M4L_FILES)} files)")
    print(f"  tap command/status files: {state}")
    if retargeted_from:
        print(f"  retargeted device away from: {retargeted_from}")
    print("  (Max for Live device — browse: Max for Live > Max Audio Effect)")


def do_uninstall_m4l(user_library: Path):
    dest = m4l_dest(user_library)
    removed = 0
    for name in M4L_FILES:
        target = dest / name
        if target.exists():
            target.unlink()
            removed += 1
    print(f"Removed {removed} Max for Live file(s) from {dest}" if removed
          else f"No Max for Live files to remove in {dest}")


def do_uninstall(dest: Path):
    if dest.exists():
        shutil.rmtree(dest)
        print(f"Removed {dest}")
        print("Restart Live and clear the control-surface slot if it was selected.")
    else:
        print(f"Nothing to remove ({dest} does not exist)")


def do_status(user_library: Path, dest: Path):
    print(f"User Library : {user_library}")
    print(f"Install path : {dest}")
    if dest.exists():
        files = sorted(p.name for p in dest.glob('*.py'))
        print(f"Installed    : yes ({len(files)} files: {', '.join(files)})")
    else:
        print("Installed    : no")
    md = m4l_dest(user_library)
    present = [n for n in M4L_FILES if (md / n).is_file()]
    print(f"M4L device   : {'yes' if len(present) == len(M4L_FILES) else 'no'} "
          f"({md})")


def main():
    ap = argparse.ArgumentParser(description="Install the AI Bridge remote script into Ableton Live.")
    ap.add_argument("--uninstall", action="store_true", help="remove the installed remote script")
    ap.add_argument("--status", action="store_true", help="show install location and state")
    ap.add_argument("--user-library", metavar="PATH", help="override the Ableton User Library path")
    ap.add_argument("--no-m4l", action="store_true",
                    help="skip the Max for Live audio-tap device")
    args = ap.parse_args()

    user_library = resolve_user_library(args.user_library)
    dest = dest_dir(user_library)

    if args.status:
        do_status(user_library, dest)
    elif args.uninstall:
        do_uninstall(dest)
        if not args.no_m4l:
            do_uninstall_m4l(user_library)
    else:
        do_install(dest)
        if not args.no_m4l:
            print()
            do_install_m4l(user_library)


if __name__ == "__main__":
    main()
