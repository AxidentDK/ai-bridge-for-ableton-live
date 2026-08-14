"""Render/save logic tests — fake bridge, no Live, no UI, no pytest.

The keystroke/dialog machinery itself can only be validated against a real
Live; these tests cover everything around it: refusals, validation, the save
mtime loop, and the duration-mismatch trap detection.
"""
import os
import sys
import tempfile
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "host"))

import render  # noqa: E402


class FakeBridge:
    def __init__(self, props=None):
        self.props = props or {}
        self.sets = []
        self.calls = []

    def get(self, path, prop):
        return self.props.get(f"{path}.{prop}")

    def set(self, path, prop, value):
        self.sets.append((path, prop, value))

    def call(self, path, func, *args):
        self.calls.append((path, func) + args)


def test_save_refuses_unsaved_set():
    r = render.save_set(FakeBridge({"live_set.file_path": ""}))
    assert r["saved"] is False and r["error"] == "unsaved_set"


def test_save_missing_file_raises():
    fb = FakeBridge({"live_set.file_path": r"C:\definitely\not\here.als"})
    try:
        render.save_set(fb)
        assert False, "should raise on nonexistent file"
    except render.SaveSetError:
        pass


def test_save_verifies_mtime_change():
    with tempfile.TemporaryDirectory() as td:
        als = os.path.join(td, "set.als")
        with open(als, "wb") as fh:
            fh.write(b"x")
        fb = FakeBridge({"live_set.file_path": als})
        original = render._send_save_keystroke

        def fake_keystroke():  # "Live" saves: bump the mtime
            os.utime(als, (os.path.getmtime(als) + 5, os.path.getmtime(als) + 5))
        render._send_save_keystroke = fake_keystroke
        try:
            r = render.save_set(fb, timeout=3.0, poll_interval=0.05)
        finally:
            render._send_save_keystroke = original
        assert r["saved"] is True and r["mtime_after"] != r["mtime_before"]


def test_save_not_observed():
    with tempfile.TemporaryDirectory() as td:
        als = os.path.join(td, "set.als")
        with open(als, "wb") as fh:
            fh.write(b"x")
        fb = FakeBridge({"live_set.file_path": als})
        original = render._send_save_keystroke
        render._send_save_keystroke = lambda: None  # keystroke goes nowhere
        try:
            r = render.save_set(fb, timeout=1.0, poll_interval=0.05)
        finally:
            render._send_save_keystroke = original
        assert r["saved"] is False and r["error"] == "save_not_observed"


def test_export_validations():
    fb = FakeBridge()
    with tempfile.TemporaryDirectory() as td:
        for bad_kwargs, why in [
            (dict(output_path=""), "empty path"),
            (dict(output_path=os.path.join(td, "x.mp3")), "not wav"),
            (dict(output_path=os.path.join(td, "no", "dir", "x.wav")), "missing dir"),
            (dict(output_path=os.path.join(td, "x.wav"), start_beats=0.0), "half a range"),
        ]:
            try:
                render.export_set(fb, **bad_kwargs)
                assert False, f"should reject: {why}"
            except render.ExportError:
                pass
        exists = os.path.join(td, "already.wav")
        with open(exists, "wb") as fh:
            fh.write(b"x")
        try:
            render.export_set(fb, exists)
            assert False, "should refuse to overwrite"
        except render.ExportError:
            pass


def test_duration_mismatch_flagged():
    ok = render._check_duration({"exported": True, "duration_s": 134.4}, 268.8)
    assert ok["warning"] == "duration_mismatch" and "selection" in ok["hint"]
    fine = render._check_duration({"exported": True, "duration_s": 268.5}, 268.8)
    assert "warning" not in fine
    none = render._check_duration({"exported": True, "duration_s": 10.0}, None)
    assert "warning" not in none and "expected_duration_s" not in none


def test_read_wav_result():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.wav")
        with wave.open(p, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\x00\x00" * 2 * 44100)  # 1 second stereo
        r = render._read_wav_result(p)
        assert r["duration_s"] == 1.0 and r["sample_rate"] == 44100
        assert r["channels"] == 2 and r["bit_depth"] == 16
        empty = os.path.join(td, "empty.wav")
        with open(empty, "wb") as fh:
            fh.write(b"RIFF")
        assert render._read_wav_result(empty) is None


def test_export_stems_validates_inputs():
    fb = FakeBridge()
    try:
        render.export_stems(fb, os.path.join(tempfile.gettempdir(), "no-such-dir-xyz"))
        assert False, "missing dir must be refused"
    except render.ExportError as e:
        assert "does not exist" in str(e)
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "old.wav"), "wb") as fh:
            fh.write(b"RIFF")
        try:
            render.export_stems(fb, td)
            assert False, "non-empty dir must be refused"
        except render.ExportError as e:
            assert "already contains" in str(e)
    with tempfile.TemporaryDirectory() as td:
        try:
            render.export_stems(fb, td, start_beats=0.0)  # missing length
            assert False, "half a range must be refused"
        except render.ExportError as e:
            assert "together" in str(e)


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
