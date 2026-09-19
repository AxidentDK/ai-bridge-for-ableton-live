"""The fixes from the first unattended Gemini run (2026-09-19): every one is a case where
the bridge did what it was asked and said nothing useful about it.

No pytest, no Live: fake bridges only. Run the file.
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "host"))

import api                                                            # noqa: E402
import mcp_server                                                     # noqa: E402
import render                                                         # noqa: E402
from client import BridgeError                                        # noqa: E402


class FakeParam:
    """A DeviceParameter whose display is a function of its raw value."""

    def __init__(self, lo, hi, show, quantized=False):
        self.lo, self.hi, self.show, self.quantized = lo, hi, show, quantized
        self.value = lo


class FakeBridge:
    def __init__(self, params=None, props=None):
        self.params = params or {}
        self.props = dict(props or {})
        self.sets = []

    def get(self, path, prop):
        if path in self.params:
            p = self.params[path]
            return {"min": p.lo, "max": p.hi, "is_quantized": p.quantized,
                    "value": p.value, "display_value": p.show(p.value)}[prop]
        return self.props[f"{path}.{prop}"]

    def set(self, path, prop, value):
        self.sets.append((path, prop, value))
        if path in self.params:
            self.params[path].value = value
        else:
            self.props[f"{path}.{prop}"] = value
        return True

    def call(self, path, func, *args):
        pass


# --- parameters answer in Live's units ---------------------------------------------------

def test_set_by_display_bisects_a_continuous_parameter():
    """A limiter gain: raw 0..1 shown as -24..+24 dB. Asking for '-3 dB' must land there."""
    gain = FakeParam(0.0, 1.0, lambda v: -24.0 + 48.0 * v)
    live = api.Live(FakeBridge({"p": gain}))
    out = live.set_by_display("p", "-3 dB")
    assert abs(out["display"] - (-3.0)) < 0.05, out
    assert abs(gain.value - 0.4375) < 0.002, gain.value


def test_set_by_display_matches_a_quantized_parameter_by_text():
    """An arpeggiator rate is a list index; '1/16' is a name, not a number."""
    names = ["1/1", "1/2", "1/4", "1/6", "1/8", "1/12", "1/16", "1/32"]
    rate = FakeParam(0, 7, lambda v: names[int(v)], quantized=True)
    live = api.Live(FakeBridge({"p": rate}))
    out = live.set_by_display("p", "1/16")
    assert out["value"] == 6 and out["display"] == "1/16", out


def test_set_by_display_refuses_what_the_range_cannot_reach():
    gain = FakeParam(0.0, 1.0, lambda v: -24.0 + 48.0 * v)
    try:
        api.Live(FakeBridge({"p": gain})).set_by_display("p", "+40 dB")
    except ValueError as exc:
        assert "-24.0 to 24.0" in str(exc), exc
    else:
        raise AssertionError("accepted an unreachable value")


def test_a_raw_value_write_answers_with_the_display_value():
    """The field-hit: 0.0 written meaning unity, -24 dB in fact, and nothing said."""
    gain = FakeParam(0.0, 1.0, lambda v: -24.0 + 48.0 * v)
    fake = FakeBridge({"p": gain})
    mcp_server._bridge = fake
    mcp_server.bridge = lambda: fake
    out = mcp_server.run_tool("live_set", {"path": "p", "prop": "value", "value": 0.0})
    assert out == {"ok": True, "value": 0.0, "display": -24.0}, out
    # a non-parameter write is unchanged
    out = mcp_server.run_tool("live_set", {"path": "live_set", "prop": "tempo", "value": 90})
    assert out is True


# --- errors that say what to do ----------------------------------------------------------

def test_a_cpp_signature_error_says_how_many_arguments_and_which():
    raw = ("Python argument types in\n    Track.create_midi_clip(Track)\n"
           "did not match C++ signature:\n    create_midi_clip(class TTrackPyHandle, double, double)")
    hint = mcp_server._signature_hint(raw)
    assert hint == "create_midi_clip needs (start_beats, length_beats)", hint
    generic = mcp_server._signature_hint(
        "did not match C++ signature:\n    frobnicate(class TPyHandle, double, bool)")
    assert generic == "frobnicate needs 2 arguments: number, true/false", generic
    assert mcp_server._signature_hint("something else entirely") is None


def test_a_timeout_becomes_a_sentence_about_live_being_busy():
    class Stuck:
        def ping(self):
            return "pong"

        def get(self, path, prop):
            raise TimeoutError("timed out")

    mcp_server.bridge = lambda: Stuck()
    try:
        mcp_server.run_tool("live_get", {"path": "live_set", "prop": "tempo"})
    except RuntimeError as exc:
        assert "rendering" in str(exc) and "try again" in str(exc), exc
    else:
        raise AssertionError("timeout passed through raw")


# --- export: the loop brace, the mix state, the field text ----------------------------

def test_the_loop_brace_is_shrunk_before_it_is_moved():
    fb = FakeBridge(props={"live_set.tempo": 120.0})
    render._set_loop_brace(fb, 128.0, 4.0)
    props = [s[1] for s in fb.sets]
    assert props.index("loop_length") < props.index("loop_start"), props


def test_a_soloed_track_is_named_in_the_export_result():
    fb = FakeBridge(props={"live_set.tracks": [1, 2, 3],
                           "live_set tracks 0.name": "Drone", "live_set tracks 0.solo": False,
                           "live_set tracks 0.mute": True,
                           "live_set tracks 1.name": "Brass", "live_set tracks 1.solo": False,
                           "live_set tracks 1.mute": False,
                           "live_set tracks 2.name": "Ensemble", "live_set tracks 2.solo": True,
                           "live_set tracks 2.mute": False})
    state = render._mix_state(fb)
    assert state["soloed"] == ["Ensemble"] and state["muted"] == ["Drone"], state
    assert "solo_active" in state["warning"] and "Ensemble" in state["warning"]
    assert "warning" not in render._mix_state(FakeBridge(props={"live_set.tracks": []}))


def test_beats_become_live_field_text():
    assert render._bars_beats(0, 4, 4) == "1.1.1"
    assert render._bars_beats(128, 4, 4) == "33.1.1"
    assert render._bars_beats(4, 4, 4) == "2.1.1"
    assert render._bars_beats(5.5, 4, 4) == "2.2.3"
    assert render._bars_beats(6, 3, 4) == "3.1.1"          # 3/4: 3 beats per bar


def test_the_tool_list_has_the_display_setter():
    names = {t["name"] for t in mcp_server.TOOLS}
    assert "live_set_display" in names


def _run():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run() else 0)
