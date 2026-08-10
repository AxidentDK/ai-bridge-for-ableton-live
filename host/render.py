"""Beyond-LOM: save the set and render the arrangement to WAV.

Live's Object Model offers NO save API and NO export API — an agent can mutate
everything and persist nothing. These tools do it the only way possible: drive
the application itself (accelerator keystrokes + dialog walking), then verify
against ground truth (.als mtime for save; the rendered file for export).

Hard-won behaviors baked in:

- Foreground grab uses AttachThreadInput + verification (plain
  SetForegroundWindow silently loses to focus-stealing prevention — the
  keystroke then lands in another app).
- Export DURATION VERIFICATION: Live's export dialog silently renders a stray
  Arrangement time-selection instead of the loop brace (field-hit 2026-08-09:
  a leftover selection halved a render with no warning). When the caller gives
  a render range, the result is checked against the expected duration and a
  mismatch is flagged.
- Save verification tolerates "no change": Live skips the rewrite entirely
  when the set is not dirty, so an unchanged mtime is usually harmless.

Adapted from MIT-licensed ableton-live-mcp originals (see NOTICE).
"""
from __future__ import annotations

import os
import time
import wave
from typing import Any

try:
    from . import winui  # package import
except ImportError:  # flat script import (host/ on sys.path)
    import winui


class SaveSetError(RuntimeError):
    pass


class ExportError(RuntimeError):
    pass


# --- save ------------------------------------------------------------------------

def save_set(bridge, timeout: float = 10.0, poll_interval: float = 0.25) -> dict[str, Any]:
    """Ctrl+S to Live, verified via the .als file's mtime."""
    file_path = str(bridge.get("live_set", "file_path") or "")
    if not file_path:
        return {
            "saved": False,
            "file_path": "",
            "error": "unsaved_set",
            "hint": (
                "This set has never been saved (file_path is empty), so the save "
                "keystroke would open a Save As dialog the agent cannot drive. Do the "
                "first File > Save As manually; after that save_set works."
            ),
        }
    if not os.path.exists(file_path):
        raise SaveSetError(
            "file_path %r does not exist on disk; cannot verify a save against it" % file_path)

    mtime_before = os.path.getmtime(file_path)
    _send_save_keystroke()
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        mtime_now = os.path.getmtime(file_path)
        if mtime_now != mtime_before:
            return {"saved": True, "file_path": file_path,
                    "mtime_before": mtime_before, "mtime_after": mtime_now}
        time.sleep(poll_interval)
    return {
        "saved": False,
        "file_path": file_path,
        "error": "save_not_observed",
        "hint": (
            "Sent the save keystroke but the .als mtime did not change within %.1fs. "
            "Most often the set simply had no unsaved changes (Live skips the rewrite "
            "when not dirty — usually harmless). Otherwise a modal dialog may be open; "
            "check the Live window." % float(timeout)
        ),
    }


def _send_save_keystroke() -> None:
    if winui.IS_WINDOWS:
        hwnd = winui.find_live_window()
        if hwnd is None:
            raise SaveSetError("No Ableton Live main window found")
        winui.focus_window(hwnd)
        winui.send_keys(winui.chord(winui.VK_CONTROL, winui.VK_S))
    elif winui.IS_MACOS:
        winui.osascript(
            'tell application "System Events"\n'
            '  set liveProcs to (every process whose name is "Live")\n'
            '  if (count of liveProcs) is 0 then error "No running Live process"\n'
            '  set frontmost of item 1 of liveProcs to true\n'
            '  delay 0.15\n'
            '  keystroke "s" using command down\n'
            'end tell\n', "save keystroke")
    else:
        raise SaveSetError("save_set supports Windows and macOS only")


# --- export ----------------------------------------------------------------------

def export_set(
    bridge,
    output_path: str,
    start_beats: float | None = None,
    length_beats: float | None = None,
    timeout: float = 240.0,
    dialog_delay: float = 1.5,
) -> dict[str, Any]:
    """Render the arrangement to a WAV by driving Live's export dialog.

    Inherits Live's last-used export settings (rate/depth/file type — must be
    WAV). With a range given, sets the loop brace over it AND verifies the
    rendered duration against the expectation (a stray Arrangement selection
    overrides the brace silently — the classic half-length render trap).
    """
    if not output_path:
        raise ExportError("output_path is required (target .wav)")
    output_path = str(output_path)
    if not output_path.lower().endswith(".wav"):
        raise ExportError("output_path must end in .wav (Live's last-used file type must be WAV)")
    if os.path.exists(output_path):
        raise ExportError(
            "output_path %r already exists; refusing to overwrite (an overwrite "
            "prompt would stall the dialog walk)" % output_path)
    parent = os.path.dirname(output_path) or "."
    if not os.path.isdir(parent):
        raise ExportError("output directory %r does not exist" % parent)

    if (start_beats is None) != (length_beats is None):
        raise ExportError("pass start_beats and length_beats together, or neither (uses the current loop brace)")

    expected_s = None
    if start_beats is not None:
        bridge.call("live_set", "stop_playing")
        bridge.set("live_set", "loop_start", float(start_beats))
        bridge.set("live_set", "loop_length", float(length_beats))
        bridge.set("live_set", "loop", True)
        tempo = float(bridge.get("live_set", "tempo"))
        expected_s = float(length_beats) * 60.0 / tempo

    _drive_export_dialog(output_path, dialog_delay)

    deadline = time.monotonic() + max(10.0, float(timeout))
    last_size = -1
    stable_since = None
    while time.monotonic() < deadline:
        if os.path.exists(output_path):
            size = os.path.getsize(output_path)
            if size > 0 and size == last_size:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 1.5:
                    result = _read_wav_result(output_path)
                    if result is not None:
                        return _check_duration(result, expected_s)
                    stable_since = None  # header not finalized yet
            else:
                stable_since = None
            last_size = size
        time.sleep(0.5)
    return {
        "exported": False,
        "path": output_path,
        "error": "render_not_observed",
        "hint": (
            "Drove the export dialog but no finished WAV appeared within %.0fs. A "
            "dialog may be stuck open (check the Live window), the last-used file "
            "type may not be WAV, or the render is still running — check the file "
            "again before retrying (a retry re-opens dialogs)." % float(timeout)
        ),
    }


def _read_wav_result(path: str):
    try:
        with wave.open(path, "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            if frames > 0:
                return {
                    "exported": True,
                    "path": path,
                    "duration_s": round(frames / float(rate), 2),
                    "sample_rate": rate,
                    "channels": handle.getnchannels(),
                    "bit_depth": handle.getsampwidth() * 8,
                }
    except (wave.Error, EOFError):
        pass
    return None


def _check_duration(result: dict, expected_s: float | None) -> dict:
    """Flag the silent-selection-override trap instead of reporting a bad render as fine."""
    if expected_s is not None:
        result["expected_duration_s"] = round(expected_s, 2)
        deviation = abs(result["duration_s"] - expected_s) / max(expected_s, 0.001)
        if deviation > 0.05:
            result["warning"] = "duration_mismatch"
            result["hint"] = (
                "Rendered %.1fs but the requested range is %.1fs. Live's export "
                "silently uses an Arrangement time-selection when one exists — a "
                "leftover selection overrides the loop brace with no warning. Clear "
                "the selection in the Arrangement (click empty space) and re-export."
                % (result["duration_s"], expected_s)
            )
    return result


def _drive_export_dialog(output_path: str, dialog_delay: float) -> None:
    if winui.IS_WINDOWS:
        hwnd = winui.find_live_window()
        if hwnd is None:
            raise ExportError("No Ableton Live main window found")
        winui.focus_window(hwnd)
        winui.send_keys(winui.chord(winui.VK_CONTROL, winui.VK_SHIFT, winui.VK_R))
        time.sleep(dialog_delay)                                  # export settings dialog
        winui.send_keys(winui.chord(winui.VK_RETURN))             # confirm last-used settings
        time.sleep(dialog_delay)                                  # native save dialog
        winui.type_text(output_path)                              # full path into name field
        time.sleep(0.3)
        winui.send_keys(winui.chord(winui.VK_RETURN))             # start the render
    elif winui.IS_MACOS:
        escaped = output_path.replace("\\", "\\\\").replace('"', '\\"')
        winui.osascript(
            'tell application "System Events"\n'
            '  set liveProcs to (every process whose name is "Live")\n'
            '  if (count of liveProcs) is 0 then error "No running Live process"\n'
            '  set frontmost of item 1 of liveProcs to true\n'
            '  delay 0.2\n'
            '  keystroke "r" using {command down, shift down}\n'
            '  delay %f\n'
            '  keystroke return\n'
            '  delay %f\n'
            '  keystroke "g" using {command down, shift down}\n'
            '  delay 0.4\n'
            '  keystroke "%s"\n'
            '  keystroke return\n'
            '  delay 0.4\n'
            '  keystroke return\n'
            'end tell\n' % (dialog_delay, dialog_delay, escaped), "export dialog drive")
    else:
        raise ExportError("export_set supports Windows and macOS only")
