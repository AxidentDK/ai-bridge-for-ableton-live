"""The DSP both programs run — ONE implementation, copied verbatim, hash-checked.

WHY THIS FILE EXISTS
--------------------
The listener and the bridge measure some of the same things: onsets, tempo, chroma,
key, flatness, envelope, stereo, loudness. They were written twice and kept in step by
hand, and that failed three times in one night. The last failure is the one worth
remembering: the wide-lag tempo fix was made in the listener at 01:00 and reproduced in
the bridge at 03:00 **with its explanatory comment copied across intact** — the comment
warned about precisely the bug that the port reintroduced. Reading a warning is not
executing it.

So the maths lives here once. The listener repo owns this file; the bridge carries a
byte-for-byte copy, and a test in each repo compares SHA-256 of the whole file and
refuses to pass when they differ. That is the same contract the SQLite schema already
runs under — the one duplicated thing in this project that has never drifted.

THREE RULES THAT MAKE THE CONTRACT WORK. Breaking any one of them re-opens the hole:

1. **This module owns PRE-PROCESSING, not just the maths.** Everything enters through
   ``prepare()``, which sums to mono and resamples to ``ANALYSIS_SR`` with the
   resampler defined below. If each program did its own resampling — the bridge
   analysed at 44.1 kHz, the listener resampled with soxr — then identical maths still
   returns different numbers and the drift has merely moved somewhere harder to see.
   ``measure()`` refuses a raw array for exactly this reason: it takes a ``Prepared``.

2. **The divergence check is SHA-256 of the whole file, never substring matching.** A
   substring check would happily pass a copy in which ``_FLUX_FFT = 1024`` had become
   ``8192``. A whole-file hash also forces this file to stay genuinely self-contained,
   which is why there are no relative imports, no database access and no dependency
   beyond numpy.

3. **numpy only, and even numpy is imported softly.** The bridge ships with
   ``dependencies = []`` and makes numpy an optional extra; its MIDI key detection
   imports the Krumhansl core below on machines that have no numpy at all. The
   pure-Python functions (``tonal_contrast``, ``key_scores``, ``_correlate``) must
   therefore never touch numpy. Everything array-shaped requires it and says so.

WHAT IS DELIBERATELY *NOT* HERE
-------------------------------
Routing and presentation. Which measurements a file earns (the listener's one-shot /
loop router), the YIN pitch estimator (listener-only — the bridge has no pitch
detector to agree with), spectral centroid and crest (bridge-only), and the shape of
the dict each program returns. Sharing code that only one caller runs buys nothing and
costs a hash check on every edit.
"""
from __future__ import annotations

from math import gcd

try:
    import numpy as np
except ImportError:            # pure-Python callers (MIDI key detection) still import
    np = None                  # this module; see rule 3 above.

#: Bump when anything here changes what a caller would STORE. Each program folds this
#: into its own analyzer-version string, so a change to the shared maths invalidates
#: cached results in both — which is the whole point of sharing it.
#: dsp1 = extracted from listener/features.py at feat5, byte-identical maths.
DSP_VERSION = "dsp1"

#: Everything is measured at 16 kHz. Not a compromise for speed: the flux hop, the
#: chroma window and the events-per-beat anchors below were all calibrated at this
#: rate, on 546 loops whose filenames state their own tempo. Measuring the same file
#: at 44.1 kHz changes every one of those constants' meaning.
ANALYSIS_SR = 16000


def _require_numpy():
    if np is None:
        raise RuntimeError(
            "shared_dsp needs numpy for anything array-shaped. Only the key/chroma "
            "scoring functions (tonal_contrast, key_scores) are pure Python.")


# =====================================================================================
# PRE-PROCESSING — rule 1. Nothing below this line ever sees a caller's own resampling.
# =====================================================================================

#: Sinc zero-crossings kept either side of each output sample. 16 is the quality/cost
#: knob: at 44.1 -> 16 kHz it yields a 91-tap filter, which is ~40 ms of numpy for a
#: 30 s file. Raising it sharpens the transition band and costs linearly.
_RESAMPLE_ZEROS = 16
#: Kaiser beta for a ~120 dB stopband, from the standard design rule
#: beta = 0.1102 * (A - 8.7) with A = 120 dB. Aliasing that far down is inaudible and,
#: more to the point, far below the numerical noise of everything measured from it.
_RESAMPLE_BETA = 12.2653
#: Output samples processed per strided block — a memory bound, not a tuning knob.
#: The result is identical at any value; only the size of the temporary changes.
_RESAMPLE_CHUNK = 8192


class Prepared:
    """A signal that has been through ``prepare()`` — the only input ``measure()`` takes.

    Deliberately a distinct type rather than a tuple of arrays. A caller cannot hand in
    a mono signal it resampled itself, because it has no way to construct one of these
    without going through ``prepare()``. That is rule 1 enforced by the type rather
    than by a comment nobody reads.

    ``mono`` is float32 at ``ANALYSIS_SR``; ``source`` is the ORIGINAL array at its own
    rate, still multi-channel, because stereo width and BS.1770 loudness must be
    measured before the downmix and before the resample.
    """

    __slots__ = ("mono", "source", "sample_rate", "duration")

    def __init__(self, mono, source, sample_rate, duration):
        self.mono = mono
        self.source = source
        self.sample_rate = sample_rate
        self.duration = duration


def to_mono(audio):
    """Sum to a single float32 channel. Always via the mean, never channel 0.

    Taking one channel would silently halve a hard-panned sample and miss anything
    that only exists in the other side.
    """
    _require_numpy()
    audio = np.asarray(audio)
    if audio.ndim == 1:
        audio = audio[:, None]
    return audio.mean(axis=1).astype(np.float32)


#: Built once per (source rate, target rate), not per file: a scan analyses thousands
#: of files and a library holds a handful of distinct sample rates. Same reason the
#: mel filterbank is cached — the design is pure and the arrays are small.
_RESAMPLE_CACHE: dict = {}


def _resample_filter(up: int, taps_half: int, cutoff: float):
    """One polyphase bank: [up, 2*taps_half+1] windowed-sinc kernels, DC-normalised.

    Row ``p`` is the kernel for an output sample whose true position falls ``p/up`` of
    an input sample past an input sample boundary, so the whole rational conversion is
    a strided view and one matrix-vector product per phase — no giant zero-stuffed
    intermediate signal.
    """
    key = (up, taps_half, cutoff)
    if key in _RESAMPLE_CACHE:
        return _RESAMPLE_CACHE[key]
    offsets = np.arange(2 * taps_half + 1) - taps_half
    frac = np.arange(up) / float(up)
    d = offsets[None, :] - frac[:, None]
    kernel = 2.0 * cutoff * np.sinc(2.0 * cutoff * d)
    # Window over (taps_half + 1) rather than taps_half, so the outermost tap of a
    # fractional phase is attenuated rather than annihilated.
    shape = np.sqrt(np.maximum(0.0, 1.0 - (d / (taps_half + 1.0)) ** 2))
    kernel = kernel * (np.i0(_RESAMPLE_BETA * shape) / np.i0(_RESAMPLE_BETA))
    # Unity gain at DC per phase. Without this the output ripples at the phase rate —
    # a buzz at src_sr/up, which on a 44.1 kHz source is squarely in the audio band.
    #
    # DESIGNED in float64, STORED in float32. The design wants double precision (np.i0
    # of a large argument, and the sinc tails), but the product in ``resample`` runs
    # against float32 audio and matching the dtypes is the difference between numpy
    # calling sgemv and silently upcasting every strided block to float64 first —
    # measured 24 ms against 282 ms for one 30 s file at 44.1 kHz. The extra precision
    # would have been discarded by the float32 output either way.
    bank = (kernel / kernel.sum(axis=1, keepdims=True)).astype(np.float32)
    _RESAMPLE_CACHE[key] = bank
    return bank


def resample(x, src_sr: int, dst_sr: int = ANALYSIS_SR):
    """Rational band-limited resampling, numpy only.

    THE POINT IS THAT BOTH PROGRAMS GET THE SAME SAMPLES, not that this is the finest
    resampler available. soxr is better and the listener uses it for the mel pass,
    which is validated against Essentia's own pipeline and must not move. But soxr is
    not numpy, and this file may not depend on anything else (rule 3) — and a feature
    computed off a soxr signal in one program and a linear-interpolated one in the
    other is exactly the silent divergence this module exists to end.

    Linear interpolation is not an option here even as a fallback: it is a 2-tap
    low-pass whose response droops ~4 dB by half of Nyquist and folds everything above
    it back into the band. Chroma and spectral flatness are read straight out of that
    region.
    """
    _require_numpy()
    x = np.asarray(x, dtype=np.float32)
    if src_sr == dst_sr or not len(x):
        return x
    divisor = gcd(int(src_sr), int(dst_sr))
    up, down = int(dst_sr) // divisor, int(src_sr) // divisor
    # Cutoff in cycles per INPUT sample: the lower of the two Nyquists. Downsampling
    # needs the anti-alias filter, upsampling needs the image filter; one expression
    # covers both.
    cutoff = 0.5 * min(1.0, float(dst_sr) / float(src_sr))
    taps_half = int(np.ceil(_RESAMPLE_ZEROS / (2.0 * cutoff)))
    bank = _resample_filter(up, taps_half, cutoff)
    taps = bank.shape[1]

    n_out = int(round(len(x) * float(up) / float(down)))
    if n_out < 1:
        return np.zeros(0, dtype=np.float32)
    padded = np.pad(x, (taps_half, taps_half + taps + down))
    out = np.zeros(n_out, dtype=np.float32)
    stride = padded.strides[0]
    # NO INDEX ARRAYS ANYWHERE IN HERE, and that is the whole performance story.
    #
    # Output sample j sits at input position j*down/up, so its fractional phase is
    # (j*down) % up. Because up and down are coprime, each phase recurs every `up`
    # outputs — and those outputs' input positions advance by exactly `down`. So every
    # phase is a plain strided view over the input and one matrix-vector product, with
    # no gather and no scan. Measured on one 30 s file at 44.1 kHz: this form 24 ms,
    # against 282 ms building the indices explicitly and 234 ms selecting each phase
    # with a boolean scan (which is O(up * n_out) — 153 million comparisons).
    for first_out in range(min(up, n_out)):
        kernel = bank[(first_out * down) % up]
        count = (n_out - first_out + up - 1) // up
        first_in = (first_out * down) // up
        dest = out[first_out::up]
        # Chunked so the contiguous copy BLAS makes of each strided block stays
        # bounded: an integer decimation (up == 1) puts every output sample into one
        # phase, and a single 30 s file would otherwise materialise a few hundred MB.
        for start in range(0, count, _RESAMPLE_CHUNK):
            rows = min(_RESAMPLE_CHUNK, count - start)
            view = np.lib.stride_tricks.as_strided(
                padded[first_in + start * down:], shape=(rows, taps),
                strides=(stride * down, stride))
            dest[start:start + rows] = view @ kernel
    return out


def prepare(audio, sample_rate: int, duration: float | None = None) -> Prepared:
    """THE GATEWAY. Sum to mono, resample to ``ANALYSIS_SR``, keep the original.

    ``duration`` overrides the array's own length, and exists for one real case: a
    scanner that decodes only the first N seconds of a long file still knows the true
    length from the container, and the whole-bar tempo snap wants the true one.
    """
    _require_numpy()
    audio = np.asarray(audio)
    if audio.ndim == 1:
        audio = audio[:, None]
    if duration is None:
        duration = len(audio) / float(sample_rate) if sample_rate else None
    return Prepared(resample(to_mono(audio), sample_rate, ANALYSIS_SR),
                    audio, int(sample_rate), duration)


# =====================================================================================
# FRAMING
# =====================================================================================

#: Spectral flux, not high-frequency content. HFC over-triggers on exactly the
#: material a sample library is full of — shakers, vinyl crackle, noise sweeps — and
#: would report them as dense loops.
_FLUX_FFT, _FLUX_HOP = 1024, 256                      # 16 ms hop at 16 kHz
_MIN_ONSET_GAP_S = 0.05                               # 50 ms: two hits, not one flam
_MEDIAN_WIN = 9                                       # ~145 ms moving threshold
_FLUX_DELTA = 0.6                                     # above the local median, in SDs
#: A peak must also reach this fraction of the file's largest rise. Stops the adaptive
#: threshold from tracking down into the noise when there is nothing to find.
_FLUX_MIN_FRACTION = 0.15

#: Onset counts that decide the route. A single hit, or a hit with a flam or a short
#: pre-delay, stays a one-shot; a bar of anything has at least four.
ONE_SHOT_MAX_ONSETS = 2
LOOP_MIN_ONSETS = 4
#: A loop's onsets must cover this much of its length. Without it, ripple in a decay
#: counts as a rhythm — see the 808 case in ``classify``.
LOOP_MIN_SPAN = 0.5
#: Exactly three onsets is genuinely ambiguous, so it falls back to the duration rule
#: that used to decide everything. Not a fudge — a triplet fill and a three-hit loop
#: are the same measurement, and length is the only thing left to separate them.
ONE_SHOT_SECONDS = 2.048

KIND_ONE_SHOT = "one_shot"
KIND_LOOP = "loop"

#: The general-purpose analysis window, used by width, flatness and as the floor for
#: chroma. 2048 at 16 kHz is 128 ms and 7.8 Hz per bin.
_WIDTH_FFT, _WIDTH_HOP = 2048, 1024
_WIDTH_MAX_FRAMES = 256      # bounded work: a 30 s file costs what a 3 s file costs


def frames(x, n_fft: int, hop: int, max_frames: int | None = None):
    """Windowed frames, optionally decimated to bound the work on long files."""
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    count = 1 + (len(x) - n_fft) // hop
    if count < 1:
        return np.zeros((0, n_fft), dtype=np.float32)
    starts = np.arange(count) * hop
    if max_frames and count > max_frames:
        starts = starts[np.linspace(0, count - 1, max_frames).astype(int)]
    idx = np.arange(n_fft)[None, :] + starts[:, None]
    return (x[idx] * np.hanning(n_fft)[None, :]).astype(np.float32)


def median_filter(x, win: int):
    """Moving median via a strided view. Pure numpy — no scipy in this project.

    Padded with ZEROS, not edge values. Edge padding replicates the first sample
    across half the window, so a large opening value becomes its own local median and
    thresholds itself out — which silently swallowed the onset at t=0, the one most
    one-shots have. Outside the signal there is no flux, and zero says exactly that.
    """
    if len(x) < win:
        return np.full_like(x, float(np.median(x)) if len(x) else 0.0)
    pad = win // 2
    padded = np.pad(x, (pad, pad), mode="constant", constant_values=0.0)
    strided = np.lib.stride_tricks.sliding_window_view(padded, win)
    return np.median(strided, axis=-1)[:len(x)]


# =====================================================================================
# ONSETS
# =====================================================================================

def onset_strength(mono):
    """The spectral-flux curve itself. Tempo wants the whole curve, not just the peaks."""
    padded = np.pad(mono, (_FLUX_FFT // 2, 0))
    frames_ = frames(padded, _FLUX_FFT, _FLUX_HOP)
    if len(frames_) < 2:
        return np.zeros(0)
    spec = np.abs(np.fft.rfft(frames_, axis=1))
    spec = np.vstack([np.zeros((1, spec.shape[1]), dtype=spec.dtype), spec])
    return np.maximum(0.0, np.diff(spec, axis=0)).sum(axis=1)


def onset_times(mono, sr: int = ANALYSIS_SR):
    """Onset positions in seconds, by spectral flux against a moving median."""
    # The frames behind this are CENTRED, and silence is prepended, in
    # ``onset_strength``. Two bugs die there. A Hann window is zero at its edges, so a
    # transient at sample 0 — where most one-shots put theirs — was multiplied away and
    # the file reported no onset at all. And timing a frame by its START rather than
    # its centre reported every onset half a window early, which showed up as a 35 ms
    # bias in exactly the intervals tempo is derived from.
    flux = onset_strength(mono)
    if not flux.size or not np.any(flux):
        return np.zeros(0)
    # An adaptive threshold alone is not enough: with no real onsets it collapses to
    # the noise floor and starts reporting it. A decaying sine — nothing whatsoever
    # happening after the first instant — produced five evenly spaced "onsets" that
    # way, and was routed as a loop. So a peak must ALSO be significant against the
    # largest rise in the file.
    threshold = np.maximum(median_filter(flux, _MEDIAN_WIN) + _FLUX_DELTA * flux.std(),
                           _FLUX_MIN_FRACTION * float(flux.max()))
    gap = max(1, int(_MIN_ONSET_GAP_S * sr / _FLUX_HOP))
    hits, last = [], -10 ** 6
    for i in np.nonzero(flux > threshold)[0]:
        # A peak, not merely a crossing: a rising slope alone fires several times
        # across one transient's attack.
        if i - last >= gap and (i == 0 or flux[i] >= flux[i - 1]):
            # Sub-frame position by parabolic interpolation of the flux peak. The hop
            # is 16 ms, and rounding to it put a 120 BPM loop at 117.2 — small per
            # onset, but tempo comes from the interval between them, so it lands
            # squarely in the digits a producer reads as the answer.
            offset = 0.0
            if 0 < i < len(flux) - 1:
                a, b, c = flux[i - 1], flux[i], flux[i + 1]
                denom = 2.0 * (a - 2.0 * b + c)
                if abs(denom) > 1e-12:
                    offset = float(np.clip((a - c) / denom, -0.5, 0.5))
            # Frame i now begins at sample i*hop (the silent frame absorbed the +1),
            # and a transient sits at the START of the frame that first sees it.
            hits.append(max(0.0, (i + offset) * _FLUX_HOP / float(sr)))
            last = i
    return np.asarray(hits)


def classify(onsets, duration: float | None) -> str:
    """One hit or a bar of them. The decision every other measurement hangs off.

    Count alone is not enough, and a real file showed why: an 808 bass drum whose
    decay ripples produced four "onsets" — all inside the first 340 ms of a 2.3 s
    file — and was routed as a loop, earning it a tempo of 96.9 BPM and a key of B
    major, both meaningless. A loop's onsets SPAN it; a one-shot's artefacts cluster
    at the front. So the span is required as well as the count.
    """
    count = len(onsets)
    if count <= ONE_SHOT_MAX_ONSETS:
        return KIND_ONE_SHOT
    spans = True
    if duration and count >= 2:
        spans = (float(onsets[-1] - onsets[0]) / duration) >= LOOP_MIN_SPAN
    if count >= LOOP_MIN_ONSETS and spans:
        return KIND_LOOP
    if duration is None:
        return KIND_ONE_SHOT
    return KIND_ONE_SHOT if duration < ONE_SHOT_SECONDS or not spans else KIND_LOOP


# =====================================================================================
# STEREO
# =====================================================================================

#: Width is measured ABOVE this, never broadband. Mono bass under a wide top end is
#: the standard production shape — bass is summed to mono to keep club systems
#: phase-aligned — so a broadband number averages that to "narrow" and hides the
#: thing being asked about.
_WIDTH_MIN_HZ = 250.0


def stereo(audio, sr: int):
    """(width above 250 Hz, broadband correlation). Both None if the file is mono.

    Width is reported as S / (M + S): 0 is a mono signal, 0.5 is equal mid and side
    energy. The unbounded S/M ratio a producer might name goes to infinity on
    out-of-phase material, which makes it useless to rank or threshold on.

    Correlation is kept alongside because it answers a DIFFERENT question — near -1
    means the channels fight and the mono sum partially cancels, which is a defect
    worth naming and which a width number alone never reveals.

    Measured on the SOURCE at its own rate, before any downmix — this is the one
    measurement that cannot be recovered from ``Prepared.mono``.
    """
    _require_numpy()
    if audio.ndim < 2 or audio.shape[1] < 2:
        return None, None
    left, right = audio[:, 0].astype(np.float32), audio[:, 1].astype(np.float32)
    correlation = None
    if float(left.std()) > 1e-9 and float(right.std()) > 1e-9:
        correlation = round(float(np.corrcoef(left, right)[0, 1]), 3)

    mid, side = (left + right) * 0.5, (left - right) * 0.5
    freqs = np.fft.rfftfreq(_WIDTH_FFT, 1.0 / sr)
    band = freqs >= _WIDTH_MIN_HZ
    if not band.any():
        return None, correlation
    m_frames = frames(mid, _WIDTH_FFT, _WIDTH_HOP, _WIDTH_MAX_FRAMES)
    s_frames = frames(side, _WIDTH_FFT, _WIDTH_HOP, _WIDTH_MAX_FRAMES)
    if not len(m_frames):
        return None, correlation
    m_energy = float((np.abs(np.fft.rfft(m_frames, axis=1))[:, band] ** 2).sum())
    s_energy = float((np.abs(np.fft.rfft(s_frames, axis=1))[:, band] ** 2).sum())
    total = m_energy + s_energy
    if total <= 1e-20:
        return None, correlation
    return round(s_energy / total, 4), correlation


# =====================================================================================
# ENVELOPE
# =====================================================================================

#: Rectify-and-smooth window for the amplitude envelope. 5 ms is short enough to keep
#: a drum transient's shape and long enough to stop a waveform's own zero crossings
#: from reading as an envelope.
_ENV_SMOOTH_S = 0.005


#: How close to the maximum still counts AS the maximum when timing the attack.
#: 0.1% is far below anything audible and far above float noise.
_PEAK_TOLERANCE = 0.001


def smoothed_envelope(mono, sr: int = ANALYSIS_SR):
    """|x| through a short moving average — the amplitude envelope everything else
    reads. Exposed separately because callers want more from it than attack and decay
    (whether a sound sustains, for one)."""
    env = np.abs(mono)
    win = max(1, int(_ENV_SMOOTH_S * sr))
    return np.convolve(env, np.ones(win, dtype=np.float32) / win, mode="same")


def envelope(mono, onsets, kind: str, sr: int = ANALYSIS_SR):
    """(attack_ms, decay_ms, peak_index) — both timed from the FIRST hit.

    ``attack_ms`` is time from the start of the file to the peak of the first hit, and
    it is the tag that serves DECISION-MAKING rather than search: a reverse cymbal or a
    chopped vocal can carry 40 ms of run-up, so a sample sequenced flat on the grid
    lands late. Knowing the offset lets the bridge move the note instead.

    It is scoped to the first hit rather than the whole file because the global peak of
    a loop is wherever its loudest hit happens to fall — real measurements of 1.0 s,
    1.2 s and 1.5 s "attack" on drum loops, which describe nothing about the attack.

    ``decay_ms`` is a T60 estimated from the first 20 dB of decay and multiplied by
    three — the standard T20 extrapolation. Measuring a full 60 dB directly would find
    the noise floor of most produced samples rather than the tail. It is returned as 0
    for anything with a second hit: what a loop decays to is the next hit, not silence.
    """
    env = smoothed_envelope(mono, sr)
    if not env.size or not np.any(env):
        return 0.0, 0.0, 0
    # Look for the peak only within the first hit — up to the second onset, if there
    # is one.
    limit = len(env)
    if len(onsets) > 1:
        limit = max(1, min(limit, int(onsets[1] * sr)))
    # FIRST time the envelope reaches its peak, not argmax's arbitrary winner.
    #
    # A sustained one-shot has a near-flat top, and argmax then decides on numerical
    # noise. `ORGAN9.wav` in this library has 69 envelope samples within 0.1% of its
    # maximum, spread over 1.13 s, and the gap between the top two is 3e-8 — so any
    # change to decoding moved its reported attack by a full second, and roughly 2% of
    # one-shots are shaped like that. The stored value was not reproducible.
    #
    # Taking the first sample that reaches the peak within a tolerance is both stable
    # AND the musically correct question: attack is when the sound ARRIVES at its
    # level, not when its single largest sample happens to fall.
    window = env[:limit]
    peak = float(window.max())
    if peak > 0:
        peak_idx = int(np.argmax(window >= peak * (1.0 - _PEAK_TOLERANCE)))
    else:
        peak_idx = 0
    attack_ms = round(peak_idx * 1000.0 / sr, 1)
    if kind == KIND_LOOP:
        # What a loop decays to is its next hit, not silence. Gating this on the ONSET
        # COUNT instead cost every two-onset one-shot its decay — a kick with an
        # audible click is still a kick that rings.
        return attack_ms, 0.0, peak_idx

    # The tail STOPS at the next onset. A one-shot may legitimately have two onsets —
    # a flam, a kick with an audible click, a double hit — and measuring decay through
    # the second one measures the second hit, not the first's ring-out. A double-hit
    # kick whose first hit decays in ~150 ms was reporting 386 ms, because the
    # envelope never fell 20 dB before the second impact lifted it again.
    tail = env[peak_idx:limit] if limit > peak_idx else env[peak_idx:]
    below = np.nonzero(tail <= peak * (10.0 ** (-20.0 / 20.0)))[0]
    if below.size:
        decay_ms = round(float(below[0]) * 3.0 * 1000.0 / sr, 1)
    else:
        # Never fell 20 dB inside the file: it is still ringing at the end, so the
        # honest answer is "at least this long", not an extrapolation off a slope
        # that was never observed.
        decay_ms = round(len(tail) * 1000.0 / sr, 1)
    return attack_ms, decay_ms, peak_idx


# =====================================================================================
# SPECTRAL FLATNESS
# =====================================================================================

def spectral_flatness(mono) -> float:
    """Geometric over arithmetic mean of the spectrum: ~1 is noise, near 0 is a tone.

    The cheap answer to "does this even HAVE a pitch". A hi-hat and a snare do not, and
    an estimator asked anyway will still return a number.
    """
    frames_ = frames(mono, _WIDTH_FFT, _WIDTH_HOP, _WIDTH_MAX_FRAMES)
    if not len(frames_):
        return 1.0
    spec = np.abs(np.fft.rfft(frames_, axis=1))
    geo = np.exp(np.mean(np.log(spec + 1e-12), axis=1))
    ari = np.mean(spec, axis=1) + 1e-12
    return float(np.mean(geo / ari))


# =====================================================================================
# CHROMA AND KEY
# =====================================================================================

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

#: HOW A KEY IS SPELLED, which is not how a NOTE is spelled.
#:
#: ``NOTE_NAMES`` is all sharps, and that is right for naming a pitch — "C#4" is a note
#: anyone reads. It is wrong for naming a KEY: it printed **"D# major"** for a piece in
#: E flat. D♯ major is a theoretical key of nine sharps that no musician writes or reads;
#: the name of that key is E♭ major. Reported to a producer, it is simply an error.
#:
#: The convention also differs between the two modes, which is why there are two tables:
#: pitch class 8 is A♭ MAJOR but G♯ MINOR, and pitch class 1 is D♭ major but C♯ minor.
#: Where both spellings are in real use the commoner one is chosen (F♯ major over G♭,
#: E♭ minor over D♯ minor, B♭ minor over A♯ minor).
KEY_NAMES_MAJOR = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
KEY_NAMES_MINOR = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B")


def key_name(root: int, mode: str) -> str:
    """The conventional name of a key — ``key_name(3, "major")`` is ``"Eb"``, not ``"D#"``."""
    table = KEY_NAMES_MINOR if mode == "minor" else KEY_NAMES_MAJOR
    return table[int(root) % 12]


def relative_key(root: int, mode: str) -> tuple:
    """The relative major of a minor key, or the relative minor of a major one.

    They share every pitch class, so a chroma histogram CANNOT tell them apart — C minor
    and E♭ major are the same seven notes and differ only in which is home. Correlation
    picks one and the margin between them is tiny; naming the other explicitly is more
    honest than presenting the winner as the answer.
    """
    if mode == "minor":
        return (int(root) + 3) % 12, "major"
    return (int(root) + 9) % 12, "minor"

#: Krumhansl-Schmuckler key profiles: how strongly each scale degree is weighted in a
#: tonal piece. Correlating a pitch-class histogram against all 24 rotations beats
#: naive "count the accidentals" because it uses EMPHASIS — a passing F# does not
#: outvote a tonic held for four bars. The same profiles serve MIDI (bins weighted by
#: note duration) and audio (bins weighted by chroma energy); the profiles do not care
#: where the weights came from, which is why this is one function and not two.
_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

#: How peaked a pitch-class histogram must be before a key is claimed, as
#: (max - min) / max. White noise measures about 0.07 through a corrected chroma; a
#: single held note approaches 1.0. Set low deliberately — the job is to refuse the
#: hopeless cases, not to second-guess genuinely weak tonality, which the margin
#: already reports.
MIN_TONAL_CONTRAST = 0.25

#: The chroma band. The bottom is A0 — the lowest note on a piano — and it used to be
#: A1, 55 Hz, which silently transposed a whole genre of bass up a perfect fifth.
#:
#: A pure square wave has NO even harmonics, and symmetrical clipping (saturation,
#: overdrive, anything used to make a sub audible on a phone speaker) generates odd
#: harmonics. So for a square or saturated sub at C1, the fundamental sat below the old
#: floor and was discarded, the second harmonic at C2 does not exist, and the loudest
#: thing left in band was the THIRD harmonic — a fifth above the root. Measured on
#: synthetic square subs at the old floor: C1 read G, D1 read A, F1 read C. Every one a
#: perfect fifth up, confidently, with no indication anything was missing. That is the
#: worst possible failure for a key estimator, because a fifth is the interval it is
#: already most likely to confuse.
#:
#: Sawtooth basses were fine throughout (1 error in 15), which is why this hid for so
#: long — a saw HAS a second harmonic, and an octave is the same pitch class.
#:
#: Lowering it is not free: at 27.5 Hz a semitone spans 1.6 Hz against 0.98 Hz bins, so
#: the bottom octave is coarse. Measured, the trade is one-sided — sub-bass errors fall
#: from 11/15 to 1/15 on sine and 13/15 to 1/15 on square, and the range from C2 up,
#: which is most of the library, is completely unaffected: 0 errors in 60 notes at every
#: floor tested. On 120 real bass/808/sub loops from the index, 2% change key.
_CHROMA_MIN_HZ, _CHROMA_MAX_HZ = 27.5, 5000.0
#: Chroma alone uses a LONGER window than the rest of the analysis. At 2048 samples
#: the bins are 7.8 Hz apart at 16 kHz, so the octave from A1 to A2 holds about seven
#: of them — twelve pitch classes cannot be separated inside seven bins, and the
#: bottom octave of a bass line is invisible to key detection. Zero-padding does not
#: help: it interpolates the same smeared peak. Only a longer window buys real
#: resolution. 16384 at 16 kHz is 1.02 s and just under 1 Hz per bin, which separates
#: semitones to the bottom of a piano.
_CHROMA_FFT = 16384


def chroma_of(mono, sr: int = ANALYSIS_SR) -> list:
    """12 pitch classes, resolved low enough to see a bass note.

    Each class is AVERAGED over its bins rather than summed. Linear bins are even in
    Hz while pitch classes are even in log frequency, so a high class covers far more
    bins than a low one — across 55-5000 Hz the count per class ranges 15 to 28, a 46%
    imbalance. Summing therefore measured bin count as much as music, which is how
    white noise came back as a confident D minor.

    THE WINDOW IS FIXED, and short input is zero-padded up to it by ``frames``. It used
    to shrink to the largest power of two below the signal length, which put a 0.5 s
    file on a 4096-point window and 3.9 Hz bins. That does not merely blur the answer —
    at the bottom of the range the semitones are closer together than the bins (A1 to
    Bb1 is 3.27 Hz), so whole pitch classes get no bin at all while their neighbours
    collect two semitones' worth.

    The padding is worth being precise about, because the obvious reading of it is
    wrong. Zero-padding buys NO resolution: a 0.5 s observation cannot separate two
    tones closer than about 2 Hz however much it is padded, and no amount of grid
    refinement invents information that was never observed. What it buys is GRID
    DENSITY, and that is what this function needs — every bin is assigned to a pitch
    class by rounding its own centre frequency, so a coarse grid mis-assigns energy at
    the class boundaries even when the underlying peak is perfectly well determined.
    Resolution separates chords; density places a note. Only the second is at stake
    here.
    """
    n_fft = _CHROMA_FFT
    frames_ = frames(mono, n_fft, n_fft // 2, _WIDTH_MAX_FRAMES)
    if not len(frames_):
        return [0.0] * 12
    spec = np.abs(np.fft.rfft(frames_, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    usable = (freqs > _CHROMA_MIN_HZ) & (freqs < _CHROMA_MAX_HZ)
    if not usable.any():
        return [0.0] * 12
    pcs = np.round(12 * np.log2(freqs[usable] / 440.0) + 69).astype(int) % 12
    weights = spec[:, usable].sum(axis=0)
    out = []
    for pc in range(12):
        sel = pcs == pc
        out.append(float(weights[sel].mean()) if sel.any() else 0.0)
    return out


def _correlate(hist, profile) -> float:
    """Pearson correlation, pure Python — see rule 3, this runs without numpy."""
    n = len(profile)
    mh, mp = sum(hist) / n, sum(profile) / n
    num = sum((hist[i] - mh) * (profile[i] - mp) for i in range(n))
    den = (sum((hist[i] - mh) ** 2 for i in range(n)) ** 0.5
           * sum((profile[i] - mp) ** 2 for i in range(n)) ** 0.5)
    return num / den if den else 0.0


def tonal_contrast(hist) -> float:
    """(max - min) / max over the 12 bins. 0.0 for an empty or silent histogram.

    Split out so a caller can tell "no energy at all" from "energy, but flat" and word
    its refusal accordingly — the two mean different things to a user.
    """
    if not hist or not any(hist):
        return 0.0
    peak = max(hist)
    return (peak - min(hist)) / peak if peak > 0 else 0.0


def key_scores(hist) -> list:
    """All 24 (correlation, root, mode) triples, best first. Empty list = no key.

    IS THERE ENOUGH CONTRAST TO ANSWER AT ALL? Krumhansl-Schmuckler always returns a
    winner, even from a histogram carrying no tonal information — so white noise came
    back as a confident D minor with a 0.229 margin and ``ambiguous: False``.
    Correlation is scale-invariant, which is a virtue everywhere except here: it
    cannot tell a flat distribution from a peaked one. Fixing the chroma was necessary
    and not sufficient; this gate is the other half.
    """
    if tonal_contrast(hist) < MIN_TONAL_CONTRAST:
        return []
    scored = []
    for root in range(12):
        rotated = list(hist[root:]) + list(hist[:root])
        for profile, mode in ((_MAJOR, "major"), (_MINOR, "minor")):
            scored.append((_correlate(rotated, profile), root, mode))
    scored.sort(reverse=True)
    return scored


#: The smallest margin over the runner-up that still means something. The bridge already
#: used 0.05 to flag a key "ambiguous"; naming it here stops the two from drifting and
#: lets the STORAGE path apply the same standard the display path does.
#:
#: It is not a tuning knob, it is a floor on arbitrariness. A drum-rack preview scored
#: G minor 0.2428 against F# minor 0.1944 — a margin of 0.048 among four candidates
#: bunched between 0.13 and 0.24 — and the winner flipped between G and E depending on
#: which resampler produced the signal. An answer that changes with the resampler is not
#: an answer, and storing it makes a drum kit searchable by a key it does not have.
MIN_KEY_MARGIN = 0.05


def key_from_chroma(hist) -> dict:
    """{key, scale, key_strength} — the flat shape the sample index stores.

    Returns ``{}`` when no key can honestly be claimed: too flat to carry one, or too
    close between the top two to mean anything.
    """
    scored = key_scores(hist)
    if not scored:
        return {}
    score, root, mode = scored[0]
    if score - scored[1][0] < MIN_KEY_MARGIN:
        return {}
    # key_name, not NOTE_NAMES: the index stored "D#" for keys that are called "Eb".
    return {"key": key_name(root, mode), "scale": mode,
            # Strength is the MARGIN over the runner-up, not the raw correlation.
            # C major and A minor share every note and both correlate highly; the gap
            # between them is what says whether the answer means anything.
            "key_strength": round(float(max(0.0, score - scored[1][0])), 3)}


# =====================================================================================
# TEMPO
# =====================================================================================

#: Tempo search range, and the prior it is weighted toward. Autocorrelation is
#: genuinely ambiguous between a pulse and its double or half, so SOMETHING has to
#: break the tie; a log-normal prior around 120 BPM is the standard choice.
_BPM_MIN, _BPM_MAX, _BPM_PRIOR = 60.0, 190.0, 120.0
#: Standard deviation of that prior, in OCTAVES. It was 1.0, which is the same as
#: having no prior at all: one sigma of a whole octave tells the search that a tempo
#: and its double are near enough equally fine, which is precisely the tie it exists
#: to break. Half-time was the single largest error class — 92 of 236 wrong tempos.
#:
#: ⚠️ THIS IS A BLUNT INSTRUMENT AND THE WIDTH IS A COMPROMISE, not an optimum. A
#: prior centred on 120 BPM inevitably favours material near 120: swept per tempo
#: band, 0.30 scores 53/83/47 (slow/mid/fast) and 0.80 scores 54/66/53 — tightening it
#: buys mid-tempo accuracy by taking it from drum-and-bass and trap, and loosening it
#: does the reverse. No value is good everywhere, because the prior is *assuming* an
#: answer rather than measuring one.
#:
#: 0.45 is chosen on the WORST BAND rather than the mean: it scores 54/81/50 for the
#: same 69% overall as 0.30, so it dominates it. Judging on the average is what let a
#: 36% collapse on fast material hide behind a headline that barely moved — Gemini
#: predicted that collapse from these multipliers alone, before it was measured.
#: The real fix is for the events-per-beat term to carry the octave decision so the
#: prior can be flattened; it is not strong enough to do that alone yet.
_BPM_PRIOR_WIDTH = 0.45

#: EVENTS PER BEAT — the term that actually separates a tempo from its half, since
#: duration cannot: a file that is 4 bars at 100 BPM is also exactly 8 bars at 200.
#:
#: Measured over 546 loops whose filenames state their tempo: at the TRUE tempo the
#: median is 2.10 events per beat (p25 1.50, p75 2.67); read at half-time it doubles
#: to 4.19. Sweeping the anchor, 1.5 picked the true tempo over its half on 88% of
#: them, against 78% at 2.0 and 49% at 3.0.
#:
#: ⚠️ THIS ANCHOR BELONGS TO THE DETECTOR, NOT TO MUSIC. A 16th-note groove has four
#: musical events per beat; `onset_times` counts only what clears `_FLUX_MIN_FRACTION`
#: of the largest rise, so it counts STRUCTURAL impacts — kicks, main snares, stabs —
#: and drops ghost notes, quiet hats and delay tails. Change that threshold or the
#: onset function and these numbers must be re-measured, or they will quietly mislead.
#:
#: The target SLOPES with tempo rather than being fixed. Fast music leans on half-time
#: phrasing (the snare on 3, not on 2 and 4), so it carries FEWER structural events per
#: beat than slow music, not more — visible in the same data: files named 140+ BPM
#: average 4.25 onsets/s (1.6 per beat) against 3.20 (2.3 per beat) for those named
#: under 100.
_EPB_AT_SLOW, _EPB_AT_FAST = 2.2, 1.5      # anchors at 85 and 170 BPM
_EPB_SLOW_BPM, _EPB_FAST_BPM = 85.0, 170.0
_EPB_WIDTH = 0.9                            # sigma in octaves; deliberately generous

#: Bar counts a sample-library loop is actually cut to. Powers of two, because that is
#: how loops are sold; odd lengths exist but guessing them costs more than it wins.
_BAR_CANDIDATES = (1, 2, 4, 8, 16, 32)
#: How far the autocorrelation estimate may sit from a whole-bar tempo and still be
#: snapped to it — a factor of 1.5 in either direction. Wide on purpose: the errors
#: this corrects are metrical (3/4, 4/3, 2/3 of the beat), not small.
_SNAP_MAX_LOG2 = 0.6


def _epb_target(bpm):
    """Expected structural events per beat at a given tempo — a sloped line."""
    frac = ((np.log2(np.asarray(bpm, dtype=float)) - np.log2(_EPB_SLOW_BPM))
            / (np.log2(_EPB_FAST_BPM) - np.log2(_EPB_SLOW_BPM)))
    return _EPB_AT_SLOW + frac * (_EPB_AT_FAST - _EPB_AT_SLOW)


def _density_likelihood(bpms, n_onsets: int, duration: float | None):
    """How plausible each candidate tempo's implied subdivision density is."""
    if not duration or duration <= 0 or n_onsets < 2:
        return np.ones(len(bpms))
    beats = duration * np.asarray(bpms, dtype=float) / 60.0
    epb = n_onsets / np.maximum(beats, 1e-9)
    return np.exp(-0.5 * (np.log2(epb / _epb_target(bpms)) / _EPB_WIDTH) ** 2)


def _metrical_margin(acf, lag: int) -> float:
    """1 - (strongest metrical rival / winner), over the half, double, third and 3/2.

    The confidence this replaces measured how PERIODIC a loop is, which is not the
    same question and turned out to be anti-correlated with being right: 53% accurate
    above 0.9 confidence against 65% below it. A perfectly quantised loop has a
    perfect half-time alias, so "very rhythmic" was being scored as "very certain"
    exactly where the tempo was most ambiguous.
    """
    if not 0 < lag < len(acf):
        return 0.0
    winner = float(acf[lag])
    if winner <= 1e-12:
        return 0.0
    rivals = []
    for factor in (0.5, 2.0, 1.0 / 3.0, 3.0, 1.5, 2.0 / 3.0):
        rival = int(round(lag * factor))
        if 0 < rival < len(acf) and abs(rival - lag) > 1:
            rivals.append(float(acf[rival]))
    if not rivals:
        return 1.0
    return float(np.clip(1.0 - max(rivals) / winner, 0.0, 1.0))


#: How close a file must sit to a whole bar count for that to count as evidence, in bars.
#: Beyond this the fit says nothing; the bonus fades to zero rather than falling off a
#: cliff, so no single threshold decides an answer.
_BAR_FIT_TOLERANCE = 0.05


def bar_fit(bpm: float, duration: float | None):
    """How nearly ``duration`` is a whole number of bars at ``bpm``. ``(bars, error)``.

    Separate from ``_snap_to_whole_bars`` because it MEASURES the fit without changing
    the tempo. A caller analysing arbitrary audio must not have its tempo pulled onto a
    bar grid — a genuine 90 BPM clip lasting a bar and a half would come back 120 — but
    the fit is still evidence, and throwing it away cost the confidence all of its
    meaning: a rendered mix read 96.1 BPM (8.008 bars, right) and a sustained pad read
    131.6 BPM (10.97 bars, wrong), and BOTH were reported at confidence 0.0.

    ⚠️ A good bar fit does NOT resolve the octave. 20 s is a whole number of bars at 96
    and at 192 alike, and those are different pieces of music — see ``_snap_to_whole_bars``
    on why a tempo and its double are not interchangeable. This corroborates the pulse,
    not which octave it sits in.
    """
    if not duration or duration <= 0 or bpm <= 0:
        return None, None
    bars = duration * bpm / 240.0
    nearest = round(bars)
    if nearest < 1:
        return None, None
    return int(nearest), abs(bars - nearest)


def _snap_to_whole_bars(bpm: float, duration: float | None):
    """Pull a tempo estimate onto the nearest tempo that makes the file whole bars.

    Sample-library loops are cut to a whole number of bars, so their LENGTH already
    encodes the tempo: at B BPM in 4/4, N bars last 240*N/B seconds. That is an
    independent measurement, and a far more precise one than autocorrelation.

    It settled a disagreement rather than assuming one: six loops whose filenames
    stated a BPM were being reported at exactly 4/3 of it, and checking their
    durations showed every one to be 4.000 bars at the STATED tempo. The filenames
    were right; the estimator was hearing a dotted-eighth layer as the beat.

    Returns the tempo and the BAR COUNT it implies, which is worth storing. A tempo
    and its double are NOT interchangeable — the same pulse written at 85 and at 170
    differs in note values, so the grid, the swing and every quantise decision differ
    with it. The bar count is the evidence for which of the two a file actually is,
    and storing it makes the claim checkable instead of asserted.
    """
    if not duration or duration <= 0:
        return bpm, None
    pairs = [(240.0 * n / duration, n) for n in _BAR_CANDIDATES]
    pairs = [(c, n) for c, n in pairs if _BPM_MIN <= c <= _BPM_MAX]
    if not pairs:
        return bpm, None
    best, bars = min(pairs, key=lambda p: abs(np.log2(p[0] / bpm)))
    if abs(np.log2(best / bpm)) <= _SNAP_MAX_LOG2:
        return best, bars
    return bpm, None


def tempo(onsets, flux=None, sr: int = ANALYSIS_SR, duration: float | None = None,
          snap_to_bars: bool = True) -> dict:
    """BPM by autocorrelation of the onset-strength curve.

    This replaced a median inter-onset interval, which measures the COMMONEST GAP
    rather than the beat — on a busy break that is a sixteenth, and the answer comes
    back at four times the tempo or some unrelated fraction of it. Filenames carrying
    their own BPM made the failure impossible to miss: a file named `...-170bpm` was
    reported at 120, and one named `120 BPM` at 156.

    Autocorrelation asks the right question — at what lag does the whole envelope best
    resemble itself — so a break with sixteenths still correlates strongest at its
    beat. The inter-onset median stays as a fallback for material too short to
    autocorrelate.
    """
    frames_per_sec = sr / float(_FLUX_HOP)
    if flux is not None and len(flux) >= 8:
        x = flux - flux.mean()
        n = 1 << int(np.ceil(np.log2(len(x) * 2)))
        spectrum = np.fft.rfft(x, n=n)
        acf = np.fft.irfft(spectrum * np.conj(spectrum))[:len(x)]
        # UNBIASED: divide by the number of overlapping terms at each lag. Raw
        # autocorrelation has fewer products to sum as the lag grows, so it slopes
        # downward on its own and systematically prefers short lags — fast tempos.
        # The signature was unmistakable once filenames were used as ground truth:
        # six loops came back at exactly 4/3 of their stated BPM, which is the beat
        # period shortened by one notch, not a musical relationship.
        acf = acf / np.maximum(np.arange(len(x), 0, -1), 1)
        if acf[0] > 0:
            acf = acf / acf[0]
            lo = max(2, int(round(frames_per_sec * 60.0 / _BPM_MAX)))
            hi = min(len(acf) - 2, int(round(frames_per_sec * 60.0 / _BPM_MIN)))
            if hi > lo:
                lags = np.arange(lo, hi + 1)
                bpms = 60.0 * frames_per_sec / lags
                prior = np.exp(-0.5 * (np.log2(bpms / _BPM_PRIOR)
                                       / _BPM_PRIOR_WIDTH) ** 2)
                # Score each candidate by its HARMONICS, not by its own peak alone. A
                # real beat repeats at 2x, 3x and 4x its period, so support accumulates
                # there; a spurious peak has none. Without this, a dotted-eighth layer
                # — the 3-3-2 pattern all over electronic music — correlates strongly
                # at 3/4 of the beat, and a whole pack of loops came back at exactly
                # 4/3 of the tempo their own filenames stated.
                harmonic = np.zeros(len(lags))
                for k, weight in ((1, 1.0), (2, 0.5), (3, 0.33), (4, 0.25)):
                    idx = lags * k
                    valid = idx < len(acf)
                    harmonic[valid] += weight * acf[idx[valid]]
                # Three multiplied terms, not one deciding: how periodic the envelope
                # is, whether the tempo is one a human would tap, and whether the
                # implied subdivision density is plausible. A SOFT weight on purpose —
                # as a hard gate, one missed kick would punt a file into the wrong
                # octave over an otherwise flawless autocorrelation peak.
                scored = harmonic * prior * _density_likelihood(bpms, len(onsets),
                                                                duration)
                best = int(np.argmax(scored))
                lag = lags[best]
                # Sub-frame refinement, same reason as everywhere else here: the lag
                # quantises to 16 ms and tempo is derived from it.
                if 0 < lag < len(acf) - 1:
                    a, b, c = acf[lag - 1], acf[lag], acf[lag + 1]
                    denom = 2.0 * (a - 2.0 * b + c)
                    if abs(denom) > 1e-12:
                        lag = lag + float(np.clip((a - c) / denom, -0.5, 0.5))
                bpm = 60.0 * frames_per_sec / float(lag)
                # SNAPPING IS THE CALLER'S CALL, because it encodes an assumption
                # about the material rather than a fact about the signal: that the
                # file is a whole number of bars. That is true of a sample-library
                # loop and is what took tempo accuracy from 57% to 69%. It is NOT
                # true of an arbitrary recording, and there it does damage — a
                # genuine 90 BPM clip lasting 1.5 bars gets pulled to 120, because
                # 120 is what a whole number of bars in that duration would mean.
                bpm, bars = (_snap_to_whole_bars(bpm, duration) if snap_to_bars
                             else (bpm, None))
                if _BPM_MIN <= bpm <= _BPM_MAX:
                    # Confidence needs BOTH a strong pulse and an absence of metrical
                    # ambiguity, so the two are multiplied. Prominence alone answered
                    # "how rhythmic is this", which is why it pointed the wrong way.
                    band = acf[lo:hi + 1]
                    peak = float(band[best])
                    spread = float(band.std()) or 1e-9
                    clarity = (peak - float(band.mean())) / spread
                    prominence = min(1.0, max(0.0, clarity / 3.0))
                    # Measured on the SCORED candidates, not the raw autocorrelation.
                    # The raw version was the honest-looking choice — evidence only,
                    # no prior double-counted — and it produced a confidence that was
                    # merely flat instead of inverted: a clean loop has a genuinely
                    # strong half-time alias in the ACF, so almost everything scored
                    # near zero. What a caller needs to know is how sure the system is
                    # GIVEN everything it knows, and if the prior legitimately rules a
                    # rival out then the answer really is more certain.
                    # Score a WIDER lag range than the tempo search itself, purely so
                    # the rivals can be looked up. The search is bounded to 60-190 BPM,
                    # but a rival at half or double the winner routinely falls outside
                    # that: at 100 BPM the winner sits at lag 38 and its half-time
                    # rival at 76, past the window's end of 62. Those rivals were being
                    # read as zero, making the margin a perfect 1.0 and the confidence
                    # highest exactly where the ambiguity was worst — for every tempo
                    # below about 120 BPM, which is most of a sample library.
                    #
                    # ⚠️ THIS IS THE FIX THAT WAS MADE IN ONE PROGRAM AND UNDONE IN THE
                    # OTHER TWO HOURS LATER, comment and all. It is the reason this
                    # file exists. If you are reading it in a copy, do not edit the
                    # copy — see the header.
                    wide_lo = max(2, lo // 3)
                    wide_hi = min(len(acf) - 2, hi * 3)
                    wide_lags = np.arange(wide_lo, wide_hi + 1)
                    wide_bpms = 60.0 * frames_per_sec / wide_lags
                    wide_harmonic = np.zeros(len(wide_lags))
                    for k, weight in ((1, 1.0), (2, 0.5), (3, 0.33), (4, 0.25)):
                        idx = wide_lags * k
                        valid = idx < len(acf)
                        wide_harmonic[valid] += weight * acf[idx[valid]]
                    posterior = np.zeros(len(acf))
                    posterior[wide_lags] = wide_harmonic * _density_likelihood(
                        wide_bpms, len(onsets), duration)
                    confidence = prominence * _metrical_margin(posterior,
                                                               int(lags[best]))
                    fit_error = None
                    if bars:
                        # Two independent lines of evidence agreeing — the envelope's
                        # own periodicity and the file's length — is worth more than
                        # either alone.
                        confidence = min(1.0, confidence + 0.1)
                    else:
                        # NOT SNAPPING, but the length is still evidence. Without this the
                        # confidence was 0.0 for a correct 96.1 and for a wrong 131.6
                        # alike, which is no signal at all. Graded by how well it fits, so
                        # no threshold decides the answer on its own.
                        bars, fit_error = bar_fit(bpm, duration)
                        if bars and fit_error is not None:
                            confidence = min(1.0, confidence + 0.5 * max(
                                0.0, 1.0 - fit_error / _BAR_FIT_TOLERANCE))
                        else:
                            bars = None
                    out = {"bpm": round(bpm, 1),
                           "bpm_confidence": round(confidence, 2),
                           "bars": bars}
                    if fit_error is not None:
                        # Reported so a caller can see WHY the confidence moved, and so a
                        # bad fit is visible rather than merely producing a low number.
                        out["bar_fit_error"] = round(fit_error, 3)
                    return out

    if len(onsets) < 4:
        return {}
    iois = np.diff(onsets)
    iois = iois[iois > 0.04]
    if iois.size < 3:
        return {}
    median = float(np.median(iois))
    if median <= 0:
        return {}
    spread = float(np.median(np.abs(iois - median)) / median)
    bpm = 60.0 / median
    while bpm < _BPM_MIN:
        bpm *= 2
    while bpm > _BPM_MAX:
        bpm /= 2
    return {"bpm": round(bpm, 1),
            "bpm_confidence": round(max(0.0, 1.0 - spread * 2.0), 2)}


# =====================================================================================
# LOUDNESS
# =====================================================================================

def loudness_lufs(audio, sr: int):
    """Integrated loudness, BS.1770-4 gating. Measured on the SOURCE, all channels.

    K-weighting is applied as a zero-phase filter in the frequency domain rather than
    as the specified cascade of two biquads run sample by sample. A Python per-sample
    biquad loop is fine for one clip on demand and costs more than everything else here
    combined across 30,000 files. Block mean-square energy is unaffected by the phase
    difference, which is all the gating uses — verified against the standard's own
    anchor (a 1 kHz sine at -20 dBFS in both channels reads -20 LUFS).
    """
    _require_numpy()
    if audio.ndim < 2:
        audio = audio[:, None]
    block, hop = int(0.4 * sr), int(0.1 * sr)
    if hop < 1 or len(audio) < block:
        return None                                   # shorter than one 400 ms block
    freqs = np.fft.rfftfreq(len(audio), 1.0 / sr)
    gain = _k_weight_response(freqs, sr)
    z = None
    for ch in range(audio.shape[1]):
        filtered = np.fft.irfft(np.fft.rfft(audio[:, ch]) * gain, n=len(audio))
        squared = np.concatenate(([0.0], np.cumsum(filtered ** 2)))
        # Block starts by arange rather than (n - block) // hop: the latter counts the
        # GAPS between block starts, not the blocks, and dropped the last whole block.
        # Measured 0.49 LU on material that ends louder than it starts.
        starts = np.arange(0, len(audio) - block + 1, hop)
        mean_sq = (squared[starts + block] - squared[starts]) / block
        z = mean_sq if z is None else z + mean_sq
    if z is None or not len(z):
        return None
    lk = -0.691 + 10 * np.log10(z + 1e-12)
    gated = z[lk > -70.0]                             # absolute gate
    if not len(gated):
        return None
    relative = -0.691 + 10 * np.log10(gated.mean() + 1e-12) - 10.0
    final = gated[(-0.691 + 10 * np.log10(gated + 1e-12)) > relative]
    if not len(final):
        return None
    return round(float(-0.691 + 10 * np.log10(final.mean() + 1e-12)), 1)


def _biquad_response(freqs, sr: int, b, a):
    """|H| of a digital biquad on a frequency grid, from the same coefficients the
    time-domain filter would run."""
    z = np.exp(-2j * np.pi * freqs / sr)
    return np.abs((b[0] + b[1] * z + b[2] * z ** 2)
                  / (1.0 + a[1] * z + a[2] * z ** 2))


def _k_weight_response(freqs, sr: int):
    """Magnitude response of the BS.1770-4 K-weighting pair.

    The DIGITAL coefficients are built exactly as a time-domain port would build them,
    then evaluated on the grid. An earlier version sampled hand-derived ANALOGUE
    transfer functions instead, which looked right and was wrong by 3-4 LU — and wrong
    by a different amount at every frequency, so no single check would have caught it.
    Deriving the response from the filter the standard actually specifies removes the
    opportunity. The odd-looking constants are the standard's own, to full precision;
    rounding them moves the answer.
    """
    # High-shelf pre-filter: +4 dB above ~1.68 kHz.
    f0, gain_db, q = 1681.9744509555319, 3.99984385397, 0.7071752369554193
    k = np.tan(np.pi * f0 / sr)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = _biquad_response(
        freqs, sr,
        ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0,
         (vh - vb * k / q + k * k) / a0),
        (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0))
    # RLB high-pass at ~38 Hz.
    f0, q = 38.13547087602444, 0.5003270373238773
    k = np.tan(np.pi * f0 / sr)
    a0 = 1 + k / q + k * k
    highpass = _biquad_response(
        freqs, sr, (1 / a0, -2 / a0, 1 / a0),
        (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0))
    return shelf * highpass


# =====================================================================================
# THE ONE ENTRY POINT
# =====================================================================================

def measure(prepared: Prepared, snap_to_bars: bool = True) -> dict:
    """Every measurement both programs share, from one prepared signal.

    Takes a ``Prepared`` and nothing else — rule 1. Callers add what only they need:
    the listener routes a one-shot to its YIN pitch estimator, the bridge adds
    centroid, crest and its plain-language labels. Neither re-derives what is here.

    ``kind`` is computed because tempo and key are only asked of a loop. Computing
    both for everything is not merely wasteful, it fills the output with confident
    junk — a BPM derived from one transient, a "key" for an atonal breakbeat — and a
    consumer downstream has no way to tell that from a real measurement.
    """
    _require_numpy()
    if not isinstance(prepared, Prepared):
        raise TypeError(
            "measure() takes a Prepared from shared_dsp.prepare(audio, sample_rate). "
            "Passing a raw array would mean the caller chose its own resampling and "
            "mono-summing, which is how the two programs drifted apart before this "
            "module existed — see rule 1 in the module docstring.")
    mono, duration = prepared.mono, prepared.duration
    onsets = onset_times(mono)
    kind = classify(onsets, duration)
    attack_ms, decay_ms, peak_idx = envelope(mono, onsets, kind)
    width, correlation = stereo(prepared.source, prepared.sample_rate)

    out = {"kind": kind, "onsets": int(len(onsets)),
           "attack_ms": attack_ms,
           "decay_ms": decay_ms or None,
           "flatness": round(spectral_flatness(mono), 4),
           "stereo_width": width, "stereo_correlation": correlation,
           "loudness_lufs": loudness_lufs(prepared.source, prepared.sample_rate),
           # Not stored anywhere, but the caller's own estimators need it: the
           # listener's pitch guard starts measuring just past the first peak.
           "peak_index": peak_idx}
    if kind == KIND_LOOP:
        # A loop gets the question asked the other way round: not "what note", but
        # "what key" — which is what a producer needs before dropping it into a set.
        out.update(tempo(onsets, onset_strength(mono), duration=duration,
                         snap_to_bars=snap_to_bars))
        out.update(key_from_chroma(chroma_of(mono)))
    return out
