"""Tests for host/clip_export.py — saving a clip as a file.

The .mid is parsed back rather than merely checked for existence: a MIDI file that
writes without error but has a wrong tempo or a swallowed note opens fine in every DAW
and is quietly wrong, which is the failure worth guarding against.
"""
import contextlib
import os
import shutil
import struct
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))

import clip_export  # noqa: E402


def parse_midi(data: bytes) -> dict:
    """Minimal format-0 reader: enough to prove what was written."""
    assert data[:4] == b"MThd"
    _len, fmt, ntrk, ppq = struct.unpack(">IHHH", data[4:14])
    assert data[14:18] == b"MTrk"
    (tlen,) = struct.unpack(">I", data[18:22])
    trk = data[22:22 + tlen]
    i, t, out = 0, 0, {"fmt": fmt, "ntrk": ntrk, "ppq": ppq, "events": [], "end": None}

    def vlq():
        nonlocal i
        v = 0
        while True:
            b = trk[i]; i += 1
            v = (v << 7) | (b & 0x7F)
            if not b & 0x80:
                return v
    while i < len(trk):
        t += vlq()
        st = trk[i]
        if st == 0xFF:
            typ = trk[i + 1]; i += 2
            ln = vlq(); body = trk[i:i + ln]; i += ln
            if typ == 0x51:
                out["tempo"] = 60_000_000 / int.from_bytes(body, "big")
            elif typ == 0x58:
                out["sig"] = (body[0], 2 ** body[1])
            elif typ == 0x03:
                out["name"] = body.decode()
            elif typ == 0x2F:
                out["end"] = t
        else:
            out["events"].append((t, st & 0xF0, trk[i + 1], trk[i + 2])); i += 3
    return out


N = lambda p, s, d, v=100, mute=False: {"pitch": p, "start_time": s, "duration": d,
                                        "velocity": v, "mute": mute}


def test_roundtrip_notes_tempo_signature_name():
    data = clip_export.midi_bytes([N(60, 0, 1), N(64, 1, 0.5, 90)], tempo=124,
                                  length_beats=4, numerator=3, denominator=4, name="Bass")
    m = parse_midi(data)
    assert (m["fmt"], m["ntrk"], m["ppq"]) == (0, 1, 480)
    assert abs(m["tempo"] - 124) < 0.01
    assert m["sig"] == (3, 4)
    assert m["name"] == "Bass"
    ons = [(t, p, v) for t, kind, p, v in m["events"] if kind == 0x90]
    assert ons == [(0, 60, 100), (480, 64, 90)]


def test_muted_notes_are_not_written():
    m = parse_midi(clip_export.midi_bytes([N(60, 0, 1), N(62, 1, 1, mute=True)], 120))
    assert [p for _t, k, p, _v in m["events"] if k == 0x90] == [60]


def test_repeated_pitch_off_comes_before_on():
    """Back-to-back same-pitch notes: the old note's off must precede the new note's
    on, or the second note is cut off the instant it starts."""
    m = parse_midi(clip_export.midi_bytes([N(60, 0, 1), N(60, 1, 1)], 120))
    at_480 = [k for t, k, _p, _v in m["events"] if t == 480]
    assert at_480 == [0x80, 0x90], at_480


def test_track_ends_at_clip_length_not_last_note():
    """A 4-bar loop with one note in bar 1 must still be 4 bars long elsewhere."""
    m = parse_midi(clip_export.midi_bytes([N(60, 0, 1)], 120, length_beats=16))
    assert m["end"] == 16 * 480


def test_safe_name_and_no_overwrite():
    assert clip_export.safe_name('Bass: "verse"/1?') == "Bass verse1"
    tmp = Path(tempfile.mkdtemp())
    try:
        a = clip_export.unique_path(tmp, "x", ".mid"); a.write_bytes(b"1")
        b = clip_export.unique_path(tmp, "x", ".mid")
        assert (a.name, b.name) == ("x.mid", "x (2).mid")
    finally:
        shutil.rmtree(tmp)


class FakeBridge:
    def __init__(self, audio_file=None):
        self.audio = audio_file
        self.props = {
            ("live_set tracks 1 clip_slots 0", "has_clip"): True,
            ("live_set tracks 1", "name"): "Bass",
            ("live_set tracks 1 clip_slots 0 clip", "name"): "Verse",
            ("live_set tracks 1 clip_slots 0 clip", "is_audio_clip"): audio_file is not None,
            ("live_set tracks 1 clip_slots 0 clip", "length"): 8.0,
            ("live_set tracks 1 clip_slots 0 clip", "file_path"): str(audio_file or ""),
            ("live_set", "tempo"): 124.0,
            ("live_set", "signature_numerator"): 4,
            ("live_set", "signature_denominator"): 4,
        }

    def get(self, path, prop):
        return self.props[(path, prop)]

    def request(self, op, **kw):
        assert op == "clip_get_notes"
        return [N(36, 0, 0.5), N(38, 1, 0.5), N(40, 2, 0.5, mute=True)]


@contextlib.contextmanager
def library():
    tmp = tempfile.mkdtemp()
    saved = os.environ.get("ABLETON_USER_LIBRARY")
    os.environ["ABLETON_USER_LIBRARY"] = tmp
    try:
        yield Path(tmp)
    finally:
        if saved is None:
            os.environ.pop("ABLETON_USER_LIBRARY", None)
        else:
            os.environ["ABLETON_USER_LIBRARY"] = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_save_midi_clip_lands_in_user_library_named_sensibly():
    with library() as lib:
        r = clip_export.save_clip(FakeBridge(), "live_set tracks 1 clip_slots 0 clip",
                                  show_session=False)
        p = Path(r["saved"])
        assert p.parent == lib / "AI Bridge" / "Clips"
        assert p.name == "Bass - Verse - 124 BPM.mid", p.name
        assert r["kind"] == "midi" and r["notes"] == 2          # the muted one is not played
        assert len([e for e in parse_midi(p.read_bytes())["events"] if e[1] == 0x90]) == 2


def test_save_audio_clip_copies_the_source():
    with library() as lib:
        src = lib / "source.wav"
        src.write_bytes(b"RIFF....WAVE")
        r = clip_export.save_clip(FakeBridge(audio_file=src),
                                  "live_set tracks 1 clip_slots 0 clip", show_session=False)
        assert r["kind"] == "audio"
        assert Path(r["saved"]).name == "Bass - Verse.wav"
        assert Path(r["saved"]).read_bytes() == src.read_bytes()


def test_empty_slot_is_reported_as_empty():
    b = FakeBridge()
    b.props[("live_set tracks 1 clip_slots 0", "has_clip")] = False
    with library():
        try:
            clip_export.save_clip(b, "live_set tracks 1 clip_slots 0 clip", show_session=False)
        except ValueError as exc:
            assert "empty" in str(exc)
            return
    raise AssertionError("an empty slot must be reported, not crash elsewhere")


if __name__ == "__main__":
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
