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


def apply_modifications(roots: dict, path: str, notes: list) -> int:
    """Modify SPECIFIC existing notes in place, by ``note_id``.

    ``add_notes``/``remove_notes`` work on windows, so changing three notes means
    rewriting everything around them — which also discards anything added since the
    read. Live's ``apply_note_modifications`` edits exactly the notes whose ids are
    given and leaves the rest alone.

    It wants MidiNoteSpecification-like objects carrying an existing ``note_id``,
    so each incoming dict is turned back into the note object Live handed out. Every
    field must be present: the caller merges omitted fields with the current values
    before sending, because Live replaces the whole note, not just the named fields.
    """
    clip = _require_clip(roots, path)
    if not hasattr(clip, "apply_note_modifications"):
        raise LomError("wrong_type",
                       f"clip at {path!r} does not support note modification "
                       "(Live 11+ MIDI clips only)")
    if not isinstance(notes, list) or not notes:
        raise LomError("bad_request", "notes must be a non-empty list")

    wanted = {}
    for i, d in enumerate(notes):
        try:
            wanted[int(d["note_id"])] = d
        except (KeyError, TypeError, ValueError):
            raise LomError("bad_request", f"note at index {i} has no usable note_id")

    # Mutate the vector Live handed us and give that SAME object back.
    #
    # apply_note_modifications binds to std::vector<TNoteInfo> and refuses both a
    # tuple and a plain Python list (verified against Live 12.4.3 — two separate
    # type errors). Only Live's own MidiNoteVector satisfies it, so the working
    # pattern is: get_notes_extended -> edit the note objects in place -> pass the
    # vector itself. Notes that were not asked about keep their current values, so
    # handing back the whole vector is still surgical: ids are preserved and
    # nothing is removed and re-added.
    existing = clip.get_notes_extended(
        int(_ALL_PITCH_FROM), int(_ALL_PITCH_SPAN),
        float(_ALL_TIME_FROM), float(_ALL_TIME_SPAN))
    changed = 0
    for note in existing:
        edit = wanted.get(getattr(note, "note_id", None))
        if edit is None:
            continue
        note.pitch = int(edit["pitch"])
        note.start_time = float(edit["start_time"])
        note.duration = float(edit["duration"])
        note.velocity = float(edit["velocity"])
        note.mute = bool(edit.get("mute", False))
        if hasattr(note, "probability"):
            note.probability = float(edit.get("probability", 1.0))
        changed += 1
    if not changed:
        raise LomError("bad_request", "none of the given note_ids exist in this clip")
    clip.apply_note_modifications(existing)
    return changed


def remove_by_id(roots: dict, path: str, note_ids: list) -> int:
    """Delete specific notes by id, leaving everything else untouched."""
    clip = _require_clip(roots, path)
    if not hasattr(clip, "remove_notes_by_id"):
        raise LomError("wrong_type", f"clip at {path!r} cannot remove notes by id")
    ids = [int(i) for i in (note_ids or [])]
    if not ids:
        raise LomError("bad_request", "note_ids must be a non-empty list")
    # Same vector-vs-tuple trap as apply_note_modifications; fall back to varargs
    # in case a Live version wants them spread instead.
    try:
        clip.remove_notes_by_id(ids)
    except Exception:
        clip.remove_notes_by_id(*ids)
    return len(ids)


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
