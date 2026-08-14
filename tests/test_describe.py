"""Tests for host/describe.py — the musical description logic.

Pure functions over plain note dicts: no Live, no bridge, no pytest.
The point of pinning these is that a wrong key or chord name is not an obvious
crash — it is a plausible-looking wrong answer, which is worse.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))

from describe import (  # noqa: E402
    describe_notes, detect_key, name_chord, note_name,
)


def N(pitch, start, duration=1.0):
    return {"pitch": pitch, "start_time": start, "duration": duration}


def test_note_names():
    assert note_name(60) == "C3"          # Ableton's convention
    assert note_name(61) == "C#3"
    assert note_name(48) == "C2"


def test_chord_naming():
    cases = {
        (60, 64, 67): "C", (60, 63, 67): "Cm", (60, 63, 66): "Cdim",
        (60, 64, 68): "Caug", (60, 65, 67): "Csus4", (60, 62, 67): "Csus2",
        (60, 64, 67, 71): "Cmaj7", (60, 64, 67, 70): "C7",
        (60, 63, 67, 70): "Cm7", (62, 65, 69): "Dm",
    }
    for stack, expected in cases.items():
        assert name_chord(list(stack)) == expected, f"{stack} -> {name_chord(list(stack))}"
    # inversions still name the same chord (root is found, not assumed lowest)
    assert name_chord([64, 67, 72]) == "C"
    # fewer than three pitches is not a chord
    assert name_chord([60, 64]) is None


def test_key_detection():
    # unambiguous F minor: tonic held longest, all notes in key
    fmin = [N(53, 0, 2), N(56, 2), N(60, 3), N(63, 4), N(56, 5), N(53, 6, 2)]
    key = detect_key(fmin)
    assert key["key"] == "F minor", key
    assert key["in_key_fraction"] == 1.0

    # a C major triad bed
    cmaj = [N(60, 0, 4), N(64, 0, 4), N(67, 0, 4), N(72, 4, 4), N(76, 4, 4), N(79, 4, 4)]
    assert detect_key(cmaj)["key"].startswith("C "), detect_key(cmaj)

    # empty input must not raise
    assert detect_key([])["key"] is None


def test_describe_texture_and_progression():
    prog = []
    for i, chord in enumerate([[60, 64, 67], [55, 59, 62], [57, 60, 64], [53, 57, 60]]):
        prog += [N(p, i * 4, 4) for p in chord]
    d = describe_notes(prog)
    assert d["texture"] == "chordal"
    assert d["max_polyphony"] == 3
    assert d["chords"] == ["C", "G", "Am", "F"], d["chords"]      # I - V - vi - IV
    assert d["key"]["key"] == "C major"

    # NB: step the TIME by position and the pitch by scale degree — reusing one
    # index for both makes notes share a start time, which reads as polyphony.
    line = [N(60 + p, i * 0.5, 0.5)
            for i, p in enumerate((0, 2, 4, 5, 7, 5, 4, 2))]
    m = describe_notes(line)
    assert m["texture"] == "monophonic"
    assert m["max_polyphony"] == 1
    assert m["melodic"]["stepwise_pct"] > 50      # a scale run is mostly steps

    assert describe_notes([])["empty"] is True


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
