"""Describe an audio file by its measurable CHARACTER — the audio tier of the index.

What this can and cannot do, stated plainly, because the difference decides
whether the output is trustworthy:

  CAN (measured)     duration, loudness, dynamics/crest, stereo width and phase,
                     spectral balance and brightness, tonal-vs-noisy, attack and
                     decay shape, onset density, tempo, and a chroma-based key.
  CANNOT (perceived) genre, era, mood, "sounds like a vintage break", or which
                     instrument is playing in a dense mix. Those need a model
                     that hears the waveform; measurement does not reach them.

So the summaries read "dark, sustained, tonal, F minor, ~92 BPM" and never
"melancholy boom-bap loop". That is the honest ceiling — and for finding things
in a large filename-only sample library it is most of the practical value.

Key estimation reuses ``describe.key_from_histogram``: the same Krumhansl
profiles serve MIDI (bins weighted by note duration) and audio (bins weighted by
chroma energy), so both tiers agree on what "F minor" means.

The MEASUREMENTS themselves live in ``shared_dsp`` — the same file, byte for byte,
that the listener uses, with a SHA-256 check in both test suites. What remains here
is what only this side needs: brightness, crest, peak and the plain-language labels.
Three separate faults once lived in both copies of this maths at the same time.

Complements ``audio_analysis`` (LUFS / peak / bands), which stays as ported.
"""
from __future__ import annotations

import os

import shared_dsp
from audio_analysis import _numpy, read_wav
from describe import key_from_histogram

_N_FFT = 2048
_HOP = 512


def _frames(np, mono, n_fft=_N_FFT, hop=_HOP):
    if len(mono) < n_fft:
        mono = np.pad(mono, (0, n_fft - len(mono)))
    count = 1 + (len(mono) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(count)[:, None]
    return mono[idx] * np.hanning(n_fft)[None, :]


def analyze(path: str) -> dict:
    """Extract character features from a WAV file.

    The MEASUREMENTS come from ``shared_dsp`` — the same module, byte for byte, that
    the listener uses. What stays here is what only this side needs: brightness, crest,
    peak, and the plain-language labels below. Everything that was duplicated is gone,
    including the three separate occasions on which a fix made in the listener was
    hand-ported here and arrived broken.

    One consequence worth stating plainly: analysis now happens at 16 kHz on a signal
    the shared resampler produced, not at the file's native rate. The bridge gains
    whole-bar tempo snapping, stereo width and sub-frame onset interpolation it did not
    have, and gives up nothing that was measured well before.
    """
    np = _numpy()
    samples, rate = read_wav(path)
    if samples.size == 0:
        return {"error": "empty file", "path": path}

    prepared = shared_dsp.prepare(samples, rate)
    # snap_to_bars=False: this side analyses ARBITRARY audio — a recorded clip, a
    # rendered stem, whatever a user points at — not a sample-library loop cut to
    # whole bars. Snapping assumes the file IS a whole number of bars, and on a
    # genuine 90 BPM clip lasting a bar and a half it answers 120.
    measured = shared_dsp.measure(prepared, snap_to_bars=False)
    mono = prepared.mono
    duration = prepared.duration

    # Peak comes from the CHANNELS, not the mono sum. An out-of-phase stereo file
    # cancels when summed: a real test case read -240 dBFS from the sum while the
    # channels were at -10.5. Crest and RMS follow the same signal for consistency.
    loudest = np.abs(samples).max(axis=1) if samples.ndim > 1 else np.abs(samples)
    peak = float(np.max(loudest)) or 1e-12
    rms = float(np.sqrt(np.mean(loudest ** 2))) or 1e-12
    crest_db = round(20.0 * np.log10(peak / rms), 1)

    # BRIGHTNESS is the bridge's own: the listener has no use for it, and it is the
    # first word of every summary below. Energy-weighted across frames, because a
    # silent frame has a centroid of 0 and averaging frames equally let dead air drag
    # it down — the same tone read 3162 Hz alone and 814 Hz with three seconds of
    # silence after it.
    spec = np.abs(np.fft.rfft(_frames(np, np.pad(mono, (_N_FFT // 2, 0))), axis=1))
    freqs = np.fft.rfftfreq(_N_FFT, 1.0 / shared_dsp.ANALYSIS_SR)
    energy = spec.sum(axis=1) + 1e-12
    per_frame = (spec * freqs[None, :]).sum(axis=1) / energy
    loud_enough = energy > (float(energy.max()) * 1e-3)
    centroid = float(np.average(per_frame[loud_enough], weights=energy[loud_enough])
                     if loud_enough.any() else 0.0)

    chroma = shared_dsp.chroma_of(mono)
    onsets = shared_dsp.onset_times(mono)
    onset_rate = len(onsets) / duration if duration else 0.0
    env = shared_dsp.smoothed_envelope(mono)
    tail = env[int(len(env) * 0.75):]
    sustained = bool(tail.mean() > 0.25 * (env.max() or 1e-12))

    return {
        "path": path,
        "duration_s": round(duration, 3),
        "sample_rate": rate,
        "channels": int(samples.shape[1]),
        "peak_dbfs": round(20.0 * np.log10(peak), 1),
        "crest_db": crest_db,
        "stereo_correlation": measured["stereo_correlation"],
        "stereo_width": measured["stereo_width"],
        "centroid_hz": round(centroid, 1),
        "flatness": measured["flatness"],
        "chroma": [round(c, 2) for c in chroma],
        "key": key_from_histogram(chroma),
        "onsets": measured["onsets"],
        "onset_rate_hz": round(onset_rate, 2),
        "tempo_bpm": measured.get("bpm"),
        "tempo_confidence": measured.get("bpm_confidence"),
        "bars": measured.get("bars"),
        "attack_s": round(measured["attack_ms"] / 1000.0, 3),
        "decay_ms": measured.get("decay_ms"),
        "loudness_lufs": measured.get("loudness_lufs"),
        "kind": measured["kind"],
        "sustained": sustained,
    }


def describe(path: str) -> dict:
    """Feature analysis plus plain-language character labels."""
    f = analyze(path)
    if "error" in f:
        return f

    labels = []
    centroid = f["centroid_hz"]
    labels.append("dark" if centroid < 800 else
                  "warm" if centroid < 2000 else
                  "bright" if centroid < 4500 else "very bright")
    labels.append("noisy" if f["flatness"] > 0.35 else
                  "tonal" if f["flatness"] < 0.12 else "mixed")
    labels.append("percussive" if f["attack_s"] < 0.02 and not f["sustained"] else
                  "sustained" if f["sustained"] else "plucked")
    if f["duration_s"] < 2 and f["onsets"] <= 2:
        labels.append("one-shot")
    elif f["onset_rate_hz"] > 4:
        labels.append("busy")
    if f["crest_db"] < 6:
        labels.append("compressed")
    if f["stereo_correlation"] is not None:
        if f["stereo_correlation"] > 0.98:
            labels.append("mono-ish")
        elif f["stereo_correlation"] < -0.5:
            # Was `< 0`, which called ordinary widening a defect: a 15 ms Haas delay
            # measures -0.294 and is a deliberate production choice, not a fault. Only
            # a strongly negative correlation means the mono sum genuinely cancels.
            labels.append("out-of-phase")
        elif f["stereo_correlation"] < 0.5:
            labels.append("wide")

    parts = list(labels)
    key = f.get("key") or {}
    if key.get("key") and not key.get("ambiguous"):
        parts.append(key["key"])
    # Only claim a tempo when the onsets were regular enough to mean it.
    if f.get("tempo_bpm") and (f.get("tempo_confidence") or 0) >= 0.5:
        parts.append(f"~{f['tempo_bpm']:g} BPM")
    elif f.get("tempo_bpm"):
        parts.append(f"~{f['tempo_bpm']:g} BPM (uncertain)")
    parts.append(f"{f['duration_s']:g}s")

    f["labels"] = labels
    f["summary"] = ", ".join(parts)
    f["name"] = os.path.basename(path)
    f["note"] = ("Measured character only — brightness, texture, envelope, key and "
                 "tempo. It cannot judge genre, era or mood; that needs a model "
                 "that hears the audio. Key from chroma is an estimate and is "
                 "weakest on percussive or atonal material.")
    return f
