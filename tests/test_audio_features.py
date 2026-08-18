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
import shared_dsp  # noqa: E402
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


def test_digital_silence_is_reported_as_silent_not_characterised():
    """Found by rendering stems from a real set.

    Two send tracks with nothing routed to them came out as literal digital silence —
    every sample zero, true peak 0.00000000 — and ``describe`` called them
    "dark, noisy, percussive, compressed". Every label was a ratio computed from zeros.
    A producer handed that would believe an empty stem contained something.
    """
    import audio_features
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "silence.wav")
        _write(path, np.zeros(2 * RATE))
        out = audio_features.describe(path)
    assert out["labels"] == ["silent"], out["labels"]
    assert out.get("silent") is True
    for wrong in ("dark", "noisy", "percussive", "compressed", "bright", "tonal"):
        assert wrong not in out["labels"], f"still characterising silence as {wrong!r}"
    assert "silent" in out["summary"]


def test_a_very_quiet_but_real_signal_is_still_described():
    """The silence guard must not swallow quiet material — only genuine nothing."""
    import audio_features
    t = np.arange(2 * RATE) / RATE
    quiet = np.sin(2 * np.pi * 220.0 * t) * 0.02          # about -34 dBFS
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "quiet.wav")
        _write(path, quiet)
        out = audio_features.describe(path)
    assert out.get("silent") is not True, "a -34 dBFS tone is not silence"
    assert out["labels"] != ["silent"]


def test_white_noise_gets_no_key():
    """Krumhansl-Schmuckler always returns a winner, and correlation is scale
    invariant — so it cannot tell a flat histogram from a peaked one. White noise came
    back as a confident D minor with a 0.229 margin and ambiguous=False, and a noise
    riser was therefore searchable by key."""
    from describe import key_from_histogram
    noise = np.random.RandomState(5).randn(2 * RATE) * 0.3
    chroma = shared_dsp.chroma_of(shared_dsp.prepare(noise, RATE).mono)
    assert key_from_histogram(chroma).get("key") is None


def test_a_bass_note_is_resolved_at_all():
    """At 2048 samples the whole octave from A1 to A2 holds THREE bins, so twelve
    pitch classes could not be separated and the bottom octave of a bass line was
    invisible. Only a longer window buys real resolution — zero-padding interpolates
    the same smeared peak."""
    t = np.arange(2 * RATE) / RATE
    c2 = np.sin(2 * np.pi * (440 * 2 ** ((36 - 69) / 12)) * t)      # C2, 65.4 Hz
    chroma = np.array(shared_dsp.chroma_of(shared_dsp.prepare(c2, RATE).mono))
    assert int(np.argmax(chroma)) == 0, f"C2 should peak on C: {chroma.round(2)}"
    assert chroma[0] > 3 * np.median(chroma)


def test_band_energy_does_not_depend_on_capture_length():
    """Without dividing by the frame length, magnitude scales with how many samples
    went in — the same noise measured 28.1 dB at 1 s and 37.3 dB at 8 s."""
    noise = np.random.RandomState(1).randn(RATE * 8) * 0.2
    got = []
    for secs in (1, 8):
        seg = noise[:RATE * secs]
        got.append(audio_analysis.band_energies(np.stack([seg, seg], axis=1), RATE)["mid"])
    assert abs(got[0] - got[1]) < 1.0, got


def test_a_transient_at_the_start_is_not_windowed_away():
    """One Hann window across the WHOLE file is zero at its edges, so a one-shot — whose
    content is by definition at the start — was multiplied away. Measured 66 dB between
    the same click at t=0 and at t=2 s."""
    rs = np.random.RandomState(2)
    at_start = np.zeros(RATE * 4)
    at_start[:2000] = rs.randn(2000) * 0.8
    later = np.zeros(RATE * 4)
    later[RATE * 2:RATE * 2 + 2000] = at_start[:2000]
    a = audio_analysis.band_energies(np.stack([at_start, at_start], axis=1), RATE)["mid"]
    b = audio_analysis.band_energies(np.stack([later, later], axis=1), RATE)["mid"]
    assert abs(a - b) < 3.0, f"identical transient measured {a} vs {b} dB"



def test_brightness_is_measured_at_the_native_rate_not_the_analysis_rate():
    """Brightness lives largely above 8 kHz, so measuring it on the 16 kHz shared mono
    does not shift the number — it deletes the band the number is about.

    A 12 kHz tone cannot produce a centroid anywhere near 12 kHz if the signal has been
    resampled to 16 kHz first: there is no such frequency left to weight. Measured
    across 500 library files, computing this at 16 kHz relabelled 27% of them and
    emptied "very bright" of 75% of its members, because a crash that genuinely sits at
    6,833 Hz reads 4,093 Hz once its top octave is gone.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "high.wav")
        t = np.arange(RATE) / RATE
        _write(path, 0.5 * np.sin(2 * np.pi * 12000 * t))
        result = audio_features.analyze(path)
    assert result["centroid_hz"] > 10000, (
        f"centroid {result['centroid_hz']} Hz — a 12 kHz tone cannot read this low "
        f"unless brightness is being measured on the 16 kHz mono")


def test_a_bright_sound_is_still_called_very_bright():
    """The label, not just the number. The thresholds are calibrated for native-rate
    centroids and were deliberately left untouched when the measurement moved back, so
    the two have to keep agreeing."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "air.wav")
        rs = np.random.RandomState(1)
        noise = rs.randn(RATE)
        spec = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(len(noise), 1.0 / RATE)
        spec[(freqs < 8000) | (freqs > 16000)] = 0        # nothing but "air"
        bright = np.fft.irfft(spec, n=len(noise))
        _write(path, bright / (np.abs(bright).max() or 1.0) * 0.5)
        described = audio_features.describe(path)
    assert "very bright" in described["labels"], described["labels"]


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
