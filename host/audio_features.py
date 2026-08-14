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

Complements ``audio_analysis`` (LUFS / peak / bands), which stays as ported.
"""
from __future__ import annotations

import os

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
    """Extract character features from a WAV file."""
    np = _numpy()
    samples, rate = read_wav(path)
    if samples.size == 0:
        return {"error": "empty file", "path": path}
    mono = samples.mean(axis=1)
    duration = len(mono) / float(rate)

    peak = float(np.max(np.abs(mono))) or 1e-12
    rms = float(np.sqrt(np.mean(mono ** 2))) or 1e-12
    crest_db = round(20.0 * np.log10(peak / rms), 1)

    # Stereo: correlation near 1 is effectively mono, near -1 means the channels
    # fight and the sum will partially cancel — a real defect worth naming.
    correlation = None
    if samples.shape[1] >= 2:
        left, right = samples[:, 0], samples[:, 1]
        if np.std(left) > 1e-9 and np.std(right) > 1e-9:
            correlation = round(float(np.corrcoef(left, right)[0, 1]), 3)

    spec = np.abs(np.fft.rfft(_frames(np, mono), axis=1))
    freqs = np.fft.rfftfreq(_N_FFT, 1.0 / rate)
    energy = spec.sum(axis=1) + 1e-12

    centroid = float(np.mean((spec * freqs[None, :]).sum(axis=1) / energy))
    # Flatness: geometric/arithmetic mean. ~1 = noise-like (a snare, a hat),
    # near 0 = strongly tonal (a held note).
    geo = np.exp(np.mean(np.log(spec + 1e-12), axis=1))
    ari = np.mean(spec, axis=1) + 1e-12
    flatness = float(np.mean(geo / ari))

    # Chroma: fold spectral energy onto the 12 pitch classes.
    chroma = [0.0] * 12
    usable = (freqs > 55.0) & (freqs < 5000.0)
    if usable.any():
        pcs = np.round(12 * np.log2(freqs[usable] / 440.0) + 69).astype(int) % 12
        weights = spec[:, usable].sum(axis=0)
        for pc in range(12):
            chroma[pc] = float(weights[pcs == pc].sum())

    # Onsets via spectral flux: rises in energy, peak-picked above an adaptive
    # threshold. Rate separates percussive material from sustained.
    flux = np.maximum(0.0, np.diff(spec, axis=0)).sum(axis=1)
    onsets = []
    if flux.size:
        threshold = flux.mean() + flux.std()
        last = -10
        for i, value in enumerate(flux):
            if value > threshold and i - last > 4:      # ~46 ms minimum spacing
                onsets.append((i + 1) * _HOP / float(rate))
                last = i
    onset_rate = len(onsets) / duration if duration else 0.0

    # Tempo from the median inter-onset interval, folded into a musical range.
    # Confidence comes from how CONSISTENT those intervals are: on percussive
    # material this lands within ~0.3 BPM (validated at 90/120/140), but on
    # sustained material there are no real onsets and the same arithmetic
    # produces a confident-looking wrong number — so the spread is reported and
    # a ragged estimate is marked unreliable rather than presented as fact.
    tempo, tempo_conf = None, None
    if len(onsets) >= 4:
        iois = np.diff(onsets)
        iois = iois[iois > 0.04]
        if iois.size >= 3:
            median = float(np.median(iois))
            if median > 0:
                spread = float(np.median(np.abs(iois - median)) / median)
                tempo_conf = round(max(0.0, 1.0 - spread * 2.0), 2)
                bpm = 60.0 / median
                while bpm < 60:
                    bpm *= 2
                while bpm > 190:
                    bpm /= 2
                tempo = round(bpm, 1)

    # Envelope: time to peak (attack) and whether it decays or sustains.
    env = np.abs(mono)
    win = max(1, int(0.01 * rate))
    env = np.convolve(env, np.ones(win) / win, mode="same")
    attack_s = round(float(np.argmax(env)) / rate, 3)
    tail = env[int(len(env) * 0.75):]
    sustained = bool(tail.mean() > 0.25 * (env.max() or 1e-12))

    return {
        "path": path,
        "duration_s": round(duration, 3),
        "sample_rate": rate,
        "channels": int(samples.shape[1]),
        "peak_dbfs": round(20.0 * np.log10(peak), 1),
        "crest_db": crest_db,
        "stereo_correlation": correlation,
        "centroid_hz": round(centroid, 1),
        "flatness": round(flatness, 4),
        "chroma": [round(c, 2) for c in chroma],
        "key": key_from_histogram(chroma),
        "onsets": len(onsets),
        "onset_rate_hz": round(onset_rate, 2),
        "tempo_bpm": tempo,
        "tempo_confidence": tempo_conf,
        "attack_s": attack_s,
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
        elif f["stereo_correlation"] < 0:
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
