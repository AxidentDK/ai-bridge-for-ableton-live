"""Warp-marker tests — fakes for Live's warp API. No Live, no pytest.

The fakes mirror behaviour that was PROBED against Live 12.4.3 rather than assumed, which
is the only reason they are worth testing against:

* ``add_warp_marker`` refuses anything that is not a real WarpMarker object — dict, tuple,
  list and two floats were all rejected with "No registered converter was able to produce
  a C++ rvalue of type NApiHelpers::TWarpMarker".
* the LAST marker is a shadow marker and moving it raises "The shadow marker can't be
  moved."
* a freshly imported loop has two markers, at (0.0, 0.0) and (0.010416, 0.015625).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import traceback                                                       # noqa: E402

from remote_script.lom import LomError                                 # noqa: E402
from remote_script.warp import warp_markers                            # noqa: E402


class FakeMarker:
    def __init__(self, sample_time, beat_time):
        self.sample_time = float(sample_time)
        self.beat_time = float(beat_time)


def factory(sample_time, beat_time):
    """Stands in for ``Live.Clip.WarpMarker`` — the injected constructor."""
    return FakeMarker(sample_time, beat_time)


class FakeAudioClip:
    def __init__(self):
        self.warp_markers = [FakeMarker(0.0, 0.0), FakeMarker(0.010416, 0.015625)]
        self.warping = True
        self.warp_mode = 0
        self.available_warp_modes = [0, 1, 2, 3, 4, 6]
        self.is_audio_clip = True
        self.sample_rate = 44100.0
        self.sample_length = 235200
        self.reanalysed = 0

    def __setattr__(self, name, value):
        # Live re-analyses the sample when warping is toggled; count it so a test can
        # assert the ORDER of operations rather than trusting a comment.
        if name == "warping" and getattr(self, "warping", None) is not None:
            object.__setattr__(self, "reanalysed", self.reanalysed + 1)
        object.__setattr__(self, name, value)

    def add_warp_marker(self, marker):
        if not isinstance(marker, FakeMarker):
            raise RuntimeError("No registered converter was able to produce a C++ rvalue "
                               "of type NApiHelpers::TWarpMarker from this Python object "
                               f"of type {type(marker).__name__}")
        self.warp_markers.append(marker)

    def move_warp_marker(self, beat_time, delta):
        for i, marker in enumerate(self.warp_markers):
            if abs(marker.beat_time - beat_time) < 1e-9:
                if i == len(self.warp_markers) - 1:
                    raise RuntimeError("The shadow marker can't be moved.")
                marker.beat_time += delta
                return
        raise RuntimeError("The specified warp marker doesn't exist")

    def remove_warp_marker(self, beat_time):
        for i, marker in enumerate(self.warp_markers):
            if abs(marker.beat_time - beat_time) < 1e-9:
                del self.warp_markers[i]
                return
        raise RuntimeError("The specified warp marker doesn't exist")


class FakeMidiClip:
    warp_markers = []
    is_audio_clip = False


def roots_with(clip):
    return {"live_set": type("Song", (), {"clip": clip})()}


PATH = "live_set clip"


def test_reading_flattens_markers_that_the_wire_returns_as_opaque_objects():
    out = warp_markers(roots_with(FakeAudioClip()), PATH, factory)
    assert out["marker_count"] == 2
    assert out["markers"][0] == {"sample_time": 0.0, "beat_time": 0.0}
    assert out["markers"][1]["beat_time"] == 0.015625
    assert out["warping"] is True
    assert out["available_warp_modes"] == [0, 1, 2, 3, 4, 6]


def test_a_marker_is_built_through_the_injected_factory_not_from_a_dict():
    """The whole reason this is an RPC: Live will not convert a dict."""
    clip = FakeAudioClip()
    out = warp_markers(roots_with(clip), PATH, factory,
                       add=[{"sample_time": 2.0, "beat_time": 4.0}])
    assert out["applied"][0]["ok"] is True, out["applied"]
    assert out["marker_count"] == 3
    assert isinstance(clip.warp_markers[-1], FakeMarker)


def test_passing_a_dict_straight_through_would_have_been_rejected():
    """Guards the claim in the docstring: a dict really is refused by Live."""
    clip = FakeAudioClip()
    try:
        clip.add_warp_marker({"sample_time": 2.0, "beat_time": 4.0})
    except RuntimeError as exc:
        assert "TWarpMarker" in str(exc)
    else:
        raise AssertionError("the fake accepted a dict; it should mirror Live's refusal")


def test_moving_the_shadow_marker_explains_itself_instead_of_leaking_livespeak():
    out = warp_markers(roots_with(FakeAudioClip()), PATH, factory,
                       move=[{"beat_time": 0.015625, "beat_time_delta": 0.5}])
    applied = out["applied"][0]
    assert applied["ok"] is False
    assert "shadow marker" in applied["error"]
    assert "end-of-sample" in applied["error"], applied["error"]


def test_a_real_marker_moves():
    clip = FakeAudioClip()
    clip.warp_markers.append(FakeMarker(1.0, 2.0))          # so [1] is no longer last
    out = warp_markers(roots_with(clip), PATH, factory,
                       move=[{"beat_time": 0.015625, "beat_time_delta": 0.25}])
    assert out["applied"][0]["ok"] is True, out["applied"]
    assert abs(clip.warp_markers[1].beat_time - 0.265625) < 1e-9


def test_one_bad_edit_does_not_lose_the_others():
    """A caller adding several markers should not lose them all to one bad beat time."""
    clip = FakeAudioClip()
    out = warp_markers(roots_with(clip), PATH, factory,
                       remove=[999.0],
                       add=[{"sample_time": 1.0, "beat_time": 1.0},
                            {"sample_time": 2.0, "beat_time": 2.0}])
    outcomes = [step["ok"] for step in out["applied"]]
    assert outcomes == [False, True, True], out["applied"]
    assert out["marker_count"] == 4


def test_warping_is_written_last_because_it_stalls_lives_main_thread():
    """The read must be complete BEFORE the re-analysis, or the caller loses it."""
    clip = FakeAudioClip()
    out = warp_markers(roots_with(clip), PATH, factory, warping=False,
                       add=[{"sample_time": 1.0, "beat_time": 1.0}])
    # The add landed and is visible in the same reply that triggered re-analysis.
    assert out["marker_count"] == 3
    assert clip.reanalysed == 1
    assert out["warping"] is False
    assert "re-analyse" in out["note"]


def test_an_unavailable_warp_mode_is_refused_with_the_list_of_real_ones():
    try:
        warp_markers(roots_with(FakeAudioClip()), PATH, factory, warp_mode=5)
    except LomError as exc:
        assert "available" in str(exc) and "[0, 1, 2, 3, 4, 6]" in str(exc)
    else:
        raise AssertionError("warp_mode 5 is not in available_warp_modes")


def test_a_midi_clip_is_refused_by_name():
    try:
        warp_markers(roots_with(FakeMidiClip()), PATH, factory)
    except LomError as exc:
        assert "MIDI clip" in str(exc)
    else:
        raise AssertionError("a MIDI clip has no warp markers")


def test_limit_caps_what_is_returned_without_lying_about_the_count():
    clip = FakeAudioClip()
    clip.warp_markers.extend([FakeMarker(i, i) for i in range(1, 6)])
    out = warp_markers(roots_with(clip), PATH, factory, limit=2)
    assert len(out["markers"]) == 2
    assert out["marker_count"] == 7, "the count must describe the clip, not the page"


def _run() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:                                              # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"  PASS  {name}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
