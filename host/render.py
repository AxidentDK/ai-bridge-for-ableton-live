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


def export_stems(
    bridge,
    output_dir: str,
    base_name: str = "stems",
    start_beats: float | None = None,
    length_beats: float | None = None,
    timeout: float = 600.0,
    dialog_delay: float = 1.5,
) -> dict[str, Any]:
    """Render EVERY audio-producing track to its own aligned WAV (stems).

    The studio-interchange move: hand the results to any other DAW. Drives the
    export dialog with 'Rendered Track: All Individual Tracks' — one offline
    pass, all files starting at the same point with the same length. MIDI
    tracks without an instrument produce no stem (they make no audio).

    ``base_name`` is a PREFIX: Live writes '<base_name> <track name>.wav' per
    track plus '<base_name>.wav' for the main mix (verified on Live 12.4).
    Every track gets a stem — including empty ones and the return tracks.
    ``output_dir`` must already exist and hold no .wav files.
    """
    if not winui.IS_WINDOWS:
        raise ExportError("export_stems is Windows-only for now")
    output_dir = str(output_dir)
    if not os.path.isdir(output_dir):
        raise ExportError("output_dir %r does not exist" % output_dir)
    if any(name.lower().endswith(".wav") for name in os.listdir(output_dir)):
        raise ExportError(
            "output_dir %r already contains .wav files — use an empty folder so "
            "the rendered stems are unambiguous" % output_dir)
    if (start_beats is None) != (length_beats is None):
        raise ExportError("pass start_beats and length_beats together, or neither")

    expected_s = None
    if start_beats is not None:
        bridge.call("live_set", "stop_playing")
        bridge.set("live_set", "loop_start", float(start_beats))
        bridge.set("live_set", "loop_length", float(length_beats))
        bridge.set("live_set", "loop", True)
        tempo = float(bridge.get("live_set", "tempo"))
        expected_s = float(length_beats) * 60.0 / tempo

    _drive_export_dialog(os.path.join(output_dir, base_name), dialog_delay,
                         rendered_track=RENDERED_ALL_INDIVIDUAL)

    deadline = time.monotonic() + max(30.0, float(timeout))
    stable_snapshot: dict = {}
    stable_since = None
    while time.monotonic() < deadline:
        snapshot = {}
        for name in os.listdir(output_dir):
            if name.lower().endswith(".wav"):
                path = os.path.join(output_dir, name)
                try:
                    snapshot[name] = os.path.getsize(path)
                except OSError:
                    snapshot[name] = -1
        if snapshot and snapshot == stable_snapshot:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 3.0:
                files = []
                for name in sorted(snapshot):
                    info = _read_wav_result(os.path.join(output_dir, name))
                    if info is None:
                        stable_since = None  # a header not finalized — keep waiting
                        break
                    info["file"] = name
                    del info["exported"]
                    files.append(info)
                else:
                    result = {"exported": True, "output_dir": output_dir,
                              "count": len(files), "files": files}
                    if expected_s is not None:
                        result["expected_duration_s"] = round(expected_s, 2)
                    return result
        else:
            stable_since = None
        stable_snapshot = snapshot
        time.sleep(0.5)
    return {
        "exported": False,
        "output_dir": output_dir,
        "error": "stems_not_observed",
        "hint": (
            "Drove the stems export but no stable set of WAV files appeared "
            "within %.0fs. A dialog may be stuck open (check the Live window), "
            "or a long render is still running — check the folder before "
            "retrying." % float(timeout)),
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


EXPORT_DIALOG_TITLE = "Export Audio/Video"
SAVE_DIALOG_TITLE = "Save Audio File As:"
# 'Rendered Track' dropdown, relative to the dialog window's top-left corner
# (measured on Live 12.4, default UI zoom)
_RENDERED_TRACK_OFFSET = (217, 75)
# 'Export' button: x from the left edge, y measured UP from the dialog's bottom
# (the dialog's height varies with the options shown, its footer does not)
_EXPORT_BUTTON_OFFSET = (117, 37)
RENDERED_MAIN = 0             # menu order: Main, All Individual Tracks, ...
RENDERED_ALL_INDIVIDUAL = 1


def _set_rendered_track(item_index: int, dialog_delay: float) -> None:
    """In the (already open) export dialog, pick the 'Rendered Track' mode.

    The dialog is a real titled window, so the dropdown is clicked at a
    window-relative offset. Menu selection is keyboard-only and anchored with
    HOME: the menu WRAPS AROUND, so repeated Up does NOT clamp to the first
    item (field-hit 2026-08-11: Up x10 landed on a track name and rendered the
    wrong thing). Home jumps to 'Main' unconditionally; Down x item_index then
    selects deterministically whatever was last used.
    """
    dlg = winui.find_window_by_title(EXPORT_DIALOG_TITLE)
    if not dlg:
        raise ExportError(
            "the '%s' dialog did not appear (is Live's window visible?)"
            % EXPORT_DIALOG_TITLE)
    left, top, _right, _bottom = winui.window_rect(dlg)
    winui.click_at(left + _RENDERED_TRACK_OFFSET[0], top + _RENDERED_TRACK_OFFSET[1])
    time.sleep(0.6)
    winui.send_keys(winui.chord(winui.VK_HOME))        # anchor on 'Main'
    time.sleep(0.25)
    for _ in range(item_index):
        winui.send_keys(winui.chord(winui.VK_DOWN))
        time.sleep(0.1)
    winui.send_keys(winui.chord(winui.VK_RETURN))
    time.sleep(0.4)


def _click_export_button(timeout: float = 8.0):
    """Press the dialog's Export button and wait for the SAVE dialog to appear.

    Success is the save dialog showing up — NOT the settings dialog closing:
    Live keeps that window alive (disabled) behind the save dialog, so
    "it's gone" never becomes true.

    The Export button sits in the dialog's bottom-LEFT, but its exact offset
    drifts with UI zoom / DPI / which options the dialog shows (field-hit
    2026-08-13: the single measured offset missed the button and no render ran,
    while the dialog stayed clean). So we click a short sweep of bottom-left
    candidates, re-checking for the save dialog after each — the save dialog
    appearing is the only trustworthy success signal. Every candidate keeps the
    same small x (the Export button's column) so a stray click can never reach
    the Cancel button to its right and abort the export.
    """
    dlg = winui.find_window_by_title(EXPORT_DIALOG_TITLE)
    if not dlg:
        raise ExportError("the export dialog vanished before Export was pressed")
    # (x from left edge, y UP from the dialog bottom); most-likely first.
    candidates = [
        _EXPORT_BUTTON_OFFSET,   # the historically-measured spot
        (_EXPORT_BUTTON_OFFSET[0], 22),
        (_EXPORT_BUTTON_OFFSET[0], 30),
        (_EXPORT_BUTTON_OFFSET[0] - 10, 24),
        (_EXPORT_BUTTON_OFFSET[0] + 10, 24),
    ]
    for i, (dx, dy) in enumerate(candidates):
        dlg = winui.find_window_by_title(EXPORT_DIALOG_TITLE)
        if not dlg:
            # A prior click landed on something that dismissed the dialog. If the
            # save dialog is up, great; otherwise the export was aborted.
            if winui.find_window_by_title(SAVE_DIALOG_TITLE):
                return winui.find_window_by_title(SAVE_DIALOG_TITLE)
            raise ExportError(
                "the export settings dialog closed without a save dialog "
                "appearing — a click likely missed the Export button; check "
                "the Live window")
        left, _top, _right, bottom = winui.window_rect(dlg)
        winui.click_at(left + dx, bottom - dy)
        wait = timeout if i == 0 else 3.0
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            save = winui.find_window_by_title(SAVE_DIALOG_TITLE)
            if save:
                return save
            time.sleep(0.25)
    raise ExportError(
        "clicked the Export button column but the '%s' dialog never appeared "
        "after several attempts — UI zoom may have shifted the button, or a "
        "dropdown is stuck open; check the Live window" % SAVE_DIALOG_TITLE)


def _drive_export_dialog(output_path: str, dialog_delay: float,
                         rendered_track: int = RENDERED_MAIN) -> None:
    if winui.IS_WINDOWS:
        hwnd = winui.find_live_window()
        if hwnd is None:
            raise ExportError("No Ableton Live main window found")
        winui.focus_window(hwnd)
        winui.send_keys(winui.chord(winui.VK_CONTROL, winui.VK_SHIFT, winui.VK_R))
        time.sleep(dialog_delay)                                  # export settings dialog
        # force the 'Rendered Track' mode — the dialog remembers the LAST-used
        # one, so relying on it silently renders the wrong thing (e.g. stems
        # after a stems export). Deterministic beats remembered.
        _set_rendered_track(rendered_track, dialog_delay)
        # CLICK 'Export' — do NOT press Enter here. After the dropdown was
        # clicked, keyboard focus stays on it and Enter RE-OPENS the menu; the
        # path typed next then acts as menu type-ahead and silently selects a
        # track whose name starts with the same letter (field-hit 2026-08-11:
        # 'C:\...' selected the track 'Chord' and nothing was ever rendered).
        _click_export_button()
        time.sleep(dialog_delay + 1.0)                            # let the native save dialog fully appear
        # The filename field opens pre-populated with the LAST-used name/folder;
        # select-all first so our full path REPLACES it (typing alone appends /
        # races the remembered value — a real failure mode on re-export).
        winui.send_keys(winui.chord(winui.VK_CONTROL, winui.VK_A))
        time.sleep(0.2)
        winui.type_text(output_path)                              # full path replaces the selection
        time.sleep(0.5)
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
