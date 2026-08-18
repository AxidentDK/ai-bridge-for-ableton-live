"""Warp markers — the third thing that cannot be done through the generic primitives.

``clip.add_warp_marker`` will not accept a dict, a tuple, a list, or two floats. Live
wants a real ``Live.Clip.WarpMarker``, and says so in the only way it knows how:

    No registered converter was able to produce a C++ rvalue of type
    NApiHelpers::TWarpMarker from this Python object of type dict

So the object has to be constructed in-process, where ``Live`` is importable. Same shape
of problem as ``notes.py`` and ``envelopes.py``, and the same cure: the factory is INJECTED
by ``bridge.py`` rather than imported here, which keeps this module pure logic and
unit-testable with fakes outside Live.

READING needs its own path too. ``clip.warp_markers`` serialises to
``{"type": "WarpMarker"}`` with no fields over the wire — the values are only reachable by
walking into each marker, so this returns them flattened. Verified against Live 12.4.3: a
freshly imported loop has two markers, at (0.0, 0.0) and (0.010416, 0.015625).

⚠️ **``sample_time`` IS IN SECONDS, NOT SAMPLES**, despite the name — the single most
misleading thing here. Passing 22050 for "half a second at 44.1 kHz" gets you
*"Warp marker sample time is out of range."*, because 22050 seconds is six hours into a
five-second file. The giveaway is in the defaults: a fresh loop's second marker reads
``sample_time 0.010416`` against ``beat_time 0.015625``, which at 90 BPM is exactly the
same instant expressed in seconds.

THREE LIVE BEHAVIOURS WORTH KNOWING, all found by trying:

* **The last marker is a "shadow" marker** and Live refuses to move it —
  *"The shadow marker can't be moved."* It is Live's implicit end-of-sample anchor rather
  than something the user placed, so this reports it as such instead of passing the raw
  error up.
* **Toggling ``warping`` makes Live re-analyse the sample on its main thread**, and calls
  made during that window time out. Warp changes are applied LAST here, after the reads,
  so a caller that both edits and inspects gets its answer before the stall rather than
  losing the whole request to it.
"""
from __future__ import annotations

from .lom import LomError, resolve


def _require_audio_clip(roots, path):
    clip = resolve(roots, path)
    if not hasattr(clip, "warp_markers"):
        raise LomError("wrong_type",
                       f"{type(clip).__name__} at {path!r} has no warp markers")
    if not getattr(clip, "is_audio_clip", True):
        raise LomError("wrong_type",
                       f"{path!r} is a MIDI clip — only audio clips are warped")
    return clip


def _read_markers(clip, limit=None):
    """Flatten the marker objects into ``{sample_time, beat_time}`` dicts.

    ``last_is_shadow`` is reported alongside because the caller cannot tell from the
    numbers alone, and moving that one is an error rather than a no-op.
    """
    markers = list(getattr(clip, "warp_markers", []) or [])
    out = []
    for marker in markers:
        out.append({"sample_time": float(getattr(marker, "sample_time", 0.0)),
                    "beat_time": float(getattr(marker, "beat_time", 0.0))})
    total = len(out)
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out, total


def warp_markers(roots: dict, path: str, marker_factory, *, warping=None,
                 warp_mode=None, add=None, move=None, remove=None, limit=None) -> dict:
    """Inspect and edit one audio clip's warp state. Every argument is optional.

    Order is deliberate: removes, then moves, then adds, then the read, and only then the
    warping/warp_mode writes — see the module docstring on re-analysis stalling the main
    thread. Each edit reports its own outcome instead of aborting the request, because a
    caller adding eight markers should not lose seven of them to one bad beat time.
    """
    clip = _require_audio_clip(roots, path)
    applied: list = []

    for beat_time in (remove or []):
        try:
            clip.remove_warp_marker(float(beat_time))
            applied.append({"remove": float(beat_time), "ok": True})
        except Exception as exc:                                       # noqa: BLE001
            applied.append({"remove": float(beat_time), "ok": False, "error": str(exc)})

    for item in (move or []):
        beat_time = float(item["beat_time"])
        delta = float(item.get("beat_time_delta", item.get("delta", 0.0)))
        try:
            clip.move_warp_marker(beat_time, delta)
            applied.append({"move": beat_time, "by": delta, "ok": True})
        except Exception as exc:                                       # noqa: BLE001
            message = str(exc)
            if "shadow" in message.lower():
                message = ("that is Live's shadow marker (the implicit end-of-sample "
                           "anchor) and cannot be moved")
            applied.append({"move": beat_time, "by": delta, "ok": False,
                            "error": message})

    for item in (add or []):
        try:
            marker = marker_factory(float(item["sample_time"]), float(item["beat_time"]))
        except Exception as exc:                                       # noqa: BLE001
            applied.append({"add": item, "ok": False,
                            "error": f"could not build a WarpMarker: {exc}"})
            continue
        try:
            clip.add_warp_marker(marker)
            applied.append({"add": {"sample_time": float(item["sample_time"]),
                                    "beat_time": float(item["beat_time"])}, "ok": True})
        except Exception as exc:                                       # noqa: BLE001
            applied.append({"add": item, "ok": False, "error": str(exc)})

    markers, total = _read_markers(clip, limit)
    result = {
        "clip": path,
        "warping": bool(getattr(clip, "warping", False)),
        "warp_mode": getattr(clip, "warp_mode", None),
        "available_warp_modes": list(getattr(clip, "available_warp_modes", []) or []),
        "sample_rate": getattr(clip, "sample_rate", None),
        "sample_length": getattr(clip, "sample_length", None),
        "marker_count": total,
        "markers": markers,
        "last_is_shadow": total > 0,
    }
    if applied:
        result["applied"] = applied

    # LAST, for the reason in the module docstring: these can stall the main thread while
    # Live re-analyses, and everything above is already computed by now.
    if warp_mode is not None:
        available = result["available_warp_modes"]
        if available and int(warp_mode) not in available:
            raise LomError("bad_request",
                           f"warp_mode {warp_mode} is not available for this clip; "
                           f"available: {available}")
        clip.warp_mode = int(warp_mode)
        result["warp_mode"] = clip.warp_mode
    if warping is not None:
        clip.warping = bool(warping)
        result["warping"] = bool(warping)
        result["note"] = ("changing `warping` makes Live re-analyse the sample on its "
                          "main thread; the next call may time out — retry it")
    return result
