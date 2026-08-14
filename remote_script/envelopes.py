"""Clip automation envelopes — the second convenience that must live in-process.

``clip.automation_envelope(parameter)`` returns an ``AutomationEnvelope``
*object*; objects returned from calls carry no wire-addressable path, so the
envelope's ``insert_step`` / ``value_at_time`` are unreachable through the
generic primitives. Same shape of problem as ``notes.py``, same cure: a thin
in-process bridge, resolved per call, nothing cached.

Pure logic: no ``Live`` import, unit-testable with fakes.
"""
from __future__ import annotations

from .lom import LomError, resolve


def _require_clip(roots, path):
    obj = resolve(roots, path)
    if not hasattr(obj, "automation_envelope"):
        raise LomError("wrong_type",
                       f"{type(obj).__name__} at {path!r} has no automation envelopes")
    return obj


def _envelope(roots, clip_path, parameter_path, create=False):
    """Fetch a clip's envelope for a parameter; optionally create it.

    Live quirk (found live-validating): ``automation_envelope`` returns None
    until an envelope EXISTS for that parameter — writing needs
    ``create_automation_envelope`` (Live 11+) first. Reading never creates.
    """
    clip = _require_clip(roots, clip_path)
    param = resolve(roots, parameter_path)
    env = clip.automation_envelope(param)
    if env is None and create:
        factory = getattr(clip, "create_automation_envelope", None)
        if callable(factory):
            env = factory(param)
    if env is None:
        raise LomError("live_error",
                       f"no automation envelope for {parameter_path!r} — "
                       + ("is the parameter automatable?" if create else
                          "nothing is automated there (envelopes are created on write)"))
    return env


def insert_steps(roots: dict, clip_path: str, parameter_path: str,
                 steps: list) -> int:
    """Insert automation steps into a clip's envelope for one parameter.

    Each step: ``{"time": beats, "value": v, "length": beats?}``. A step holds
    its value flat over ``[time, time+length)`` — and a ZERO length is a silent
    no-op in Live (found live-validating), so it's rejected here. Omitted
    lengths auto-fill to the next step's time (a staircase); the last one runs
    to the clip's end.
    """
    if not isinstance(steps, list) or not steps:
        raise LomError("bad_request", "steps must be a non-empty list")
    try:
        parsed = sorted(
            ({"time": float(s["time"]), "value": float(s["value"]),
              "length": None if s.get("length") is None else float(s["length"])}
             for s in steps), key=lambda s: s["time"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LomError("bad_request", f"invalid step: {exc}")
    env = _envelope(roots, clip_path, parameter_path, create=True)
    clip = _require_clip(roots, clip_path)
    clip_end = float(getattr(clip, "length", 0.0) or 0.0)
    for i, s in enumerate(parsed):
        length = s["length"]
        if length is None:  # fill to the next step, or the clip's end
            if i + 1 < len(parsed):
                length = parsed[i + 1]["time"] - s["time"]
            else:
                length = max(clip_end - s["time"], 1.0)
        if length <= 0.0:
            raise LomError("bad_request",
                           f"step at index {i} has length {length} — steps need a "
                           "positive length (zero-length steps do nothing in Live)")
        env.insert_step(s["time"], length, s["value"])
    return len(parsed)


def read_values(roots: dict, clip_path: str, parameter_path: str,
                times: list) -> list:
    """Sample a clip envelope's value at each time (beats). For verification."""
    env = _envelope(roots, clip_path, parameter_path)
    if not isinstance(times, list) or not times:
        raise LomError("bad_request", "times must be a non-empty list")
    return [float(env.value_at_time(float(t))) for t in times]


def clear(roots: dict, clip_path: str, parameter_path: str | None = None) -> bool:
    """Clear one parameter's envelope, or ALL of the clip's envelopes."""
    clip = _require_clip(roots, clip_path)
    if parameter_path is None:
        clip.clear_all_envelopes()
    else:
        clip.clear_envelope(resolve(roots, parameter_path))
    return True
