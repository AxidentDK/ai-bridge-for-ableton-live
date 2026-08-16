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


_BPM_MIN, _BPM_MAX, _BPM_PRIOR, _BPM_PRIOR_WIDTH = 60.0, 190.0, 120.0, 0.45
#: Structural events per beat, sloping with tempo — fast music leans on half-time
#: phrasing and carries FEWER of them per beat, not more.
_EPB_SLOW, _EPB_FAST = 2.2, 1.5
_EPB_WIDTH = 0.9


def _tempo(np, flux, n_onsets: int, duration: float, rate: int):
    """BPM by autocorrelation of the onset-strength curve, plus (confidence).

    This REPLACED a median inter-onset interval, which measures the commonest gap
    rather than the beat. The sibling listener project retired the same algorithm after
    measuring it against 546 loops whose filenames state their own tempo: eighths at 90
    BPM came back as 178.2 **at confidence 1.0**, because the old confidence measured
    how REGULAR the intervals were, and a perfectly quantised loop in the wrong octave
    is perfectly regular. Confidence pointed the wrong way exactly where it mattered.

    Three multiplied terms, as validated there:
      * autocorrelation of the flux, harmonic-summed so a real beat's support at 2x,
        3x and 4x its period accumulates and a spurious peak's does not;
      * a perceptual prior around 120 BPM — deliberately not narrow, because a tight
        one buys mid-tempo accuracy by taking it from drum-and-bass and trap;
      * events per beat, which is what actually separates a tempo from its half.
    """
    if flux is None or len(flux) < 8 or not duration:
        return None, None
    frames_per_sec = rate / float(_HOP)
    x = flux - flux.mean()
    n = 1 << int(np.ceil(np.log2(len(x) * 2)))
    spectrum = np.fft.rfft(x, n=n)
    acf = np.fft.irfft(spectrum * np.conj(spectrum))[:len(x)]
    # Unbiased: fewer overlapping terms at longer lags would otherwise slope the curve
    # downward and systematically prefer fast tempos.
    acf = acf / np.maximum(np.arange(len(x), 0, -1), 1)
    if acf[0] <= 0:
        return None, None
    acf = acf / acf[0]
    lo = max(2, int(round(frames_per_sec * 60.0 / _BPM_MAX)))
    hi = min(len(acf) - 2, int(round(frames_per_sec * 60.0 / _BPM_MIN)))
    if hi <= lo:
        return None, None
    lags = np.arange(lo, hi + 1)
    bpms = 60.0 * frames_per_sec / lags

    harmonic = np.zeros(len(lags))
    for k, weight in ((1, 1.0), (2, 0.5), (3, 0.33), (4, 0.25)):
        idx = lags * k
        valid = idx < len(acf)
        harmonic[valid] += weight * acf[idx[valid]]
    prior = np.exp(-0.5 * (np.log2(bpms / _BPM_PRIOR) / _BPM_PRIOR_WIDTH) ** 2)
    if n_onsets >= 2:
        beats = duration * bpms / 60.0
        epb = n_onsets / np.maximum(beats, 1e-9)
        frac = ((np.log2(bpms) - np.log2(85.0)) / (np.log2(170.0) - np.log2(85.0)))
        target = _EPB_SLOW + frac * (_EPB_FAST - _EPB_SLOW)
        density = np.exp(-0.5 * (np.log2(epb / target) / _EPB_WIDTH) ** 2)
    else:
        density = np.ones(len(lags))

    best = int(np.argmax(harmonic * prior * density))
    lag = float(lags[best])
    if 0 < lag < len(acf) - 1:
        a, b, c = acf[int(lag) - 1], acf[int(lag)], acf[int(lag) + 1]
        denom = 2.0 * (a - 2.0 * b + c)
        if abs(denom) > 1e-12:
            lag += float(np.clip((a - c) / denom, -0.5, 0.5))
    bpm = 60.0 * frames_per_sec / lag
    if not (_BPM_MIN <= bpm <= _BPM_MAX):
        return None, None

    # Confidence is the MARGIN over the nearest metrical rival, not how rhythmic the
    # file is. Rivals are looked up over a wider lag range than the search itself —
    # at 100 BPM the half-time rival sits outside the 60-190 window entirely, and
    # reading it as zero made the margin a perfect 1.0 precisely where the octave was
    # most ambiguous.
    band = harmonic * prior * density
    # Rivals are read from the SCORED candidates, not the raw autocorrelation. A clean
    # loop's half-time alias is genuinely strong in the ACF, so measuring the margin
    # there leaves almost every file near zero confidence — honest, but useless. What
    # a caller needs is how sure the system is GIVEN everything it knows.
    posterior = np.zeros(len(acf))
    posterior[lags] = band
    winner = float(posterior[int(round(lag))]) if int(round(lag)) < len(acf) else 0.0
    rivals = []
    for factor in (0.5, 2.0, 1.0 / 3.0, 3.0, 1.5, 2.0 / 3.0):
        r = int(round(lag * factor))
        if 0 < r < len(posterior) and abs(r - lag) > 1:
            rivals.append(float(posterior[r]))
    margin = 1.0 - max(rivals) / winner if rivals and winner > 1e-12 else 1.0
    margin = max(0.0, min(1.0, margin))
    spread = float(band.std()) or 1e-9
    prominence = min(1.0, max(0.0, (float(band[best]) - float(band.mean())) / spread / 3.0))
    confidence = round(max(0.0, min(1.0, prominence * margin)), 2)
    return round(bpm, 1), confidence


def analyze(path: str) -> dict:
    """Extract character features from a WAV file."""
    np = _numpy()
    samples, rate = read_wav(path)
    if samples.size == 0:
        return {"error": "empty file", "path": path}
    mono = samples.mean(axis=1)
    duration = len(mono) / float(rate)

    # Peak comes from the CHANNELS, not the mono sum. An out-of-phase stereo file
    # cancels when summed: a real test case read -240 dBFS from the sum while the
    # channels were at -10.5. Crest and RMS follow the same signal for consistency.
    loudest = np.abs(samples).max(axis=1) if samples.ndim > 1 else np.abs(samples)
    peak = float(np.max(loudest)) or 1e-12
    rms = float(np.sqrt(np.mean(loudest ** 2))) or 1e-12
    crest_db = round(20.0 * np.log10(peak / rms), 1)

    # Stereo: correlation near 1 is effectively mono, near -1 means the channels
    # fight and the sum will partially cancel — a real defect worth naming.
    correlation = None
    if samples.shape[1] >= 2:
        left, right = samples[:, 0], samples[:, 1]
        if np.std(left) > 1e-9 and np.std(right) > 1e-9:
            correlation = round(float(np.corrcoef(left, right)[0, 1]), 3)

    # CENTRED frames: half a window of silence in front, so frame i is centred on
    # sample i*hop. A Hann window is zero at its edges, so a transient sitting at
    # sample 0 — where most one-shots and every loop's downbeat put theirs — was
    # multiplied away and never seen. Timing a frame by its start rather than its
    # centre also reported every onset half a window early.
    spec = np.abs(np.fft.rfft(_frames(np, np.pad(mono, (_N_FFT // 2, 0))), axis=1))
    freqs = np.fft.rfftfreq(_N_FFT, 1.0 / rate)
    energy = spec.sum(axis=1) + 1e-12

    # ENERGY-WEIGHTED across frames. A silent frame has a centroid of 0, and averaging
    # frames equally let dead air drag brightness down: the same 1 s tone read 3162 Hz
    # alone and 814 Hz with three seconds of trailing silence — "bright" became "warm"
    # because of the tail. Sample libraries are full of tails and pre-roll, and
    # brightness is the first word of every summary.
    per_frame = (spec * freqs[None, :]).sum(axis=1) / energy
    loud_enough = energy > (float(energy.max()) * 1e-3)
    centroid = float(np.average(per_frame[loud_enough],
                                weights=energy[loud_enough])
                     if loud_enough.any() else 0.0)
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
    # Prepend silence so a file that STARTS on its transient still shows a rise: with
    # nothing before frame 0, the loudest hit in the file can produce no flux at all.
    padded_spec = np.vstack([np.zeros((1, spec.shape[1])), spec])
    flux = np.maximum(0.0, np.diff(padded_spec, axis=0)).sum(axis=1)
    onsets = []
    if flux.size and np.any(flux):
        # A peak must clear the local mean AND be significant against the largest rise
        # in the file. The mean+std threshold alone collapses toward the noise floor
        # when there are no real onsets, and starts reporting it.
        threshold = max(flux.mean() + flux.std(), 0.15 * float(flux.max()))
        last = -10
        for i, value in enumerate(flux):
            if value > threshold and i - last > 4:      # ~46 ms minimum spacing
                onsets.append(max(0.0, i * _HOP / float(rate)))
                last = i
    onset_rate = len(onsets) / duration if duration else 0.0

    tempo, tempo_conf = _tempo(np, flux, len(onsets), duration, rate)

    # Envelope: time to the peak of the FIRST hit, and whether it decays or sustains.
    env = np.abs(mono)
    win = max(1, int(0.01 * rate))
    env = np.convolve(env, np.ones(win) / win, mode="same")
    # Scoped to the first hit. Timing to the GLOBAL peak measured whichever hit
    # happened to be loudest, so a four-hit loop whose third hit is the biggest
    # reported a 1.0 s "attack" — and a snappy drum loop could then never be labelled
    # percussive, which needs < 0.02 s.
    limit = len(env)
    if len(onsets) > 1:
        limit = max(1, min(limit, int(onsets[1] * rate)))
    attack_s = round(float(np.argmax(env[:limit])) / rate, 3)
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
