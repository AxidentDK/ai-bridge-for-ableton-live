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
    try:
        setattr(obj, prop, deserialize(roots, value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise LomError("not_writable", f"cannot set {prop!r} on {_typename(obj)}: {exc}")
    return True


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
    """Turn wire values back into Python/LOM. ``{"$path": "…"}`` -> live object."""
    if isinstance(value, dict) and "$path" in value:
        return resolve(roots, value["$path"])
    return value
