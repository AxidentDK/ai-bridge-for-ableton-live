"""Describe MIDI note data in musical terms — the symbolic half of a semantic index.

A browser or clip list indexed by filename is unsearchable once it gets big:
"Snare_047" and "Idea 12" say nothing about what they are. For AUDIO that needs a
model that hears the waveform. For MIDI it does not — the notes are right there,
and everything a musician would say about a phrase (its key, whether it is a
chord bed or a melodic line, how busy it is, where it sits) is computable.

Deliberately honest about its limits. This measures **structure**, not meaning: it
can say "F minor, chordal, sparse, low register", not "melancholy" or "sounds like
a Rhodes". Style and mood are not claimed.

Pure stdlib — no numpy, no Live. Unit-testable against plain note dicts.
"""
from __future__ import annotations

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Krumhansl-Schmuckler key profiles: how strongly each scale degree is weighted in
# a tonal piece. Correlating a duration-weighted pitch-class histogram against all
# 24 rotations is the standard, and it beats naive "count the accidentals" because
# it uses EMPHASIS — a passing F# doesn't outvote a tonic held for four bars.
_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

# interval set (relative to root) -> chord quality
_CHORDS = {
    (0, 4, 7): "", (0, 3, 7): "m", (0, 3, 6): "dim", (0, 4, 8): "aug",
    (0, 5, 7): "sus4", (0, 2, 7): "sus2",
    (0, 4, 7, 11): "maj7", (0, 4, 7, 10): "7", (0, 3, 7, 10): "m7",
    (0, 3, 6, 10): "m7b5", (0, 3, 6, 9): "dim7", (0, 4, 7, 9): "6",
    (0, 3, 7, 9): "m6", (0, 4, 7, 14 % 12): "add9",
}


def note_name(pitch: int) -> str:
    """MIDI pitch -> scientific pitch name (Ableton's convention: 60 = C3)."""
    return f"{NOTE_NAMES[int(pitch) % 12]}{int(pitch) // 12 - 2}"


def _correlate(hist: list[float], profile: tuple) -> float:
    n = len(profile)
    mh = sum(hist) / n
    mp = sum(profile) / n
    num = sum((hist[i] - mh) * (profile[i] - mp) for i in range(n))
    den = (sum((hist[i] - mh) ** 2 for i in range(n)) ** 0.5
           * sum((profile[i] - mp) ** 2 for i in range(n)) ** 0.5)
    return num / den if den else 0.0


def key_from_histogram(hist: list[float]) -> dict:
    """Best-fitting key for any 12-bin pitch-class histogram.

    Split out from ``detect_key`` so the SAME estimator serves MIDI (bins
    weighted by note duration) and audio (bins weighted by chroma energy) —
    the profiles don't care where the weights came from.
    """
    if not hist or not any(hist):
        return {"key": None}
    scored = []
    for root in range(12):
        rotated = list(hist[root:]) + list(hist[:root])
        scored.append((_correlate(rotated, _MAJOR), root, "major"))
        scored.append((_correlate(rotated, _MINOR), root, "minor"))
    scored.sort(reverse=True)
    score, root, mode = scored[0]
    runner = scored[1]
    scale = _MAJOR_SCALE if mode == "major" else _MINOR_SCALE
    allowed = {(root + s) % 12 for s in scale}
    total = sum(hist)
    in_key = sum(h for pc, h in enumerate(hist) if pc in allowed)
    return {
        "key": f"{NOTE_NAMES[root]} {mode}",
        "confidence": round(float(score), 3),
        "runner_up": f"{NOTE_NAMES[runner[1]]} {runner[2]}",
        "margin": round(float(score - runner[0]), 3),
        "in_key_fraction": round(in_key / total, 3) if total else 0.0,
        "ambiguous": bool(score - runner[0] < 0.05),
    }


def detect_key(notes: list[dict]) -> dict:
    """Best-fitting key, with the runner-up and a confidence gap.

    The gap matters: a phrase that fits C major and A minor almost equally is
    genuinely ambiguous (they share every note), and saying so is more useful
    than picking one and sounding certain.
    """
    hist = [0.0] * 12
    for n in notes:
        hist[int(n["pitch"]) % 12] += max(float(n.get("duration", 0.25)), 0.01)
    return key_from_histogram(hist)


def name_chord(pitches: list[int]) -> str | None:
    """Name a simultaneous stack, trying each member as the root."""
    pcs = sorted({int(p) % 12 for p in pitches})
    if len(pcs) < 3:
        return None
    for root in pcs:
        intervals = tuple(sorted((pc - root) % 12 for pc in pcs))
        if intervals in _CHORDS:
            return f"{NOTE_NAMES[root]}{_CHORDS[intervals]}"
    return None


def _stacks(notes: list[dict], tolerance: float = 0.05) -> list[list[dict]]:
    """Group notes that start together (within a tolerance) — i.e. chords."""
    out: list[list[dict]] = []
    for n in sorted(notes, key=lambda x: x["start_time"]):
        if out and abs(n["start_time"] - out[-1][0]["start_time"]) <= tolerance:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def describe_notes(notes: list[dict], tempo: float = 120.0) -> dict:
    """Musical description of a set of notes. Structure, not mood."""
    if not notes:
        return {"empty": True, "summary": "empty clip — no notes"}

    pitches = [int(n["pitch"]) for n in notes]
    starts = [float(n["start_time"]) for n in notes]
    ends = [float(n["start_time"]) + float(n.get("duration", 0)) for n in notes]
    span = max(ends) - min(starts) or 1.0

    key = detect_key(notes)
    groups = _stacks(notes)
    sizes = [len(g) for g in groups]
    max_poly = max(sizes)
    avg_poly = sum(sizes) / len(sizes)

    chords = []
    for g in groups:
        if len(g) >= 3:
            named = name_chord([n["pitch"] for n in g])
            if named and (not chords or chords[-1] != named):
                chords.append(named)

    # Melodic contour, only meaningful for a mostly single-voice line: the
    # step/leap ratio is what makes a line sound melodic rather than scalar.
    top = [max(g, key=lambda n: n["pitch"]) for g in groups]
    intervals = [abs(top[i + 1]["pitch"] - top[i]["pitch"]) for i in range(len(top) - 1)]
    moving = [i for i in intervals if i > 0]
    leaps = [i for i in moving if i > 2]

    durations = sorted({round(float(n.get("duration", 0)), 3) for n in notes})
    density = len(notes) / (span / 4.0) if span else 0.0   # notes per bar (4/4)

    texture = ("monophonic" if max_poly == 1
               else "chordal" if avg_poly >= 2.5
               else "polyphonic")
    register = ("bass" if sum(pitches) / len(pitches) < 48
                else "low" if sum(pitches) / len(pitches) < 60
                else "mid" if sum(pitches) / len(pitches) < 72 else "high")
    busy = ("sparse" if density < 4 else "moderate" if density < 12 else "busy")

    parts = [key.get("key") or "no clear key"]
    if key.get("ambiguous"):
        parts[0] += f" (or {key['runner_up']})"
    parts += [texture, busy, f"{register} register"]
    if chords:
        parts.append("chords: " + " ".join(chords[:8]))
    if moving and len(moving) >= 3:
        parts.append(f"{round(100 * len(leaps) / len(moving))}% leaps")

    return {
        "notes": len(notes),
        "length_beats": round(span, 3),
        "length_bars": round(span / 4.0, 2),
        "key": key,
        "texture": texture,
        "max_polyphony": max_poly,
        "avg_polyphony": round(avg_poly, 2),
        "chords": chords,
        "pitch_range": {"low": note_name(min(pitches)), "high": note_name(max(pitches)),
                        "octaves": round((max(pitches) - min(pitches)) / 12.0, 1)},
        "register": register,
        "density_notes_per_bar": round(density, 2),
        "activity": busy,
        "rhythm": {"distinct_durations": len(durations),
                   "shortest": durations[0], "longest": durations[-1]},
        "melodic": ({"stepwise_pct": round(100 * (len(moving) - len(leaps)) / len(moving)),
                     "leap_pct": round(100 * len(leaps) / len(moving))}
                    if moving else None),
        "summary": ", ".join(parts),
        "note": ("Describes musical STRUCTURE measured from the notes — key, texture, "
                 "density, register. It does not claim style or mood, which are not "
                 "computable from note data."),
    }
