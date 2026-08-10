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
import os
import platform
import shutil
import sys
from pathlib import Path

CONTROL_SURFACE = "AI_Bridge"          # -> shows as "AI Bridge" in Live's dropdown
PACKAGE = "remote_script"
HERE = Path(__file__).resolve().parent


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


def main():
    ap = argparse.ArgumentParser(description="Install the AI Bridge remote script into Ableton Live.")
    ap.add_argument("--uninstall", action="store_true", help="remove the installed remote script")
    ap.add_argument("--status", action="store_true", help="show install location and state")
    ap.add_argument("--user-library", metavar="PATH", help="override the Ableton User Library path")
    args = ap.parse_args()

    user_library = resolve_user_library(args.user_library)
    dest = dest_dir(user_library)

    if args.status:
        do_status(user_library, dest)
    elif args.uninstall:
        do_uninstall(dest)
    else:
        do_install(dest)


if __name__ == "__main__":
    main()
