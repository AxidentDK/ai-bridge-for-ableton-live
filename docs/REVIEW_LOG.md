# Peer review log — who found what

Kim's question, and it was the right one to ask: *"the review seems now to be you who
review your own code… I cannot see if you actually chat with Gemini."*

He was right that the process had become invisible. This file exists so the answer is
checkable rather than asserted. **Attribution matters**, because it decides whether the
Gemini step is worth paying for.

## How the review actually runs

1. **Gemini reviews the file.** `tools/ask_gemini.py` sends whole modules over the API.
   Every reply is saved verbatim to `gemini_reviews/` (gitignored — the replies quote
   source). Nothing below is paraphrased from memory; the files are on disk.
2. **Every claim is verified against the code before it is believed**, with a script
   that proves or disproves it where possible. This step is not ceremony — see the
   rejected column.
3. **Only verified findings are fixed**, and the fix cites the evidence.

The review moved from the browser to the API for one reason: 9,200 lines is ~100 pasted
chunks, and roughly a third of browser messages vanished silently when the page dropped
its connection. The cost is that Kim can no longer read the exchange as it happens.
**Design-level questions should go back to the browser chat**, where he can follow them;
the API is for bulk file review.

## Session 2 — 2026-08-16 afternoon: shared_dsp.py, reviewed conversationally

The first session was one-shot: send a module, get an answer. This one was a
conversation over `tools/gemini_chat.py`'s transport, which changed what was possible —
each of us could challenge the other's reasoning and be shown data in reply. Full
transcript in `gemini_reviews/20260816-145823-review.md`.

**Gemini was right about, and I was wrong:**

| Claim | Outcome |
|---|---|
| Bar-snapping would break on arbitrary audio | Correct, from the architecture alone, before seeing the code — and it was a real bug found hours earlier |
| Square/saturated subs are transposed up a fifth by the 55 Hz chroma floor | **Correct, and the find of the day.** Named the mechanism from the constant. Verified: C1→G, D1→A, F1→C |
| Lowering the floor is worth the coarseness | Correct. I argued it would degrade the working range; measured, the range above C2 is untouched — 0/60 errors at every floor |
| Brightness at native rate is not an exception to rule 1 | Correct. `Prepared.source` exists for exactly this, and its docstring says so |

**I was right about, and Gemini was wrong:**

| Claim | Evidence that settled it |
|---|---|
| The 0.15 flux floor strips hi-hats and votes half-time | Refuted on 134 real loops: the floor keeps 95.1% of onsets, and accuracy in the 118–135 BPM band is *identical* at every floor value. Flux is spectral difference, not amplitude — a quiet hat is broadband and survives |
| 16 kHz flattens cymbal timbre | Refuted on 196 drum one-shots: separation *improves*, 0.583→0.650 within the cymbal family. Above 8 kHz is mostly per-sample noise |
| His chroma patch (`min`→`max`) | Would have removed the cap, not added a floor: a 30 s loop analysed with a 16-second window. Narrow bug turned into a broad one. He conceded immediately |
| His thresholds 600/2000/3800 | Scored 72.8% — exactly what changing nothing scores. Read off drums; the library is not drums |

**Found only because a wrong prediction pointed a measurement at the right place:** the
16 kHz brightness regression. Chasing Gemini's (refuted) timbral claim meant measuring
absolute centroids, which showed 27% of 500 files had been relabelled that morning and
"very bright" had lost 75% of its members — my own bug, from that day, invisible in a
diff because both numbers are plausible.

**Open, and honestly unresolved.** Gemini predicted the real-library key changes would
spike at a perfect fifth down, which would prove the mechanism outside synthesis. On 400
real bass loops, 23 changed key, and the spike is at a **major third down (10)**, not a
fifth (4). Both are odd-harmonic intervals, so the odd-harmonic story survives — the 5th
harmonic is a major third — but the specific prediction is wrong and n=23 is too small
to lean on. Worth revisiting.

## Attribution — 2026-08-16 (session 1)

### Gemini found these

| Finding | Impact |
|---|---|
| Perceptual prior collapsing FAST genres | Predicted from the multipliers alone (0.246 at 170 BPM) *before* measurement; ≥140 BPM accuracy was **36%** behind a 69% headline |
| Support FFT too coarse to confirm a bass note | 4 of 36 notes C0–B2 had no bin within a semitone |
| Decay leaking past a second onset | A ringing 808 reported 5,625 ms instead of 275 |
| Metrical rivals invisible outside the search window | Confidence highest where ambiguity was worst |
| One-shot guard keyed on duration alone | Stripped genre from 26% of loops (1-bar loops are < 2.048 s) |
| Tempo from median inter-onset interval | Octave-wrong at full confidence |
| `add9` chord unreachable | Key written `(0,4,7,14%12)`, lookup builds sorted tuples |
| Averaging embeddings rather than predictions | Non-linear heads do not commute with a mean; 2.1% of verdicts flip |

Its musical judgement is the genuine value: the fast-genre prediction came from
reasoning about the maths, with no access to the data.

### Verification found these — Gemini did NOT raise them

| Finding | Impact |
|---|---|
| **`lastrowid` after ON CONFLICT DO UPDATE** | **CRITICAL.** Every re-analysis wrote tags, properties and embeddings against a bogus id — 581,789 orphaned tags — while stamping the file as freshly analysed. Gemini's `db.py` review instead led with an `-inf`/NaN JSON theory, which verification disproved (0 infinities in 42k rows) |
| `PRAGMA foreign_keys` never enabled | The guardrail that would have caught the above on its first row |
| Spectral centroid destroyed by silence | Same tone reads "bright" or "warm" depending on trailing silence |
| `band_energies` sums instead of averaging | 20.3 dB apparent tilt that is purely bin count |
| `record_takes` leaves Live ARMED and recording on error | Next play records over the user's material |
| `scale_fold` writes pitches outside 0..127 | Other callers clamp; this one did not |
| Cleanup could delete an empty GROUP track | Live takes the children with it |

### Verification REJECTED these Gemini claims

Each was checked and found untrue of this code. Acting on them would have damaged
working code:

* "LUFS is sample-rate blind / hardcoded coefficients" — coefficients are derived per
  rate; **K-weighting reproduces BS.1770-4 to 12+ significant figures**, and the
  standard's own calibration anchor passes at −20.037 LUFS.
* "Krumhansl-Schmuckler is applied wrongly" — correct; verified on unambiguous MIDI.
* "float16 blobs can be silently truncated" — bit-exact round-trip; all 41,394 live
  rows have `LENGTH(vector) = dim*2`.
* "`-inf` loudness will kill the MCP session" — every log is floored; 0 infinities.
* "SQLite read-only URI will fail on Windows" — already used, already working.
* "Observer registry leaks / needs a sweep" — teardown covered on unobserve, disconnect
  and shutdown.
* "`_to_db` meter conversion is dangerously wrong" — the code never claims dBFS and says
  so; the proposed replacement curve was asserted with no source.
* "Folder-name pollution dominates text search" — 0 of the top 20 for five real queries.

## The lesson worth keeping

**A confident review is not evidence.** Gemini was right about things I would not have
seen, and wrong about things that would have cost real work — in both directions,
stated with equal confidence. The verification step is what separates them, and it is
also where the worst bug of the night was found.

Neither half is sufficient alone.
