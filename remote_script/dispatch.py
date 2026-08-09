"""Route a request to a handler over the LOM, and build the response envelope.

Pure logic, no ``Live`` import. ``handle(context, request)`` always returns a
well-formed response dict — it never raises — so the socket layer can always
reply.
"""
from __future__ import annotations

import traceback

from . import lom

BRIDGE_VERSION = "0.1.0"


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
}
