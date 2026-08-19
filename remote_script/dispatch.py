"""Route a request to a handler over the LOM, and build the response envelope.

Pure logic, no ``Live`` import. ``handle(context, request)`` always returns a
well-formed response dict — it never raises — so the socket layer can always
reply.
"""
from __future__ import annotations

import traceback

from . import envelopes, lom, notes, warp

#: Kept EQUAL to host/mcp_server.py's SERVER_VERSION: the two halves ship together, so
#: two different numbers could only ever mislead someone asking whether their remote
#: script matches their host.
BRIDGE_VERSION = "1.0.0"

# One batch = one hop onto Live's main thread, which runs its ops back-to-back.
# The cap keeps a runaway batch from stalling Live's UI for a whole tick.
MAX_BATCH_OPS = 500


def handle(context: dict, request: dict, client=None) -> dict:
    """context = {roots, versions:{live,python}, capabilities, registry}.

    ``client`` (a server ``Client``: ``.id`` + non-blocking ``.send``) is
    required for ``observe`` — events flow back through its outbox.
    """
    rid = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    handler = _METHODS.get(method)
    if handler is None:
        return _err(rid, "bad_request", f"unknown method {method!r}")
    try:
        return {"id": rid, "ok": True, "result": handler(context, params, client)}
    except lom.LomError as e:
        return _err(rid, e.type, e.message)
    except KeyError as e:
        return _err(rid, "bad_request", f"missing param {e}")
    except Exception as e:  # a real Live-side failure — wrap, never crash
        return _err(rid, "live_error", str(e), detail=traceback.format_exc())


def _err(rid, type_, message, detail=None):
    error = {"type": type_, "message": message}
    if detail is not None:
        error["detail"] = detail
    return {"id": rid, "ok": False, "error": error}


# --- methods (generic core + observers) ----------------------------------------

def _hello(context, params, client):
    v = context.get("versions", {})
    return {
        "bridge_version": BRIDGE_VERSION,
        "live_version": v.get("live"),
        "python_version": v.get("python"),
        "capabilities": context.get("capabilities", []),
    }


def _ping(context, params, client):
    return "pong"


def _get(context, params, client):
    return lom.get(context["roots"], params["path"], params["prop"])


def _set(context, params, client):
    return lom.set_(context["roots"], params["path"], params["prop"], params["value"])


def _call(context, params, client):
    return lom.call(context["roots"], params["path"], params["func"], params.get("args"))


def _resolve(context, params, client):
    return lom.resolve_ref(context["roots"], params["path"])


def _children(context, params, client):
    return lom.children(context["roots"], params["path"])


def _observe(context, params, client):
    if client is None:
        raise lom.LomError("bad_request", "observe requires a connected client")
    return context["registry"].subscribe(
        context["roots"], params["path"], params["prop"], client.id, client.send
    )


def _unobserve(context, params, client):
    return context["registry"].unsubscribe(params["sub"])


def _batch(context, params, client):
    """Run many ops in ONE request — and therefore one main-thread hop.

    The per-request cost is dominated by the ``schedule_message`` hop onto
    Live's tick, not the LOM access itself; batching N ops into one hop is what
    turns a 65-second device read into a sub-second one. Ops run in order, each
    isolated: one failing op yields an error entry, the rest still run.
    """
    ops = params["ops"]
    if not isinstance(ops, list):
        raise lom.LomError("bad_request", "ops must be a list of {method, params}")
    if len(ops) > MAX_BATCH_OPS:
        raise lom.LomError(
            "bad_request", f"batch of {len(ops)} ops exceeds the {MAX_BATCH_OPS} cap")
    results = []
    for op in ops:
        method = op.get("method") if isinstance(op, dict) else None
        handler = _METHODS.get(method)
        if handler is None or method == "batch":  # no nesting
            results.append(_op_err("bad_request",
                                   f"unknown or disallowed method {method!r}"))
            continue
        try:
            results.append({"ok": True,
                            "result": handler(context, op.get("params") or {}, client)})
        except lom.LomError as e:
            results.append(_op_err(e.type, e.message))
        except KeyError as e:
            results.append(_op_err("bad_request", f"missing param {e}"))
        except Exception as e:  # same isolation contract as handle()
            results.append(_op_err("live_error", str(e)))
    return results


def _op_err(type_, message):
    return {"ok": False, "error": {"type": type_, "message": message}}


def _clip_get_notes(context, params, client):
    return notes.get_notes(
        context["roots"], params["path"],
        params.get("from_time"), params.get("time_span"),
        params.get("from_pitch"), params.get("pitch_span"),
    )


def _clip_add_notes(context, params, client):
    factory = context.get("note_spec_factory")
    if factory is None:
        raise lom.LomError("internal", "note_spec_factory missing from context")
    return notes.add_notes(context["roots"], params["path"], params["notes"], factory)


def _clip_remove_notes(context, params, client):
    return notes.remove_notes(
        context["roots"], params["path"],
        params.get("from_time"), params.get("time_span"),
        params.get("from_pitch"), params.get("pitch_span"),
    )


def _clip_apply_note_modifications(context, params, client):
    return notes.apply_modifications(context["roots"], params["path"], params["notes"])


def _clip_remove_notes_by_id(context, params, client):
    return notes.remove_by_id(context["roots"], params["path"], params["note_ids"])


def _clip_envelope_insert(context, params, client):
    return envelopes.insert_steps(
        context["roots"], params["path"], params["parameter"], params["steps"])


def _clip_envelope_read(context, params, client):
    return envelopes.read_values(
        context["roots"], params["path"], params["parameter"], params["times"])


def _clip_envelope_clear(context, params, client):
    return envelopes.clear(
        context["roots"], params["path"], params.get("parameter"))


def _clip_warp_markers(context, params, client):
    return warp.warp_markers(
        context["roots"], params["path"], context["warp_marker_factory"],
        warping=params.get("warping"), warp_mode=params.get("warp_mode"),
        add=params.get("add"), move=params.get("move"),
        remove=params.get("remove"), limit=params.get("limit"))


_METHODS = {
    "hello": _hello,
    "ping": _ping,
    "get": _get,
    "set": _set,
    "call": _call,
    "resolve": _resolve,
    "children": _children,
    "observe": _observe,
    "unobserve": _unobserve,
    "batch": _batch,
    "clip_get_notes": _clip_get_notes,
    "clip_add_notes": _clip_add_notes,
    "clip_remove_notes": _clip_remove_notes,
    "clip_apply_note_modifications": _clip_apply_note_modifications,
    "clip_remove_notes_by_id": _clip_remove_notes_by_id,
    "clip_envelope_insert": _clip_envelope_insert,
    "clip_envelope_read": _clip_envelope_read,
    "clip_envelope_clear": _clip_envelope_clear,
    "clip_warp_markers": _clip_warp_markers,
}
