"""Generic Live Object Model access — the heart of the bridge.

Pure logic: **no ``Live`` import.** The remote script supplies ``roots`` (e.g.
``{"live_set": song, "live_app": application}``) and everything here operates on
plain objects, so it's fully unit-testable against fakes.

Five primitives cover the entire LOM: ``resolve`` a path to an object, then
``get`` / ``set_`` / ``call`` / ``children`` on it. Values are serialized lazily
(scalars pass through; objects become ``{"$ref", "type", …}`` — never a deep
graph walk).
"""
from __future__ import annotations

import inspect
import json

_PRIMITIVES = (bool, int, float, str)


class LomError(Exception):
    """A structured LOM error. ``type`` is one of the protocol error strings."""

    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_
        self.message = message


def _typename(obj) -> str:
    return type(obj).__name__


def _is_index(token: str) -> bool:
    return token.isdigit()


def resolve(roots: dict, path: str):
    """Resolve a space-separated LOM path to an object.

    Walk rule: the first token names a root; each further token is either a list
    index (all-digits) or an attribute name. Raises ``LomError('no_such_path')``.
    """
    tokens = path.split()
    if not tokens:
        raise LomError("no_such_path", "empty path")
    root = tokens[0]
    if root not in roots:
        raise LomError("no_such_path", f"unknown root {root!r} (have: {sorted(roots)})")
    obj = roots[root]
    walked = [root]
    for tok in tokens[1:]:
        try:
            obj = obj[int(tok)] if _is_index(tok) else getattr(obj, tok)
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise LomError("no_such_path", f"no {tok!r} at {' '.join(walked)!r}: {exc}")
        walked.append(tok)
    return obj


def get(roots: dict, path: str, prop: str):
    obj = resolve(roots, path)
    try:
        value = getattr(obj, prop)
    except AttributeError:
        raise LomError("no_such_property", f"{_typename(obj)} has no property {prop!r}")
    return serialize(value)


def set_(roots: dict, path: str, prop: str, value):
    obj = resolve(roots, path)
    if not hasattr(obj, prop):
        raise LomError("no_such_property", f"{_typename(obj)} has no property {prop!r}")
    real = deserialize(roots, value)
    try:
        setattr(obj, prop, real)
        return True
    except Exception as first:
        # LOM setters are strict — a DeviceParameter wants a float, not "0.82".
        # Untyped callers (LLMs sending JSON through an unconstrained schema)
        # routinely pass numbers as strings, or int/float swapped. Retry with
        # sensible coercions before giving up.
        for coerced in _coercions(real):
            try:
                setattr(obj, prop, coerced)
                return True
            except Exception:
                continue
        raise LomError("not_writable", f"cannot set {prop!r} on {_typename(obj)}: {first}")


def _coercions(v):
    """Alternative typings to try when a strict LOM setter rejects the value."""
    if isinstance(v, bool):
        return []
    if isinstance(v, str):
        s, out = v.strip(), []
        try:
            out.append(int(s))
        except ValueError:
            pass
        try:
            out.append(float(s))
        except ValueError:
            pass
        if s.lower() in ("true", "false"):
            out.append(s.lower() == "true")
        return out
    if isinstance(v, float) and v.is_integer():
        return [int(v)]
    if isinstance(v, int):
        return [float(v)]
    return []


def call(roots: dict, path: str, func: str, args):
    obj = resolve(roots, path)
    fn = getattr(obj, func, None)
    if not callable(fn):
        raise LomError("no_such_function", f"{_typename(obj)} has no function {func!r}")
    real = [deserialize(roots, a) for a in (args or [])]
    return serialize(fn(*real))


def children(roots: dict, path: str):
    """Introspect a node: which properties and functions it exposes.

    Uses ``getattr_static`` so property getters are never triggered (no
    expensive fetches, no side effects) — values are read separately via ``get``.
    """
    obj = resolve(roots, path)
    props, funcs = [], []
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            static = inspect.getattr_static(obj, name)
        except AttributeError:
            continue
        if callable(static) and not isinstance(static, property):
            funcs.append(name)
        else:
            props.append(name)
    return {"type": _typename(obj), "properties": sorted(props), "functions": sorted(funcs)}


def resolve_ref(roots: dict, path: str):
    """Return a ``$ref`` for a path if it exists, else ``None`` (no raise)."""
    try:
        obj = resolve(roots, path)
    except LomError:
        return None
    return _ref(obj, path)


# --- serialization -------------------------------------------------------------

def serialize(value):
    if value is None or isinstance(value, _PRIMITIVES):
        return value
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    # Live's containers (song.tracks, clip_slots, ...) are Vector types — not
    # list/tuple, but iterable. Serialize any non-mapping iterable as a sequence.
    if not isinstance(value, (str, bytes)) and hasattr(value, "__iter__"):
        return [serialize(v) for v in value]
    return _ref(value, None)  # a LOM object; path unknown for returned objects


def _ref(obj, path):
    ref = {"$ref": path, "type": _typename(obj)}
    name = getattr(obj, "name", None)
    if isinstance(name, str):
        ref["name"] = name
    ptr = getattr(obj, "_live_ptr", None)
    if isinstance(ptr, int):
        ref["id"] = ptr
    return ref


def deserialize(roots: dict, value):
    """Turn wire values back into Python/LOM.

    A ``{"$path": "…"}`` OR ``{"$ref": "…"}`` mapping resolves to the live object
    at that path. Accepting ``$ref`` — the key ``serialize`` emits — means an
    object handed back by any read can be passed straight back as a call argument
    (e.g. ``duplicate_clip_to_arrangement(clip, time)``). A ``None`` path (a
    freshly created object whose LOM path isn't known) is left as-is.
    """
    if isinstance(value, str):
        # Untyped callers (an LLM filling an unconstrained `value` schema) send
        # complex values as JSON TEXT — the same reason numbers arrive as "0.82".
        # Only a string that actually looks like an object reference is parsed,
        # so ordinary string values (names, colors) are never touched.
        stripped = value.strip()
        if stripped.startswith("{") and ("$path" in stripped or "$ref" in stripped):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                return value
            if isinstance(parsed, dict):
                return deserialize(roots, parsed)
        return value
    if isinstance(value, dict):
        path = value.get("$path")
        if path is None:
            path = value.get("$ref")
        if isinstance(path, str):
            return resolve(roots, path)
    return value
