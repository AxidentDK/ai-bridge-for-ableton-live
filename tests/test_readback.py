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

    def __init__(self, lo, hi, show, quantized=False, label=None):
        self.lo, self.hi, self.show, self.quantized = lo, hi, show, quantized
        self.label = label or (lambda v: str(show(v)))
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
        if func == "str_for_value" and path in self.params:
            return self.params[path].label(args[0])


# --- parameters answer in Live's units ---------------------------------------------------

def test_set_by_display_bisects_a_continuous_parameter():
    """A limiter gain: raw 0..1 shown as -24..+24 dB. Asking for '-3 dB' must land there."""
    gain = FakeParam(0.0, 1.0, lambda v: -24.0 + 48.0 * v)
    live = api.Live(FakeBridge({"p": gain}))
    out = live.set_by_display("p", "-3 dB")
    assert abs(float(out["display"]) - (-3.0)) < 0.05, out
    assert abs(gain.value - 0.4375) < 0.002, gain.value


def test_a_parameter_whose_unit_prefix_changes_along_its_range():
    """EQ Eight's frequency: '10.0 Hz' at the bottom, '22.0 kHz' at the top. Read as bare
    numbers that is a range of 10..22 and '186 Hz' is refused (field-hit 2026-09-19)."""
    import math

    def label(v):
        hz = 10.0 * (2200.0 ** v)
        return "%.1f kHz" % (hz / 1000.0) if hz >= 1000 else "%.1f Hz" % hz

    freq = FakeParam(0.0, 1.0, lambda v: v, label=label)
    live = api.Live(FakeBridge({"p": freq}))
    out = live.set_by_display("p", "186 Hz")
    assert out["display"].startswith("186"), out
    out = live.set_by_display("p", "5.5 kHz")
    assert out["display"] == "5.5 kHz", out
    assert abs(10.0 * (2200.0 ** freq.value) - 5500) < 60, math.log(freq.value)


def test_set_by_display_matches_a_list_parameter_by_its_text():
    """An arpeggiator rate, as the real Live presents it (field-hit 2026-09-19): NOT flagged
    quantized, display_value is the INDEX, and only str_for_value says '1/16'. Parsing the
    target as a number turned '1/8' into 1."""
    names = ["1/1", "1/2", "1/4", "1/6", "1/8", "1/12", "1/16", "1/32"]
    rate = FakeParam(0, 7, lambda v: float(v), quantized=False,
                     label=lambda v: names[int(v)])
    live = api.Live(FakeBridge({"p": rate}))
    out = live.set_by_display("p", "1/16")
    assert out["value"] == 6 and out["display"] == "1/16", out
    assert rate.value == 6
    try:
        live.set_by_display("p", "1/7")
    except ValueError as exc:
        assert "1/6, 1/8" in str(exc), exc       # the error lists what IS offered
    else:
        raise AssertionError("accepted a rate that does not exist")


def test_set_by_display_refuses_what_the_range_cannot_reach_and_touches_nothing():
    """The first version probed the range by WRITING min and max, so a refused '+40 dB'
    left a real master limiter at +24 dB. The search must not write at all."""
    gain = FakeParam(0.0, 1.0, lambda v: -24.0 + 48.0 * v)
    gain.value = 0.5
    fake = FakeBridge({"p": gain})
    try:
        api.Live(fake).set_by_display("p", "+40 dB")
    except ValueError as exc:
        assert "-24.0 to 24.0" in str(exc), exc
    else:
        raise AssertionError("accepted an unreachable value")
    assert gain.value == 0.5 and fake.sets == [], fake.sets


def test_set_by_display_writes_exactly_once():
    gain = FakeParam(0.0, 1.0, lambda v: -24.0 + 48.0 * v)
    fake = FakeBridge({"p": gain})
    api.Live(fake).set_by_display("p", "6 dB")
    assert len(fake.sets) == 1, fake.sets
    assert abs(gain.value - 0.625) < 0.002


def test_set_by_display_costs_a_handful_of_round_trips_not_forty():
    """While Live plays, one call costs 0.5-0.8 s. A forty-step bisection was 24 s to turn
    one knob; the grid search asks 33 questions per round-trip."""
    class Counting(FakeBridge):
        trips = 0

        def get_many(self, pairs):
            Counting.trips += 1
            return [FakeBridge.get(self, p, prop) for p, prop in pairs]

        def batch(self, ops):
            Counting.trips += 1
            return [{"ok": True, "result": self.call(o["params"]["path"], o["params"]["func"],
                                                     *o["params"]["args"])} for o in ops]

        def set(self, path, prop, value):
            Counting.trips += 1
            return FakeBridge.set(self, path, prop, value)

    gain = FakeParam(0.0, 1.0, lambda v: -24.0 + 48.0 * v,
                     label=lambda v: "%.2f dB" % (-24.0 + 48.0 * v))
    out = api.Live(Counting({"p": gain})).set_by_display("p", "-7.3 dB")
    assert abs(float(out["display"].split()[0]) + 7.3) < 0.02, out
    assert Counting.trips <= 5, Counting.trips          # props + <=3 grids + the write


def test_a_fader_that_reads_minus_infinity_at_the_bottom_still_bisects():
    import math
    fader = FakeParam(0.0, 1.0, lambda v: v,
                      label=lambda v: "-inf dB" if v <= 0 else "%.1f dB" % (40 * math.log10(v) + 6))
    out = api.Live(FakeBridge({"p": fader})).set_by_display("p", "-12 dB")
    assert abs(float(out["display"].split()[0]) + 12.0) < 0.1, out


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


def test_beats_become_live_field_segments():
    bb = render._bars_beats
    assert bb(0, 4, 4) == (1, 1, 1)                      # a position is 1-based
    assert bb(128, 4, 4) == (33, 1, 1)
    assert bb(5.5, 4, 4) == (2, 2, 3)
    assert bb(6, 3, 4) == (3, 1, 1)                      # 3/4: 3 beats per bar
    assert bb(4, 4, 4, position=False) == (1, 0, 0)      # a length is a duration
    assert bb(384, 4, 4, position=False) == (96, 0, 0)
    assert bb(2.75, 4, 4, position=False) == (0, 2, 3)


def test_a_live_collection_answers_how_many():
    """Live's Vectors have no length attribute; a model asks for one anyway."""
    from remote_script import lom

    class Vector:                       # iterable, not a list — like Live's own
        def __init__(self, items):
            self._items = items

        def __iter__(self):
            return iter(self._items)

    class Song:
        tracks = Vector(["a", "b", "c"])
        name = "set"

    roots = {"live_set": Song()}
    assert lom.get(roots, "live_set tracks", "length") == 3
    assert lom.get(roots, "live_set tracks", "count") == 3
    try:
        lom.get(roots, "live_set", "length")          # a Song is not a collection
    except lom.LomError as exc:
        assert "no property" in str(exc)
    else:
        raise AssertionError("a non-collection answered 'length'")


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
