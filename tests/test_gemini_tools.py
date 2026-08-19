"""Tests for the schema translation and the function-calling loop.

NOTHING HERE TOUCHES THE NETWORK OR LIVE. The transport is a scripted fake and
``run_tool`` is a local function, so this runs with no key, no credit and no Ableton —
which is the point: the loop's failure modes are all in the plumbing, and plumbing that
can only be tested against a live DAW does not get tested.

The two tests that earn their place above the others:

``test_every_bridge_tool_translates_into_the_subset`` re-runs Gemini's documented schema
rules over the OUTPUT of the translation, for all 60 real tools. That is the test that
would have caught the three no-arg tools, which do not fail loudly — an invalid
declaration takes down the whole request, so every tool disappears rather than the one
that was malformed.

``test_a_model_turn_is_stored_verbatim_so_a_thought_signature_cannot_be_dropped`` is the
one that keeps HTTP 400 away. Gemini 3 requires the signature echoed back in position,
and the API's error names a content-block index rather than the code that lost it, so a
regression here would be expensive to trace and trivial to introduce.

No pytest, matching the rest of the repo: run the file.
"""
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "host"))

import gemini_tools as T                                              # noqa: E402
import mcp_server                                                     # noqa: E402


# --- the fake transport ----------------------------------------------------------------

class _Script:
    """Replays scripted payloads, and remembers exactly what it was asked to send.

    The recorded body is a deep snapshot, not a reference. ``drive`` appends to the same
    history list after every call, so holding a reference would show the history's FINAL
    state in place of what went out on turn one — and the whole question these tests ask
    is what was on the wire at the time.
    """

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.bodies = []

    def __call__(self, body, key, **kwargs):
        self.bodies.append(json.loads(json.dumps(body)))
        return self.payloads.pop(0) if self.payloads else _text("done")


def _calls(*calls, signature=None):
    """A model payload holding function calls, the first optionally signed."""
    parts = []
    for index, (name, args) in enumerate(calls):
        part = {"functionCall": {"name": name, "id": f"call_{index}", "args": args}}
        if index == 0 and signature:
            part["thoughtSignature"] = signature
        parts.append(part)
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def _text(text):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}},
                           ]}


# --- the schema subset, as documented --------------------------------------------------

def _violations(node, where, out):
    """Gemini's documented rules, applied to a TRANSLATED node."""
    if not isinstance(node, dict) or not node:
        out.append(f"{where}: empty schema")
        return
    kind = node.get("type")
    if kind not in T.ALLOWED_TYPES:
        out.append(f"{where}: type {kind!r}")
    for field in node:
        if field not in T.ALLOWED_FIELDS:
            out.append(f"{where}: field {field!r}")
    if kind == "object":
        properties = node.get("properties")
        if not properties:
            out.append(f"{where}: object with empty properties")
        else:
            for name, sub in properties.items():
                _violations(sub, f"{where}.{name}", out)
    if kind == "array":
        if "items" not in node:
            out.append(f"{where}: array without items")
        else:
            _violations(node["items"], f"{where}[]", out)


def test_every_bridge_tool_translates_into_the_subset():
    declarations, _ = T.to_declarations(mcp_server.TOOLS)
    assert len(declarations) == len(mcp_server.TOOLS), "a tool went missing"
    out = []
    for declaration in declarations:
        assert declaration["name"], "a declaration lost its name"
        assert declaration["description"], f"{declaration['name']}: no description"
        if "parameters" in declaration:
            _violations(declaration["parameters"], declaration["name"], out)
    assert not out, "schemas still outside the subset:\n  " + "\n  ".join(out)


def test_a_no_argument_tool_omits_parameters_entirely():
    declarations, stringified = T.to_declarations(mcp_server.TOOLS)
    by_name = {d["name"]: d for d in declarations}
    for name in ("live_ping", "live_summary", "live_scenes"):
        assert "parameters" not in by_name[name], f"{name} kept an empty parameter list"
        assert stringified[name] == [], f"{name} has nothing to decode"


def test_an_array_without_items_gets_json_encoded_elements():
    declarations, stringified = T.to_declarations(mcp_server.TOOLS, ["live_call"])
    args = declarations[0]["parameters"]["properties"]["args"]
    assert args["type"] == "array"
    assert args["items"]["type"] == "string"
    assert ("args", T.ELEMENT) in stringified["live_call"]
    decoded = T.decode_args("live_call", {"path": "live_set", "func": "x",
                                          "args": ["1", '"two"', "true"]}, stringified)
    assert decoded["args"] == [1, "two", True], decoded["args"]
    assert decoded["path"] == "live_set", "an untouched parameter was rewritten"


def test_a_free_form_object_becomes_a_string_and_round_trips():
    declarations, stringified = T.to_declarations(mcp_server.TOOLS, ["live_batch"])
    ops = declarations[0]["parameters"]["properties"]["ops"]
    assert ops["items"]["properties"]["params"]["type"] == "string"
    assert "JSON-encoded" in ops["items"]["properties"]["params"]["description"]
    assert ("ops", T.ELEMENT, "params") in stringified["live_batch"]
    decoded = T.decode_args("live_batch", {"ops": [
        {"method": "get", "params": '{"path": "live_set", "prop": "tempo"}'},
        {"method": "get", "params": '{"path": "live_set", "prop": "is_playing"}'},
    ]}, stringified)
    assert decoded["ops"][0]["params"] == {"path": "live_set", "prop": "tempo"}
    assert decoded["ops"][1]["params"]["prop"] == "is_playing"
    assert decoded["ops"][0]["method"] == "get", "a sibling field was disturbed"


def test_a_clip_named_like_json_survives_decoding():
    """The reason paths are recorded instead of sniffing every string.

    A clip really can be called ``[1,2]``. Parsing anything that parses would turn that
    name into a list, the search would then look for the wrong thing, and nothing would
    raise — the exact shape of failure this project keeps paying for.
    """
    _, stringified = T.to_declarations(mcp_server.TOOLS, ["live_browse", "live_set"])
    kept = T.decode_args("live_browse", {"category": "clips", "query": "[1,2]"},
                         stringified)
    assert kept["query"] == "[1,2]", "a literal string was decoded into structure"
    assert isinstance(kept["query"], str)
    # ...while the parameter that WAS declared as encoded is decoded at the same time.
    both = T.decode_args("live_set", {"path": "live_set", "prop": "tempo", "value": "97.5"},
                         stringified)
    assert both["value"] == 97.5
    assert both["prop"] == "tempo"


def test_a_bad_json_argument_names_the_parameter_rather_than_crashing():
    _, stringified = T.to_declarations(mcp_server.TOOLS, ["live_batch"])
    try:
        T.decode_args("live_batch", {"ops": [{"method": "get", "params": "{not json"}]},
                      stringified)
    except ValueError as exc:
        assert "live_batch" in str(exc) and "params" in str(exc), str(exc)
    else:
        raise AssertionError("malformed JSON was accepted")


def test_translation_never_mutates_the_servers_own_schemas():
    """``mcp_server.TOOLS`` is module state shared with the running MCP server."""
    before = json.dumps(mcp_server.TOOLS, sort_keys=True)
    T.to_declarations(mcp_server.TOOLS)
    assert json.dumps(mcp_server.TOOLS, sort_keys=True) == before


# --- the loop --------------------------------------------------------------------------

_PING = [{"name": "live_ping", "description": "check", "inputSchema": {
    "type": "object", "properties": {}, "required": []}}]

_ONE_ARG = [{"name": "t", "description": "d", "inputSchema": {
    "type": "object", "properties": {"a": {"type": "string"}}, "required": []}}]


def test_a_model_turn_is_stored_verbatim_so_a_thought_signature_cannot_be_dropped():
    script = _Script(_calls(("live_ping", {}), signature="SIG-ABC"), _text("pong"))
    result = T.drive("ping it", "k", run_tool=lambda n, a: {"ok": True},
                     tools=_PING, post=script)
    assert result["stopped_because"] == "answered"
    # The second request carries the model turn from the first. It must be identical,
    # signature included and still attached to the part it arrived on.
    sent = script.bodies[1]["contents"][1]
    assert sent["parts"][0]["thoughtSignature"] == "SIG-ABC", sent
    assert sent == script.bodies[1]["contents"][1]
    assert sent["parts"][0]["functionCall"]["name"] == "live_ping"
    assert sent["role"] == "model"


def test_parallel_calls_are_answered_in_the_order_received_with_matching_ids():
    script = _Script(_calls(("t", {"a": "1"}), ("t", {"a": "2"}), ("t", {"a": "3"}),
                            signature="SIG"),
                     _text("all three done"))
    seen = []

    def run(name, args):
        seen.append(args["a"])
        return {"got": args["a"]}

    result = T.drive("do three", "k", run_tool=run, tools=_ONE_ARG, post=script)
    assert seen == ["1", "2", "3"], seen
    answers = script.bodies[1]["contents"][2]["parts"]
    assert [p["functionResponse"]["id"] for p in answers] == ["call_0", "call_1", "call_2"]
    assert [p["functionResponse"]["response"]["got"] for p in answers] == ["1", "2", "3"]
    assert result["text"] == "all three done"


def test_a_raising_tool_becomes_an_error_response_and_the_run_continues():
    script = _Script(_calls(("t", {"a": "boom"})), _calls(("t", {"a": "ok"})),
                     _text("recovered"))

    def run(name, args):
        if args["a"] == "boom":
            raise ValueError("Cutoff is not modulatable on this device")
        return {"fine": True}

    result = T.drive("try", "k", run_tool=run, tools=_ONE_ARG, post=script)
    assert result["stopped_because"] == "answered"
    assert result["text"] == "recovered"
    first = script.bodies[1]["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert "not modulatable" in first["error"], first
    assert "ValueError" in first["error"], "the error kind is useful context"
    assert [s["ok"] for s in result["steps"]] == [False, True]


def test_the_loop_stops_at_max_steps_rather_than_forever():
    # A model that never stops calling, which is what a genuine loop looks like.
    script = _Script(*[_calls(("t", {"a": str(i)})) for i in range(10)])
    result = T.drive("spin", "k", run_tool=lambda n, a: {"again": True},
                     tools=_ONE_ARG, post=script, max_steps=3)
    assert result["stopped_because"] == "hit max_steps=3", result["stopped_because"]
    assert len(result["steps"]) == 3
    assert result["text"] == ""


def test_a_non_dict_result_is_wrapped_because_response_must_be_an_object():
    script = _Script(_calls(("t", {"a": "x"})), _text("ok"))
    T.drive("go", "k", run_tool=lambda n, a: [1, 2, 3], tools=_ONE_ARG, post=script)
    response = script.bodies[1]["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert response == {"result": [1, 2, 3]}, response


def test_a_string_result_is_wrapped_too():
    script = _Script(_calls(("t", {"a": "x"})), _text("ok"))
    T.drive("go", "k", run_tool=lambda n, a: "showing session", tools=_ONE_ARG,
            post=script)
    response = script.bodies[1]["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert response == {"result": "showing session"}


def test_the_declarations_and_the_task_are_both_on_the_first_request():
    script = _Script(_text("nothing to do"))
    T.drive("just answer", "k", run_tool=lambda n, a: None, tools=_PING, post=script,
            system="be a producer")
    body = script.bodies[0]
    assert body["contents"][0]["parts"][0]["text"] == "just answer"
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "live_ping"
    assert body["systemInstruction"]["parts"][0]["text"] == "be a producer"


def test_events_report_every_call_and_result():
    script = _Script(_calls(("t", {"a": "x"})), _text("ok"))
    events = []
    T.drive("go", "k", run_tool=lambda n, a: {"v": 1}, tools=_ONE_ARG, post=script,
            on_event=lambda kind, fields: events.append((kind, fields["name"])))
    assert events == [("call", "t"), ("result", "t")], events


def test_a_subset_hides_the_tools_not_asked_for():
    declarations, stringified = T.to_declarations(mcp_server.TOOLS,
                                                  ["live_ping", "live_find_sound"])
    assert sorted(d["name"] for d in declarations) == ["live_find_sound", "live_ping"]
    assert set(stringified) == {"live_find_sound", "live_ping"}


def _run() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:                                             # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"  PASS  {name}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
