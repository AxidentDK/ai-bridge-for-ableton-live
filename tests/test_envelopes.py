"""Clip-envelope module tests — fakes for Live's envelope API. No Live, no pytest."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_script import dispatch  # noqa: E402
from remote_script.envelopes import clear, insert_steps, read_values  # noqa: E402
from remote_script.lom import LomError  # noqa: E402


DEFAULT = 0.933  # a parameter's un-automated value (mirrors the live probe)


class FakeEnvelope:
    """Mirrors the REAL semantics (live-probed): a step holds its value over
    [time, time+length); a zero-length step writes nothing; outside any step,
    value_at_time returns the parameter's default."""

    def __init__(self):
        self.steps = []

    def insert_step(self, time, length, value):
        if float(length) > 0.0:  # zero-length = silent no-op, like Live
            self.steps.append((float(time), float(length), float(value)))

    def value_at_time(self, t):
        for time, length, value in sorted(self.steps):
            if time <= t < time + length:
                return value
        return DEFAULT


class FakeParam:
    def __init__(self, name, automatable=True):
        self.name = name
        self.automatable = automatable


class FakeClip:
    """Mirrors the REAL Live behavior (found live-validating): an envelope
    does not exist until created — ``automation_envelope`` returns None until
    ``create_automation_envelope`` has been called for that parameter."""

    def __init__(self, params):
        self._envs = {}
        self._params = params
        self.length = 8.0  # beats — the auto-fill target for the last step

    def automation_envelope(self, param):
        return self._envs.get(param.name)  # None until created — like Live

    def create_automation_envelope(self, param):
        if not param.automatable:
            return None
        return self._envs.setdefault(param.name, FakeEnvelope())

    def clear_envelope(self, param):
        self._envs.pop(param.name, None)

    def clear_all_envelopes(self):
        self._envs.clear()


class FakeDevice:
    def __init__(self):
        self.parameters = [FakeParam("Device On"), FakeParam("Filter Freq"),
                           FakeParam("NoAuto", automatable=False)]


class FakeSlot:
    def __init__(self, clip):
        self.clip = clip


class FakeTrack:
    def __init__(self):
        self.devices = [FakeDevice()]
        self.clip_slots = [FakeSlot(FakeClip(self.devices[0].parameters))]


class FakeSong:
    def __init__(self):
        self.tracks = [FakeTrack()]


def roots():
    return {"live_set": FakeSong()}


CLIP = "live_set tracks 0 clip_slots 0 clip"
PARAM = "live_set tracks 0 devices 0 parameters 1"


def test_insert_and_read_roundtrip():
    r = roots()
    n = insert_steps(r, CLIP, PARAM, [
        {"time": 0.0, "value": 0.2},                  # auto-fills to 2.0
        {"time": 2.0, "value": 0.5, "length": 1.0},   # explicit hold 2.0-3.0
        {"time": 4.0, "value": 0.9},                  # auto-fills to clip end (8.0)
    ])
    assert n == 3
    assert read_values(r, CLIP, PARAM, [0.5, 2.5, 5.0, 7.9]) == [0.2, 0.5, 0.9, 0.9]
    # the explicit-length gap 3.0-4.0 is un-automated -> the parameter default
    assert read_values(r, CLIP, PARAM, [3.5]) == [0.933]


def test_zero_length_rejected_and_steps_sorted():
    r = roots()
    try:
        insert_steps(r, CLIP, PARAM, [{"time": 0.0, "value": 0.5, "length": 0.0}])
        assert False, "zero-length steps do nothing in Live — must be rejected"
    except LomError as e:
        assert e.type == "bad_request" and "zero-length" in e.message
    # out-of-order input still auto-fills correctly (sorted first)
    insert_steps(r, CLIP, PARAM, [{"time": 4.0, "value": 0.9},
                                  {"time": 0.0, "value": 0.1}])
    assert read_values(r, CLIP, PARAM, [1.0, 5.0]) == [0.1, 0.9]


def test_envelopes_are_per_parameter():
    r = roots()
    other = "live_set tracks 0 devices 0 parameters 0"
    insert_steps(r, CLIP, PARAM, [{"time": 0.0, "value": 1.0}])
    insert_steps(r, CLIP, other, [{"time": 0.0, "value": 0.25}])
    assert read_values(r, CLIP, PARAM, [1.0]) == [1.0]
    assert read_values(r, CLIP, other, [1.0]) == [0.25]


def _read_errors(r, param):
    try:
        read_values(r, CLIP, param, [0.0])
        return False
    except LomError as e:
        return e.type == "live_error" and "nothing is automated" in e.message


def test_read_before_any_write_errors():
    assert _read_errors(roots(), PARAM)  # no envelope exists yet — like Live


def test_clear_one_and_all():
    r = roots()
    other = "live_set tracks 0 devices 0 parameters 0"
    insert_steps(r, CLIP, PARAM, [{"time": 0.0, "value": 1.0}])
    insert_steps(r, CLIP, other, [{"time": 0.0, "value": 0.25}])
    clear(r, CLIP, PARAM)  # one parameter only
    assert _read_errors(r, PARAM)  # its envelope is gone
    assert read_values(r, CLIP, other, [0.0]) == [0.25]  # the other survives
    clear(r, CLIP)  # everything
    assert _read_errors(r, other)


def test_non_automatable_parameter_errors():
    try:
        insert_steps(roots(), CLIP, "live_set tracks 0 devices 0 parameters 2",
                     [{"time": 0.0, "value": 1.0}])
        assert False, "None envelope must raise"
    except LomError as e:
        assert e.type == "live_error" and "automatable" in e.message


def test_bad_steps_and_wrong_object():
    r = roots()
    for bad in ([], "nope", [{"time": 1.0}]):
        try:
            insert_steps(r, CLIP, PARAM, bad)
            assert False, f"should reject {bad!r}"
        except LomError as e:
            assert e.type == "bad_request"
    try:
        insert_steps(r, "live_set tracks 0", PARAM, [{"time": 0, "value": 1}])
        assert False, "tracks have no envelopes"
    except LomError as e:
        assert e.type == "wrong_type"


def test_via_dispatch():
    ctx = {"roots": roots()}
    resp = dispatch.handle(ctx, {"id": 1, "method": "clip_envelope_insert", "params": {
        "path": CLIP, "parameter": PARAM,
        "steps": [{"time": 0.0, "value": 0.3}, {"time": 4.0, "value": 0.8}]}})
    assert resp["ok"] and resp["result"] == 2
    resp = dispatch.handle(ctx, {"id": 2, "method": "clip_envelope_read", "params": {
        "path": CLIP, "parameter": PARAM, "times": [1.0, 7.0]}})
    assert resp["ok"] and resp["result"] == [0.3, 0.8]
    resp = dispatch.handle(ctx, {"id": 3, "method": "clip_envelope_clear",
                                 "params": {"path": CLIP}})
    assert resp["ok"] and resp["result"] is True
    resp = dispatch.handle(ctx, {"id": 4, "method": "clip_envelope_read", "params": {
        "path": CLIP, "parameter": PARAM, "times": [0.0]}})
    assert resp["ok"] is False and resp["error"]["type"] == "live_error"


def test_ramp_helper():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "host"))
    from api import Live

    steps = Live.ramp(0.0, 8.0, 0.2, 1.0, steps=5)
    # starts spread over [0, 8) so the last step's auto-length reaches the end
    assert [s["time"] for s in steps] == [0.0, 1.6, 3.2, 4.8, 6.4]
    assert steps[0]["value"] == 0.2 and steps[-1]["value"] == 1.0
    assert abs(steps[2]["value"] - 0.6) < 1e-9  # halfway
    assert all("length" not in s for s in steps)  # auto-fill does the lengths
    try:
        Live.ramp(0, 1, 0, 1, steps=1)
        assert False, "1-step ramp is not a ramp"
    except ValueError:
        pass


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
