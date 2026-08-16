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

## Attribution — 2026-08-16

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
