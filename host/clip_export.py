"""Save a clip as a file, and say where it went.

The simplest path from "the AI made a clip" to "it's in my project": save it where Live's
browser already looks, switch to Session View, and let the user drag it in. No
Arrangement-to-Session conversion — a file is something every producer already knows
how to use, in either view and in any DAW.

  MIDI clip   -> .mid (notes, tempo, time signature). Notes only: a .mid cannot carry
                 the instrument, and Live's own clip format (.alc) cannot be written
                 from outside Live.
  audio clip  -> a copy of its source file (the original sample, not the warped or
                 processed result).

Saved to  <User Library>\\AI Bridge\\Clips\\  — the User Library is always in Live's
browser, so the file shows up there, ready to drag.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
from pathlib import Path

PPQ = 480
SUBFOLDER = ("AI Bridge", "Clips")


# --------------------------------------------------------------------- locations

def user_library() -> Path:
    """Live's User Library: $ABLETON_USER_LIBRARY, else the standard places (same
    rules as install.py, so the clip lands where the Control Surface went)."""
    env = os.environ.get("ABLETON_USER_LIBRARY")
    if env:
        return Path(env)
    home = Path.home()
    for c in (home / "Documents" / "Ableton" / "User Library",
              home / "Music" / "Ableton" / "User Library"):
        if c.exists():
            return c
    raise FileNotFoundError("Could not find Live's User Library. Set ABLETON_USER_LIBRARY "
                            "to its path.")


def clips_folder() -> Path:
    folder = user_library().joinpath(*SUBFOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(text: str) -> str:
    return _UNSAFE.sub("", text).strip().rstrip(".") or "Clip"


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    """Never overwrite: a second save of the same clip becomes 'name (2).mid'."""
    p = folder / f"{stem}{ext}"
    n = 2
    while p.exists():
        p = folder / f"{stem} ({n}){ext}"
        n += 1
    return p


# ------------------------------------------------------------------- MIDI writing

def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def midi_bytes(notes: list[dict], tempo: float, length_beats: float = 0.0,
               numerator: int = 4, denominator: int = 4, name: str = "") -> bytes:
    """A Standard MIDI File, format 0, from Live note dicts (times in beats).

    Muted notes are left out — muted means "not played". Note-offs sort before note-ons
    on the same tick, or a repeated pitch would have its new note cut by the old one's
    off. The track ends at the clip length, so the loop length survives the trip.
    """
    events: list[tuple[int, int, bytes]] = []
    last = 0
    for n in notes:
        if n.get("mute"):
            continue
        pitch = max(0, min(127, int(n["pitch"])))
        vel = max(1, min(127, int(round(float(n.get("velocity", 100))))))
        on = max(0, int(round(float(n["start_time"]) * PPQ)))
        off = max(on + 1, int(round((float(n["start_time"]) + float(n["duration"])) * PPQ)))
        events.append((on, 1, bytes((0x90, pitch, vel))))
        events.append((off, 0, bytes((0x80, pitch, 0))))
        last = max(last, off)
    events.sort(key=lambda e: (e[0], e[1]))

    track = bytearray()
    if name:
        nb = name.encode("utf-8")[:127]
        track += b"\x00\xff\x03" + _vlq(len(nb)) + nb
    usec = int(round(60_000_000 / float(tempo)))
    track += b"\x00\xff\x51\x03" + usec.to_bytes(3, "big")
    dd = max(0, int(denominator).bit_length() - 1)          # MIDI stores log2(denominator)
    track += b"\x00\xff\x58\x04" + bytes((int(numerator), dd, 24, 8))
    t = 0
    for tick, _order, data in events:
        track += _vlq(tick - t) + data
        t = tick
    end = max(last, int(round(float(length_beats) * PPQ)))
    track += _vlq(end - t) + b"\xff\x2f\x00"

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ)
    return header + b"MTrk" + struct.pack(">I", len(track)) + bytes(track)


# ------------------------------------------------------------------------- saving

_TRACK_OF = re.compile(r"\btracks (\d+)\b")


def save_clip(bridge, clip_path: str, name: str | None = None,
              show_session: bool = True) -> dict:
    """Save the clip at ``clip_path`` into the User Library and report where it is."""
    # A Session clip is reached through its slot; an empty slot would otherwise fail
    # further down with an error about a missing property rather than a missing clip.
    if clip_path.endswith(" clip"):
        slot = clip_path[:-len(" clip")]
        if not bridge.get(slot, "has_clip"):
            raise ValueError(f"no clip at {clip_path} — the slot is empty")

    m = _TRACK_OF.search(clip_path)
    track = str(bridge.get(f"live_set tracks {m.group(1)}", "name") or "") if m else ""
    clip_name = str(bridge.get(clip_path, "name") or "")
    folder = clips_folder()
    parts = [safe_name(track)] if track else []
    if name or clip_name:
        parts.append(safe_name(name or clip_name))
    base = " - ".join(parts) or "Clip"

    if bool(bridge.get(clip_path, "is_audio_clip")):
        src = Path(str(bridge.get(clip_path, "file_path") or ""))
        if not src.is_file():
            raise FileNotFoundError(f"the audio clip's source file is missing: {src}")
        dest = unique_path(folder, base, src.suffix)
        shutil.copy2(src, dest)
        result = {"saved": str(dest), "kind": "audio", "source": str(src),
                  "note": "A copy of the ORIGINAL sample — warping, clip gain and effects "
                          "are not included."}
    else:
        tempo = float(bridge.get("live_set", "tempo"))
        num = int(bridge.get("live_set", "signature_numerator") or 4)
        den = int(bridge.get("live_set", "signature_denominator") or 4)
        notes = bridge.request("clip_get_notes", path=clip_path) or []
        length = float(bridge.get(clip_path, "length") or 0.0)
        dest = unique_path(folder, f"{base} - {round(tempo)} BPM", ".mid")
        dest.write_bytes(midi_bytes(notes, tempo, length, num, den, name or clip_name))
        played = sum(1 for n in notes if not n.get("mute"))
        result = {"saved": str(dest), "kind": "midi", "notes": played, "tempo": tempo,
                  "note": "Notes only — drop it on a MIDI track that has an instrument."}

    result["folder"] = str(folder)
    result["in_live"] = "Browser > User Library > AI Bridge > Clips — drag it into a track"
    result["view"] = None
    if show_session:
        from api import Live
        result["view"] = Live(bridge).show_view("Session")
    return result
