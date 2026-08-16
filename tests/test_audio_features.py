"""Tests for host/audio_features.py and the band split in host/audio_analysis.py.

Synthetic WAVs with known answers, because every bug below produced a plausible number
rather than an error. No pytest: run the file.
"""
import os
import sys
import tempfile
import traceback
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))

import audio_analysis  # noqa: E402
import audio_features  # noqa: E402

RATE = 44100


def _write(path, left, right=None):
    right = left if right is None else right
    data = np.clip(np.stack([left, right], axis=1), -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((data * 32767).astype("<i2").tobytes())


def _hit(seconds=0.12, seed=0):
    t = np.arange(int(seconds * RATE)) / RATE
    rs = np.random.RandomState(seed)
    return (rs.randn(len(t)) * np.exp(-t * 40) * 0.5
            + np.sin(2 * np.pi * 60 * t) * np.exp(-t * 14))


def _loop(bpm, seconds=4.0, loud_hit=None):
    step = 60.0 / bpm / 2.0                     # eighth notes
    sig = np.zeros(int(seconds * RATE))
    for i in range(int(seconds / step)):
        h = _hit(seed=i) * (3.0 if i == loud_hit else 1.0)
        start = int(i * step * RATE)
        if start + len(h) <= len(sig):
            sig[start:start + len(h)] += h
    return sig / (np.abs(sig).max() or 1.0) * 0.9


def test_brightness_does_not_depend_on_trailing_silence():
    """A silent frame has a centroid of 0. Averaging frames equally let dead air drag
    brightness down — the same tone read 3162 Hz alone and 814 Hz with three seconds
    of silence after it, turning "bright" into "warm". Libraries are full of tails."""
    with tempfile.TemporaryDirectory() as tmp:
        t = np.arange(RATE) / RATE
        tone = 0.4 * np.sin(2 * np.pi * 3000 * t)
        bare, tailed = os.path.join(tmp, "a.wav"), os.path.join(tmp, "b.wav")
        _write(bare, tone)
        _write(tailed, np.concatenate([tone, np.zeros(3 * RATE)]))
        a = audio_features.analyze(bare)["centroid_hz"]
        b = audio_features.analyze(tailed)["centroid_hz"]
        assert abs(a - b) < 200, f"silence moved the centroid: {a} vs {b}"


def test_attack_times_the_first_hit_not_the_loudest():
    """Timing to the GLOBAL peak measured whichever hit happened to be loudest, so a
    loop whose third hit is biggest reported a 1.0 s "attack" — and could then never
    be labelled percussive, which needs < 0.02 s."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "loop.wav")
        _write(path, _loop(120, seconds=3.0, loud_hit=7))
        assert audio_features.analyze(path)["attack_s"] < 0.05


def test_an_onset_at_the_very_start_is_seen():
    """A Hann window is zero at its edges, so a transient at sample 0 was multiplied
    away — and that is where every loop's downbeat sits."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "hit.wav")
        sig = np.zeros(RATE)
        h = _hit()
        sig[:len(h)] = h
        _write(path, sig * 0.9)
        assert audio_features.analyze(path)["onsets"] >= 1


def test_tempo_reads_eighth_notes_at_the_right_octave():
    """Median inter-onset interval measures the commonest GAP, not the beat: eighths
    at 90 BPM came back as 178.2 — at confidence 1.0, because the old confidence
    measured how REGULAR the intervals were and a quantised loop in the wrong octave
    is perfectly regular."""
    with tempfile.TemporaryDirectory() as tmp:
        for bpm in (90, 120, 140):
            path = os.path.join(tmp, f"{bpm}.wav")
            _write(path, _loop(bpm))
            got = audio_features.analyze(path)
            assert got["tempo_bpm"] is not None, bpm
            assert abs(got["tempo_bpm"] - bpm) <= 3, f"{bpm} -> {got['tempo_bpm']}"


def test_peak_survives_an_out_of_phase_stereo_file():
    """Measured from the mono sum, a perfectly out-of-phase file cancelled to
    -240 dBFS while its channels sat at -10.5."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "oop.wav")
        noise = np.random.RandomState(3).randn(RATE) * 0.3
        _write(path, noise, -noise)
        got = audio_features.analyze(path)
        assert got["peak_dbfs"] > -30, got["peak_dbfs"]
        assert got["stereo_correlation"] < -0.9


def test_haas_widening_is_not_called_a_defect():
    """Flagging correlation < 0 as "out-of-phase" condemned ordinary widening: a 15 ms
    Haas delay measures about -0.3 and is a production choice, not a fault."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "haas.wav")
        src = np.random.RandomState(7).randn(2 * RATE) * 0.3
        delay = int(0.015 * RATE)
        _write(path, src, np.concatenate([np.zeros(delay), src[:-delay]]))
        assert "out-of-phase" not in audio_features.describe(path)["labels"]


def test_band_energies_are_flat_for_flat_noise():
    """Summing a band makes it proportional to how many FFT bins fall in it, and the
    bands are octave-ish — so white noise, flat by construction, read as a 20.3 dB
    high shelf that was purely bin count."""
    noise = np.random.RandomState(5).randn(3 * RATE) * 0.2
    bands = audio_analysis.band_energies(np.stack([noise, noise], axis=1), RATE)
    values = list(bands.values())
    assert max(values) - min(values) < 8, bands


def test_integrated_loudness_matches_the_standards_anchor():
    """BS.1770's own calibration: a 1 kHz sine at -20 dBFS in both channels is
    -20 LUFS. Also guards the block count — dropping the final block cost 0.49 LU on
    material that ends louder than it starts."""
    t = np.arange(3 * RATE) / RATE
    sine = 0.1 * np.sin(2 * np.pi * 1000 * t)
    got = audio_analysis.integrated_lufs(np.stack([sine, sine], axis=1), RATE)
    assert abs(got - (-20.0)) < 0.3, got


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
