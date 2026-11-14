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
    python install.py --claude-desktop       # connect the Claude desktop app
    python install.py --claude-desktop off   # disconnect it again

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
    print("  1. Restart Ableton Live (it only looks for new control surfaces when it starts).")
    print("  2. Preferences -> Link, Tempo & MIDI -> Control Surface: pick "
          '"AI Bridge" in an empty slot (Input/Output = None).')
    print("  3. That's it. The bridge is active whenever Live is running, and only "
          "programs on this computer can reach it.")


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


# --- Claude Desktop -------------------------------------------------------------------
# Claude Desktop starts MCP servers listed in its own settings file. Everyone who uses
# Claude instead of Gemini needs the bridge in that list, and hand-editing JSON is not
# something a setup program may ask of anyone. The rule for the file: add or update our
# one entry, keep everything else exactly as it was, and never write into a file we
# could not read — a broken settings file stays the user's, untouched.

CLAUDE_SERVER = "ai-bridge"
CLAUDE_BACKUP = "claude_desktop_config.before-ai-bridge.json"
CLAUDE_PACKAGE = "Claude_pzs8sxrjxfjjc"     # the Microsoft Store / MSIX build


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def claude_desktop_dir() -> Path:
    """Windows: today's Claude keeps its data in %LOCALAPPDATA%\\Claude-Data\\<install>\\,
    one folder per kind of install; the one in use holds Claude's own config.json, newest
    written. Older installs use %APPDATA%\\Claude. Measured 2026-11-14: settings written
    to the old place are not read by a Claude that has a Claude-Data folder."""
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude"
    if os.name == "nt":
        data = _local_appdata() / "Claude-Data"
        if data.is_dir():
            in_use = sorted((d / "config.json" for d in data.iterdir() if (d / "config.json").is_file()),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if in_use:
                return in_use[0].parent
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Claude"
    return Path.home() / ".config" / "Claude"


def claude_desktop_config() -> Path:
    return claude_desktop_dir() / "claude_desktop_config.json"


def claude_desktop_present() -> bool:
    """Same test as ClaudeDesktopFound in installer/ai-bridge.iss — keep them alike."""
    if claude_desktop_dir().is_dir():
        return True
    if platform.system() == "Darwin":
        return any(p.exists() for p in (Path("/Applications/Claude.app"),
                                        Path.home() / "Applications" / "Claude.app"))
    if os.name == "nt":
        local = _local_appdata()
        return any((local / name).is_dir() for name in
                   ("Claude-Data", "AnthropicClaude", "Packages\\" + CLAUDE_PACKAGE))
    return False


def claude_server_entry() -> dict:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":   # no console = no stdin/stdout for MCP
        exe = exe.with_name("python.exe")
    return {"command": str(exe), "args": [str(HERE / "host" / "mcp_server.py")]}


def _is_ours(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    args = entry.get("args") or []
    if not args or not isinstance(args[-1], str):
        return False
    try:
        return Path(args[-1]).resolve() == (HERE / "host" / "mcp_server.py").resolve()
    except OSError:
        return False


def _read_claude_config(path: Path):
    """The settings as a dict, {} when there are none yet, None when unreadable."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        return None
    return data


def _write_claude_config(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(CLAUDE_BACKUP)
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    # Rewritten in place, never swapped for another file: the file is Claude's.
    body = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    with open(path, "r+b" if path.exists() else "wb") as f:
        f.write(body)
        f.truncate()


def _ai_bridge_home() -> Path:
    home = os.environ.get("AI_BRIDGE_HOME")
    return Path(home) if home else Path.home() / ".ai-bridge"


def _connect_marker() -> Path:
    return _ai_bridge_home() / "claude_desktop_connected"


# Claude Desktop keeps its settings file in memory and writes its own copy back, for
# example just before it quits (measured 2026-11-14: main.log "Config file written",
# then "beforeQuit"; the file afterwards byte-identical to the one from before our
# entry). So a change made while it runs is made again by a hidden helper once it has
# quit. The wish lives in a file, so a later wish replaces an earlier one; on Windows
# RunOnce carries the helper over a shutdown that ends Claude Desktop and the helper.

RUN_ONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUN_ONCE_NAME = "AI Bridge - Claude Desktop"


def _wish_path() -> Path:
    return _ai_bridge_home() / "claude_desktop_wish"


def _windows_processes(exe_names: set[str]) -> list[tuple[int, str]]:
    """(pid, full image path, lower case) of the running processes with these file names."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    found = []
    snap = k32.CreateToolhelp32Snapshot(2, 0)           # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return found
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(Entry)
        more = k32.Process32FirstW(snap, ctypes.byref(e))
        while more:
            if e.szExeFile.lower() in exe_names:
                h = k32.OpenProcess(0x1000, False, e.th32ProcessID)   # QUERY_LIMITED_INFORMATION
                if h:
                    try:
                        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
                        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                            found.append((e.th32ProcessID, buf.value.lower()))
                    finally:
                        k32.CloseHandle(h)
            more = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return found


def _pgrep(pattern: str) -> set[int]:
    import subprocess
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout
    return {int(p) for p in out.split()}


def claude_desktop_pids() -> set[int]:
    """Claude Desktop's own processes. Claude Code's own claude.exe is not Claude Desktop."""
    try:
        if platform.system() == "Darwin":
            return _pgrep("/Claude.app/Contents/MacOS/Claude")
        if os.name == "nt":
            return {pid for pid, image in _windows_processes({"claude.exe"})
                    if "\\windowsapps\\claude_" in image or "\\anthropicclaude\\" in image}
    except Exception:                                    # noqa: BLE001
        pass
    return set()


def claude_desktop_running() -> bool:
    return bool(claude_desktop_pids())


def _our_server_running() -> bool:
    """This copy's bridge server is running: proof that the running Claude Desktop has
    read our entry (it starts the servers its settings list)."""
    try:
        if platform.system() == "Darwin":
            return bool(_pgrep(str(HERE / "host" / "mcp_server.py")))
        if os.name == "nt":
            ours = claude_server_entry()["command"].lower()
            return any(image == ours for _, image in _windows_processes({"python.exe"}))
    except Exception:                                    # noqa: BLE001
        pass
    return False


def _helper_command() -> list[str]:
    exe = Path(sys.executable)
    if os.name == "nt" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")               # no console window
    return [str(exe), str(HERE / "install.py"), "--claude-desktop", "wait"]


def _set_run_once(on: bool):
    if os.name != "nt":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_ONCE_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                cmd = " ".join(f'"{part}"' if " " in part else part for part in _helper_command())
                winreg.SetValueEx(key, RUN_ONCE_NAME, 0, winreg.REG_SZ, cmd)
            else:
                winreg.DeleteValue(key, RUN_ONCE_NAME)
    except OSError:
        pass


def _start_helper():
    import subprocess
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 cwd=str(HERE), close_fds=True)
    if os.name == "nt":
        subprocess.Popen(_helper_command(), creationflags=0x00000008 | 0x00000200, **quiet)
    else:
        subprocess.Popen(_helper_command(), start_new_session=True, **quiet)


def _repeat_after_quit(wish: str):
    _wish_path().parent.mkdir(parents=True, exist_ok=True)
    _wish_path().write_text(wish)
    _set_run_once(True)
    _start_helper()
    print("  Claude Desktop is running: the change is made again once it has quit.")


def _clear_wish():
    try:
        _wish_path().unlink()
    except FileNotFoundError:
        pass
    _set_run_once(False)


def _apply_connect(path: Path) -> int:
    data = _read_claude_config(path)
    if data is None:
        print(f"Claude Desktop: its settings file could not be read, so it was left as it was:\n"
              f"  {path}", file=sys.stderr)
        return 3
    servers = data.setdefault("mcpServers", {})
    want = claude_server_entry()
    have = servers.get(CLAUDE_SERVER)
    if have == want:
        print(f"Claude Desktop: already connected ({path})")
        return 0
    servers[CLAUDE_SERVER] = want
    _write_claude_config(path, data)
    print(f"Claude Desktop: connected ({path})")
    if have is not None and not _is_ours(have):
        print(f"  replaced an earlier '{CLAUDE_SERVER}' entry: {json.dumps(have)}")
    print("  Quit Claude Desktop completely and start it again to load the bridge.")
    return 0


def _apply_disconnect(path: Path) -> int:
    data = _read_claude_config(path)
    if not data or not _is_ours(data.get("mcpServers", {}).get(CLAUDE_SERVER)):
        return 0
    del data["mcpServers"][CLAUDE_SERVER]
    _write_claude_config(path, data)
    print(f"Claude Desktop: disconnected ({path})")
    return 0


def _mark_connected():
    _connect_marker().parent.mkdir(parents=True, exist_ok=True)
    _connect_marker().touch()


def connect_claude_desktop(mode: str = "on") -> int:
    """mode 'on': add or update our entry. 'auto' (the Mac app, every launch): connect
    once if Claude Desktop is there; after that only keep our own entry's path current,
    so someone who removed it is not overruled at every start. Connected counts only once
    the entry has outlived Claude Desktop's own save, so 'auto' tries again until then."""
    path = claude_desktop_config()
    if mode == "auto":
        if not claude_desktop_present():
            return 0
        if _connect_marker().exists():
            data = _read_claude_config(path) or {}
            have = data.get("mcpServers", {}).get(CLAUDE_SERVER)
            bundle_copy = isinstance(have, dict) and ".app/Contents/Resources/" in str(have.get("command", ""))
            if not bundle_copy or have == claude_server_entry():
                return 0
    code = _apply_connect(path)
    if code:
        return code
    if claude_desktop_running():
        _repeat_after_quit("on")
    else:
        _clear_wish()
        if mode == "auto":
            _mark_connected()
    return 0


def disconnect_claude_desktop(repeat: bool = True) -> int:
    """Remove our entry only — never one that points at another copy of the bridge."""
    code = _apply_disconnect(claude_desktop_config())
    if repeat and claude_desktop_running():
        _repeat_after_quit("off")
    else:
        _clear_wish()
    return code


SETTLE_SECONDS = 30.0
POLL_SECONDS = 0.2


def _read_wish() -> str:
    try:
        return _wish_path().read_text().strip()
    except OSError:
        return ""


def wait_for_claude_desktop() -> int:
    """The hidden helper. Waits until the Claude Desktop processes that may still save
    their own copy are gone, then makes the latest wish. A quick restart leaves no moment
    without Claude Desktop (measured 2026-11-14: the new one ran 2 s after the old one
    began to quit), so the old processes are followed by their ids. If a new Claude
    Desktop already runs at the write, it may have read the file before it: done once it
    shows it has our entry (it runs our server) - otherwise wait for it to quit as well."""
    import time
    _set_run_once(True)          # a shutdown ends this helper too; the next sign-in goes on
    may_save = claude_desktop_pids()
    code, wish = 0, ""
    while True:
        while may_save & claude_desktop_pids():
            time.sleep(POLL_SECONDS)
        wish = _read_wish()
        path = claude_desktop_config()
        code = (_apply_connect(path) if wish == "on" else
                _apply_disconnect(path) if wish == "off" else 0)
        newer = claude_desktop_pids()
        if not newer or wish not in ("on", "off") or code:
            break
        deadline = time.monotonic() + SETTLE_SECONDS
        while newer & claude_desktop_pids() and time.monotonic() < deadline:
            if wish == "on" and _our_server_running():
                break
            time.sleep(POLL_SECONDS)
        if not newer & claude_desktop_pids():
            may_save = newer                 # it quit within the wait: write again
            continue
        if _our_server_running() == (wish == "on"):
            break                            # the running Claude Desktop has what we want
        may_save = newer                     # it read the file before our write
    if wish == "on" and not code:
        _mark_connected()
    _clear_wish()
    return code


def main():
    ap = argparse.ArgumentParser(description="Install the AI Bridge remote script into Ableton Live.")
    ap.add_argument("--claude-desktop", nargs="?", const="on", choices=("on", "auto", "off", "wait"),
                    help="only connect (on, auto) or disconnect (off) the Claude desktop app")
    ap.add_argument("--uninstall", action="store_true", help="remove the installed remote script")
    ap.add_argument("--status", action="store_true", help="show install location and state")
    ap.add_argument("--user-library", metavar="PATH", help="override the Ableton User Library path")
    ap.add_argument("--no-m4l", action="store_true",
                    help="skip the Max for Live audio-tap device")
    args = ap.parse_args()

    if args.claude_desktop:
        sys.exit({"off": disconnect_claude_desktop, "wait": wait_for_claude_desktop}.get(
            args.claude_desktop, lambda: connect_claude_desktop(args.claude_desktop))())
    if args.uninstall:
        # Before the User Library lookup: that can fail, and the entry must go anyway. No
        # helper here: the program's files are about to go, and the uninstaller has asked
        # for Claude Desktop to be closed first.
        disconnect_claude_desktop(repeat=False)

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
