# LOM recipes — things the object model does not do the obvious way

Working notes for operations where the Live Object Model's naming misleads, or
where the answer needs two or three calls in a specific order. Each one here was
established by experiment against a running Live, not read from a manual.

Companion to [LOM_REFERENCE.md](LOM_REFERENCE.md), which lists *what* is
reachable. This page covers *how* for the awkward cases.

---

## Passing object handles to `live_call`

Several LOM functions take another LOM object as an argument — `Track.delete_clip(clip)`,
`Track.duplicate_clip_to_arrangement(clip, time)`, `Song.move_device(...)`. A raw
id or a path string is rejected by the C++ signature:

```
Python argument types in Track.duplicate_clip_to_arrangement(Track, int, int)
did not match C++ signature:
    duplicate_clip_to_arrangement(TTrackPyHandle self, TPyHandle<AClip> clip, double destination_time)
```

Wrap the path in a `$ref` object instead — the bridge resolves it to a live handle:

```json
{
  "path": "live_set tracks 1",
  "func": "duplicate_clip_to_arrangement",
  "args": [{ "$ref": "live_set tracks 1 arrangement_clips 0" }, 24]
}
```

Works in `live_call` and inside `live_batch` ops alike.

---

## Trimming an arrangement clip's left edge

**The trap:** `Clip.start_time` is read-only for arrangement clips, and
`start_marker` is *not* the clip's left edge — it slides the **content** inside a
clip whose arrangement position stays put. Setting `start_marker` alone therefore
moves the music earlier while the clip sits where it was.

**Second trap:** MIDI files imported into the Arrangement arrive with
`looping = true`. While Loop is on, `start_marker` changes nothing visible at all —
the loop brace governs the played region. Loop must go off before any of this
behaves.

The relationship that matters:

| | |
|---|---|
| `start_time` | where the clip's left edge sits on the arrangement timeline (read-only) |
| `start_marker` | where playback starts *within the clip's own content* |
| `start_time == start_marker` | content time maps 1:1 onto arrangement time — notes sound where their clip-internal times say |

So to trim a clip's left edge to beat `F` **without moving a single note**, keep
that equality true: set the content window to start at `F`, then place a copy of
the clip at `F`, then drop the original.

```
set  clip.start_marker = F
set  clip.looping      = false
call track.duplicate_clip_to_arrangement({$ref: clip}, F)
call track.delete_clip({$ref: clip})        # original is index 0 — it starts earliest
```

The duplicate inherits name, colour and content window, so it lands at `F` with
`start_time == start_marker == F`. Name and colour survive; verified.

Batches cleanly — four ops per clip, all eight tracks of a set in two
`live_batch` calls.

**Verifying afterwards:** read `start_time` and `start_marker` on every clip. If
they are equal, no note moved. That single equality is a stronger check than
re-reading note times, because clip-internal note times do not change when a clip
is repositioned — only the mapping does.

**Check `has_envelopes` first.** This recipe recreates the clip, so any clip
envelopes (MIDI CC drawn inside the clip) are lost. `false` means notes are all
there is and it is safe.

---

## Right edges after a MIDI import

Cubase writes each track's Standard MIDI File so the last event lands exactly at
the track end, so imported clips already end on their final note — `end_time`
needs no adjustment. Only the left edge carries the leading silence, because
every track in a type 1 file starts at tick 0 regardless of when it first plays.
