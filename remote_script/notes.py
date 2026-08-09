"""Clip-note access — the one convenience that must live inside Live.

Live's modern note API deals in ``MidiNoteSpecification`` objects and returns
``MidiNote`` objects; neither is expressible through the generic ``call``
primitive's JSON arguments. These two functions bridge that gap — and nothing
else. All other conveniences compose the generic primitives host-side.

Pure logic: the ``spec_factory`` (``Live.Clip.MidiNoteSpecification`` in
production) is injected via the context, so this module has no ``Live`` import
and is unit-testable with fakes.
"""
from __future__ import annotations

from .lom import LomError, resolve

# sensible whole-clip defaults for get: all pitches, a very long time span
_ALL_PITCH_FROM = 0
_ALL_PITCH_SPAN = 128
_ALL_TIME_FROM = 0.0
_ALL_TIME_SPAN = 1_000_000.0


def _require_clip(roots, path):
    obj = resolve(roots, path)
    if not hasattr(obj, "get_notes_extended") or not hasattr(obj, "add_new_notes"):
        raise LomError("wrong_type", f"{type(obj).__name__} at {path!r} is not a MIDI clip")
    return obj


def get_notes(roots: dict, path: str, from_time=None, time_span=None,
              from_pitch=None, pitch_span=None):
    """Read notes from a MIDI clip as plain dicts."""
    clip = _require_clip(roots, path)
    vector = clip.get_notes_extended(
        int(_ALL_PITCH_FROM if from_pitch is None else from_pitch),
        int(_ALL_PITCH_SPAN if pitch_span is None else pitch_span),
        float(_ALL_TIME_FROM if from_time is None else from_time),
        float(_ALL_TIME_SPAN if time_span is None else time_span),
    )
    out = []
    for n in vector:
        note = {
            "pitch": int(n.pitch),
            "start_time": float(n.start_time),
            "duration": float(n.duration),
            "velocity": float(n.velocity),
            "mute": bool(n.mute),
        }
        note_id = getattr(n, "note_id", None)
        if note_id is not None:
            note["note_id"] = int(note_id)
        probability = getattr(n, "probability", None)
        if probability is not None:
            note["probability"] = float(probability)
        out.append(note)
    out.sort(key=lambda d: (d["start_time"], d["pitch"]))
    return out


def add_notes(roots: dict, path: str, notes: list, spec_factory) -> int:
    """Add notes (list of dicts) to a MIDI clip. Returns the count added."""
    clip = _require_clip(roots, path)
    if not isinstance(notes, list) or not notes:
        raise LomError("bad_request", "notes must be a non-empty list")
    specs = []
    for i, d in enumerate(notes):
        try:
            spec = spec_factory(
                pitch=int(d["pitch"]),
                start_time=float(d["start_time"]),
                duration=float(d["duration"]),
                velocity=float(d.get("velocity", 100)),
                mute=bool(d.get("mute", False)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LomError("bad_request", f"invalid note at index {i}: {exc}")
        specs.append(spec)
    clip.add_new_notes(tuple(specs))
    return len(specs)


def remove_notes(roots: dict, path: str, from_time=None, time_span=None,
                 from_pitch=None, pitch_span=None) -> bool:
    """Remove notes in a window (defaults: the whole clip)."""
    clip = _require_clip(roots, path)
    if not hasattr(clip, "remove_notes_extended"):
        raise LomError("wrong_type", f"clip at {path!r} cannot remove notes")
    clip.remove_notes_extended(
        int(_ALL_PITCH_FROM if from_pitch is None else from_pitch),
        int(_ALL_PITCH_SPAN if pitch_span is None else pitch_span),
        float(_ALL_TIME_FROM if from_time is None else from_time),
        float(_ALL_TIME_SPAN if time_span is None else time_span),
    )
    return True
