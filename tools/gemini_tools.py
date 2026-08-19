"""Give Gemini the bridge's tools: schema translation, and the function-calling loop.

``host/backend.py`` has always said this piece was separate work. This is that piece.
The bridge already had the only seam it needs — ``mcp_server.TOOLS`` (name, description,
JSON Schema) and one ``run_tool(name, args)`` dispatch — so nothing here reaches into
Live. It translates schemas one way, decodes arguments the other way, and runs the loop
between them. ``run_tool`` is injected, which is why this file can be tested with no
Live, no key and no network.

TWO THINGS THE API FORCES, both verified against current docs rather than recalled:

1. **Function declarations take a SUBSET of OpenAPI**: only ``type``, ``properties``,
   ``items``, ``required``, ``enum``, ``description``. No ``anyOf``. No
   ``additionalProperties``. An object with empty ``properties`` is rejected, and an
   array must carry ``items``. An audit of all 60 bridge tools found exactly 9 that
   break this, in three groups — 3 no-arg tools, 1 array with no ``items``, and 5 with
   genuinely free-form objects (``live_batch`` ops, ``live_edit_notes`` edits, the two
   ``live_morph`` snapshots, ``live_snapshot_apply``, and ``live_set``'s any-typed value).

2. **Gemini 3 attaches a ``thoughtSignature``** to the part carrying a ``functionCall``,
   and it must come back verbatim and in position or the next request fails with HTTP
   400. The way that is honoured here is by NOT HANDLING IT AT ALL: the model's
   ``content`` is appended to the history exactly as it arrived, so the signature stays
   in the part it came in. There is deliberately no code path that edits a model part —
   a bug that drops a signature cannot be written without deleting a test.

WHY THE FREE-FORM PARAMETERS BECOME STRINGS. A parameter the subset cannot express is
declared ``{"type": "string"}`` and described as JSON-encoded, and the paths that were
treated this way are RECORDED. Decoding then happens at exactly those recorded paths and
nowhere else. The alternative — parsing any argument that looks like JSON — would turn a
clip genuinely named ``[1,2]`` into a list, which is the class of silent, plausible
corruption this project keeps having to hunt down.

Gemini was asked directly whether it would rather those 5 tools were withheld than
offered in a shape it has to encode into, and said keep them: hiding ``live_batch``
forces one-by-one LOM hops and throws away the batching the bridge exists to provide.
It also asked for all 60 declarations at once rather than curated subsets. That second
answer is a self-assessment, not a measurement, so ``include`` exists to subset them if
tool selection turns out to degrade.

Stdlib only, like everything else here.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Where session transcripts go. UNDER THE USER PROFILE, not next to the program: once
#: this is installed rather than cloned, the install folder may not even be writable, and
#: a chat log belongs to the person who had the conversation.
SESSION_DIR = Path.home() / ".ai-bridge" / "sessions"

#: Tools that can overwrite something outside the open set. Refused unless asked for.
GUARDED = ("live_save_set",)

#: What Gemini is told it is doing. Deliberately says NOTHING about how the sound search
#: works, how accurate it is, or that anything is being evaluated: a model briefed on a
#: system's weak points goes looking for them, and then every complaint it makes is one
#: you planted. This is the honest condition to watch it work under.
PRODUCER_PREAMBLE = """You are working inside a running Ableton Live session, as a
producer with your hands on the instrument. You have direct tool access to the session:
you can read its state, search the sample library by describing how something should
sound, audition and load material, create clips, and set parameters.

SESSION VIEW UNLESS TOLD OTHERWISE, and stay there. Session is Live's own default view and
where sketching happens, so default to it rather than asking. What matters is not mixing:
firing a Session clip also starts the global transport, so the Arrangement playhead runs
away underneath while the clip loops on top. To whoever is listening, that sounds like the
music is broken when it is not. If you do need to move between them — capturing a Session
idea into the Arrangement, say — announce it first, and leave the transport in a state you
have explained.

How to work:
- Look before you act. Check what the session already contains rather than assuming.
- When you want a sound, describe the SOUND you want. Do not go hunting through
  filenames or library names.
- Audition what you find before you commit to it, and say what you think of it in your
  own terms.
- If what comes back is not what you asked for, say so plainly and try a different way of
  asking. Do not settle for something that is merely close and call it done.
- If the library genuinely does not have what the music needs, say that instead of
  substituting the nearest thing.
- Work in small steps and tell the producer sitting next to you what you are doing and
  why. They are listening to the results as you go.
"""


def make_runner(run_tool, allow_save: bool = False, on_blocked=None):
    """``run_tool``, with the guarded tools refused rather than executed.

    The refusal is an EXCEPTION because that is how ``drive`` turns a failure into a
    ``functionResponse`` — so the model is told it may not save, in words it can act on,
    which is more useful than the tool being quietly absent from the list. A model that
    cannot see the tool has no way to say "I would save here".
    """
    def run(name: str, args: dict):
        if name in GUARDED and not allow_save:
            if on_blocked:
                on_blocked(name, args)
            raise PermissionError(
                f"{name} is not permitted in this session. The set must not be written "
                "over. Carry on without saving; the producer will save if they want to.")
        return run_tool(name, args)

    return run

#: Every schema field Gemini's subset accepts. Anything else is dropped rather than sent
#: and rejected: the audit found no offenders today, but a schema edited later should
#: degrade to a working declaration instead of breaking every tool at once.
ALLOWED_FIELDS = ("type", "properties", "items", "required", "enum", "description")

#: Every ``type`` the subset accepts. Note the absence of "null" and of unions.
ALLOWED_TYPES = ("object", "string", "integer", "number", "boolean", "array")

#: Marks "each element of this array" in a recorded path. Not a legal property name in
#: any bridge schema, so it cannot collide with one.
ELEMENT = "[]"

#: Appended to the description of anything stringified, so the model is told what shape
#: the string must hold rather than having to infer it from a failure.
_JSON_NOTE = ("Supply as a JSON-encoded string (the API's schema subset cannot express "
              "this parameter's real shape). Example: '{\"a\": 1}' or '[1, 2]'. A string "
              "value must itself be JSON-quoted.")


def _describe(node: dict, fallback: str) -> str:
    existing = (node or {}).get("description", "").strip()
    return f"{existing} {_JSON_NOTE}".strip() if existing else f"{fallback} {_JSON_NOTE}"


def _translate(node, path: tuple, stringified: list) -> dict:
    """One schema node, translated into the subset. Records what it had to stringify.

    Returns a new node; the input is never mutated, because these schemas are module
    state in ``mcp_server`` and a translation that edited them in place would corrupt
    the MCP server running in the same process.
    """
    node = node or {}
    kind = node.get("type")

    # No type at all — live_set's `value`, which is any JSON scalar by design. The subset
    # has no way to say "any", so it becomes an encoded string.
    if kind is None or kind not in ALLOWED_TYPES:
        stringified.append(path)
        return {"type": "string", "description": _describe(node, "Any JSON value.")}

    out = {field: node[field] for field in ALLOWED_FIELDS if field in node}

    if kind == "object":
        properties = node.get("properties") or {}
        if not properties:
            # A free-form object: live_batch's per-op params, a morph snapshot. Rejected
            # outright if sent as an empty-properties object.
            stringified.append(path)
            return {"type": "string", "description": _describe(node, "A JSON object.")}
        out["properties"] = {
            name: _translate(sub, path + (name,), stringified)
            for name, sub in properties.items()
        }
        out["required"] = list(node.get("required") or [])
        return out

    if kind == "array":
        if "items" not in node:
            # live_call's `args`: mixed-type JSON scalars. Each ELEMENT becomes an encoded
            # string, which keeps the array visible as an array rather than collapsing the
            # whole thing into one opaque blob.
            stringified.append(path + (ELEMENT,))
            out["items"] = {"type": "string",
                            "description": _describe({}, "One JSON-encoded value.")}
            return out
        out["items"] = _translate(node["items"], path + (ELEMENT,), stringified)
        return out

    return out


def to_declarations(tools, include=None) -> tuple[list, dict]:
    """MCP tool definitions -> (Gemini functionDeclarations, stringified paths per tool).

    ``include`` optionally selects a subset by name, for the day tool selection turns out
    to degrade with all 60 present. The returned paths are what ``decode_args`` needs;
    keep them together with the declarations they came from, because a path list from one
    translation does not describe another.
    """
    wanted = set(include) if include is not None else None
    declarations, stringified = [], {}
    for tool in tools:
        name = tool["name"]
        if wanted is not None and name not in wanted:
            continue
        raw = tool.get("inputSchema") or {}
        declaration = {"name": name, "description": tool.get("description", "")}

        # EMPTY PROPERTIES MEAN TWO DIFFERENT THINGS, and conflating them was a real bug
        # caught by the tests. NESTED, an object with no properties is free-form and has
        # to become an encoded string. At the TOP LEVEL it means the tool takes no
        # arguments — live_ping, live_summary, live_scenes — and the answer there is to
        # omit `parameters` altogether. Translating the top level with the nested rule
        # turned all three into `{"type": "string"}`, which is not a parameter list at all.
        if raw.get("type", "object") != "object":
            # Not an object at all. Refusing loudly beats presenting a tool that looks
            # callable and can never be called correctly.
            raise ValueError(f"{name}: top-level inputSchema is {raw.get('type')!r}, "
                             "which cannot be a parameter list")
        if not (raw.get("properties") or {}):
            declarations.append(declaration)
            stringified[name] = []
            continue

        paths: list = []
        declaration["parameters"] = _translate(raw, (), paths)
        declarations.append(declaration)
        stringified[name] = paths
    return declarations, stringified


def _decode_at(node, path: tuple):
    """Return ``node`` with the value at ``path`` JSON-decoded. Never mutates the input."""
    if not path:
        if not isinstance(node, str):
            # Already structured: the model sent an object where a string was declared.
            # That is harmless and shouldn't be an error — it is what we wanted anyway.
            return node
        try:
            return json.loads(node)
        except json.JSONDecodeError as exc:
            raise ValueError(f"expected a JSON-encoded value, got {node[:80]!r} "
                             f"({exc.msg})") from None
    head, rest = path[0], path[1:]
    if head == ELEMENT:
        if not isinstance(node, list):
            return node
        return [_decode_at(item, rest) for item in node]
    if not isinstance(node, dict) or head not in node:
        return node  # an absent optional parameter is not an error
    out = dict(node)
    out[head] = _decode_at(node[head], rest)
    return out


def decode_args(name: str, args: dict, stringified: dict) -> dict:
    """Undo the stringification, at the recorded paths ONLY.

    The "only" is the whole point. Scanning every argument for something that parses as
    JSON would rewrite a clip legitimately named ``[1,2]`` into a list, and nothing
    downstream would raise — it would just search for the wrong thing and succeed.
    """
    out = args if isinstance(args, dict) else {}
    for path in stringified.get(name, ()):
        try:
            out = _decode_at(out, path)
        except ValueError as exc:
            raise ValueError(f"{name}: parameter "
                             f"{'.'.join(path) or '<root>'}: {exc}") from None
    return out


def function_calls(content: dict) -> list:
    """The ``functionCall`` parts of a model turn, in the order the model produced them.

    Order matters twice over: parallel calls must be answered in the order received, and
    only the first of them carries a thought signature.
    """
    return [part["functionCall"] for part in (content.get("parts") or [])
            if isinstance(part, dict) and "functionCall" in part]


def response_part(call: dict, payload: dict) -> dict:
    """One ``functionResponse`` part, echoing the call's ``id`` when the API supplied one.

    The id is what maps an answer back to its question when several calls are in flight.
    It is conditional because it is not always present, and sending ``"id": null`` is not
    the same as omitting the field.
    """
    response = {"functionResponse": {"name": call.get("name"), "response": payload}}
    if call.get("id") is not None:
        response["functionResponse"]["id"] = call["id"]
    return response


def _text_of(content: dict) -> str:
    return "\n".join(part.get("text", "") for part in (content.get("parts") or [])
                     if isinstance(part, dict) and "text" in part).strip()


def drive(task, key, *, run_tool, tools, post, model=None, system=None, max_steps=24,
          include=None, on_event=None, timeout=300, on_retry=None, history=None) -> dict:
    """Let Gemini work the tools until it answers in words, or until ``max_steps``.

    ``run_tool(name, args)`` executes one call and returns anything JSON-serialisable, or
    raises. ``post(body, key, ...)`` is the transport — injected so this loop is testable
    without a network and shares ``gemini_client``'s retry policy rather than repeating it.

    A raising tool becomes an error in the ``functionResponse`` rather than the end of the
    run. Gemini asked for exactly that: without it, one mistyped device name aborts the
    whole workflow, where the error text lets it read the constraint and pivot. The
    bounded ``max_steps`` is the other half of that bargain — an error it cannot recover
    from must not become an unbounded loop.

    Returns the final text, every step taken, WHY it stopped, and the conversation so far.
    The reason is part of the result rather than a log line, because "ran out of steps" and
    "answered" look identical if you only read the text.

    ``history`` continues an earlier call, which is what makes a SESSION possible rather
    than a one-shot: the producer hears the result, says "not that, warmer", and Gemini
    still has everything it just did in context. The list is COPIED rather than extended
    in place, so a call that raises leaves the caller's history exactly as it was — the
    same reason ``Conversation.send`` rolls back a failed turn. A history with a question
    in it and no answer after it makes the NEXT reply read as an answer to both.
    """
    declarations, stringified = to_declarations(tools, include)
    history = list(history or [])
    history.append({"role": "user", "parts": [{"text": task}]})
    steps: list = []

    def emit(kind, **fields):
        if on_event:
            on_event(kind, fields)

    for step in range(1, max_steps + 1):
        body: dict = {"contents": history,
                      "tools": [{"functionDeclarations": declarations}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        # `model` is omitted rather than passed as None so the transport's own default
        # stays the single source of truth for which model is current.
        options = {"timeout": timeout, "on_retry": on_retry}
        if model:
            options["model"] = model
        payload = post(body, key, **options)

        candidates = payload.get("candidates") or []
        if not candidates:
            return {"text": "", "steps": steps, "stopped_because": "no candidates",
                    "raw": payload, "history": history}
        content = candidates[0].get("content") or {}

        # VERBATIM. Not rebuilt from name/args — that would drop the thoughtSignature and
        # earn an HTTP 400 on the next turn, and the error names a content-block index
        # rather than the line of code that lost it.
        history.append(content)

        calls = function_calls(content)
        if not calls:
            return {"text": _text_of(content), "steps": steps,
                    "stopped_because": "answered", "history": history,
                    "finish_reason": candidates[0].get("finishReason")}

        parts = []
        for call in calls:
            name = call.get("name")
            raw_args = call.get("args") or {}
            emit("call", step=step, name=name, args=raw_args)
            try:
                args = decode_args(name, raw_args, stringified)
                result = run_tool(name, args)
                # `response` must be a JSON object, so a list or a scalar is wrapped
                # rather than sent bare.
                payload_out = result if isinstance(result, dict) else {"result": result}
                ok, note = True, None
            except Exception as exc:                                   # noqa: BLE001
                # Deliberately broad: any tool failure is information for Gemini, and a
                # traceback here would end a run that it could have recovered from.
                payload_out = {"error": f"{type(exc).__name__}: {exc}"}
                ok, note = False, payload_out["error"]
            steps.append({"step": step, "name": name, "args": raw_args, "ok": ok,
                          "result": payload_out})
            emit("result", step=step, name=name, ok=ok, error=note, result=payload_out)
            parts.append(response_part(call, payload_out))

        history.append({"role": "user", "parts": parts})

    return {"text": "", "steps": steps, "history": history,
            "stopped_because": f"hit max_steps={max_steps}"}
