"""Clip-note module tests — fakes for the Live note API. No Live, no pytest."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_script import dispatch  # noqa: E402
from remote_script.lom import LomError  # noqa: E402
from remote_script.notes import add_notes, get_notes, remove_notes  # noqa: E402


class FakeNote:
    def __init__(self, pitch, start_time, duration, velocity, mute):
        self.pitch = pitch
        self.start_time = start_time
        self.duration = duration
        self.velocity = velocity
        self.mute = mute
        self.note_id = id(self) % 100000
        self.probability = 1.0


class FakeSpec:
    def __init__(self, pitch, start_time, duration, velocity, mute):
        self.args = dict(pitch=pitch, start_time=start_time, duration=duration,
                         velocity=velocity, mute=mute)


class FakeClip:
    def __init__(self):
        self._notes: list[FakeNote] = []

    def get_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        return [n for n in self._notes
                if from_pitch <= n.pitch < from_pitch + pitch_span
                and from_time <= n.start_time < from_time + time_span]

    def add_new_notes(self, specs):
        for s in specs:
            self._notes.append(FakeNote(**s.args))

    def remove_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        self._notes = [n for n in self._notes
                       if not (from_pitch <= n.pitch < from_pitch + pitch_span
                               and from_time <= n.start_time < from_time + time_span)]


class FakeSlot:
    def __init__(self):
        self.clip = FakeClip()


class FakeTrack:
    def __init__(self):
        self.clip_slots = [FakeSlot()]


class FakeSong:
    def __init__(self):
        self.tracks = [FakeTrack()]


def roots():
    return {"live_set": FakeSong()}


CLIP = "live_set tracks 0 clip_slots 0 clip"


def test_add_and_get_roundtrip():
    r = roots()
    n = add_notes(r, CLIP, [
        {"pitch": 64, "start_time": 0.0, "duration": 1.0, "velocity": 100},
        {"pitch": 67, "start_time": 1.0, "duration": 0.5},
    ], FakeSpec)
    assert n == 2
    got = get_notes(r, CLIP)
    assert [g["pitch"] for g in got] == [64, 67]
    assert got[0]["velocity"] == 100.0 and got[1]["velocity"] == 100.0  # default
    assert got[0]["mute"] is False and "note_id" in got[0]


def test_get_windowed():
    r = roots()
    add_notes(r, CLIP, [
        {"pitch": 40, "start_time": 0.0, "duration": 1.0},
        {"pitch": 60, "start_time": 4.0, "duration": 1.0},
        {"pitch": 80, "start_time": 8.0, "duration": 1.0},
    ], FakeSpec)
    assert len(get_notes(r, CLIP, from_time=3.0, time_span=2.0)) == 1
    assert len(get_notes(r, CLIP, from_pitch=50, pitch_span=40)) == 2


def test_remove_window_and_all():
    r = roots()
    add_notes(r, CLIP, [
        {"pitch": 40, "start_time": 0.0, "duration": 1.0},
        {"pitch": 60, "start_time": 4.0, "duration": 1.0},
    ], FakeSpec)
    remove_notes(r, CLIP, from_time=3.0, time_span=2.0)
    assert [g["pitch"] for g in get_notes(r, CLIP)] == [40]
    remove_notes(r, CLIP)
    assert get_notes(r, CLIP) == []


def test_bad_notes_rejected():
    r = roots()
    for bad in ([], "nope", [{"pitch": 64}]):
        try:
            add_notes(r, CLIP, bad, FakeSpec)
            assert False, f"should reject {bad!r}"
        except LomError as e:
            assert e.type == "bad_request"


def test_not_a_clip():
    r = roots()
    try:
        get_notes(r, "live_set tracks 0")
        assert False, "tracks are not clips"
    except LomError as e:
        assert e.type == "wrong_type"


def test_via_dispatch():
    ctx = {"roots": roots(), "note_spec_factory": FakeSpec}
    resp = dispatch.handle(ctx, {"id": 1, "method": "clip_add_notes", "params": {
        "path": CLIP, "notes": [{"pitch": 72, "start_time": 0.0, "duration": 2.0}]}})
    assert resp["ok"] and resp["result"] == 1
    resp = dispatch.handle(ctx, {"id": 2, "method": "clip_get_notes",
                                 "params": {"path": CLIP}})
    assert resp["ok"] and resp["result"][0]["pitch"] == 72
    resp = dispatch.handle(ctx, {"id": 3, "method": "clip_remove_notes",
                                 "params": {"path": CLIP}})
    assert resp["ok"] and resp["result"] is True


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
