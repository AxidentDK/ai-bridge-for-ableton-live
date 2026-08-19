"""Tests for the Gemini transport, conversation state and session log.

NOTHING HERE TOUCHES THE NETWORK. Every test either drives the pure logic directly or
substitutes ``urllib.request.urlopen``, so the suite runs with no key, no credit and no
connection — which matters today, because the account is out of credit and a suite that
needed the API would simply be red for a day and teach nothing.

What is worth testing here is narrow but real: an error path that used to call
``SystemExit`` and would now take a window down with it, a retry policy that can waste
155 seconds on a failure that will never clear, and a history that must not keep a
question whose answer never arrived.

No pytest, matching the rest of the repo: run the file.
"""
import io
import json
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import gemini_client as G  # noqa: E402


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _reply(text: str) -> _FakeResponse:
    return _FakeResponse(json.dumps(
        {"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body.encode()))


class _Transport:
    """Stands in for urlopen. Records the request bodies; replays a script of outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.bodies = []
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        self.bodies.append(json.loads(request.data.decode()))
        outcome = self.outcomes.pop(0) if self.outcomes else _reply("ok")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _patch(monkey_transport, no_sleep=True):
    urllib.request.urlopen = monkey_transport
    if no_sleep:
        G.time.sleep = lambda _s: None


# =====================================================================================
# The transport
# =====================================================================================

def test_a_failure_raises_GeminiError_and_never_SystemExit():
    """The whole reason this module exists as something other than the CLI.

    ``SystemExit`` inherits from BaseException, so a worker thread raising it inside a
    Tk callback does not get caught by ``except Exception`` and kills the interpreter.
    A chat window must be able to show "that failed" and stay open.
    """
    _patch(_Transport(_http_error(400, "bad request")))
    try:
        G.ask("hello", "key")
    except G.GeminiError as exc:
        assert exc.status == 400, exc.status
        assert "400" in str(exc)
    except SystemExit:                                             # pragma: no cover
        raise AssertionError("still raising SystemExit — a window cannot survive that")
    else:
        raise AssertionError("no error raised")


def test_a_per_minute_rate_limit_is_retried():
    """429 with a PerMinute quota id clears in seconds; waiting is the correct move."""
    transport = _Transport(
        _http_error(429, json.dumps({"error": {"status": "RESOURCE_EXHAUSTED", "details": [
            {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}})),
        _reply("second time lucky"))
    _patch(transport)
    assert G.ask("hi", "key") == "second time lucky"
    assert transport.calls == 2, transport.calls


def test_an_exhausted_quota_fails_immediately_and_says_why():
    """The case Kim is in today: credit bought, not yet registered.

    Retrying spends 155 seconds of backoff to arrive at the same failure with a vaguer
    message. Worse, "network error after 5 attempts" invites debugging the connection,
    which is not the problem.
    """
    transport = _Transport(
        _http_error(429, json.dumps({"error": {"status": "RESOURCE_EXHAUSTED",
                                               "message": "billing quota exceeded"}})))
    _patch(transport)
    try:
        G.ask("hi", "key")
    except G.GeminiError as exc:
        assert transport.calls == 1, f"retried {transport.calls} times anyway"
        assert "quota" in str(exc).lower()
        assert "day to register" in str(exc), "does not explain the wait"
    else:
        raise AssertionError("no error raised")


def test_the_key_travels_in_a_header_and_never_in_the_url():
    """So it cannot turn up in an error message, a log line or a proxy access log."""
    seen = {}

    def transport(request, timeout=None):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.headers)
        return _reply("ok")

    _patch(transport)
    G.ask("hi", "SECRET-KEY-VALUE")
    assert "SECRET" not in seen["url"], seen["url"]
    assert any(v == "SECRET-KEY-VALUE" for v in seen["headers"].values())


def test_the_preamble_is_sent_as_a_system_instruction():
    """Not as a first user turn: it must stay in force at turn 20, not drift out of
    attention as the history grows."""
    transport = _Transport(_reply("ok"))
    _patch(transport)
    G.ask("hi", "key")
    body = transport.bodies[0]
    assert "systemInstruction" in body
    assert "LISTENER" in body["systemInstruction"]["parts"][0]["text"]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


def test_an_empty_reply_is_an_error_rather_than_an_empty_string():
    """A silently empty answer is the browser failure this tool was built to escape."""
    _patch(_Transport(_FakeResponse(json.dumps(
        {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]}).encode())))
    try:
        G.ask("hi", "key")
    except G.GeminiError as exc:
        assert "SAFETY" in str(exc)
    else:
        raise AssertionError("empty reply accepted")


# =====================================================================================
# Conversation
# =====================================================================================

def test_history_accumulates_with_roles():
    _patch(_Transport(_reply("first"), _reply("second")))
    chat = G.Conversation()
    assert chat.send("one", "key") == "first"
    assert chat.send("two", "key") == "second"
    assert [t["role"] for t in chat.turns] == ["user", "model", "user", "model"]
    assert chat.turns[2]["parts"][0]["text"] == "two"


def test_a_failed_send_leaves_no_orphan_question_in_the_history():
    """Otherwise the NEXT reply reads as an answer to both questions.

    That is a quiet way to be misled about what Gemini actually said — and this is a
    tool for checking claims, so it is exactly the wrong failure to have.
    """
    _patch(_Transport(_http_error(400, "nope"), _reply("answer to the second")))
    chat = G.Conversation()
    try:
        chat.send("lost question", "key")
    except G.GeminiError:
        pass
    assert chat.turns == [], chat.turns
    chat.send("real question", "key")
    assert len(chat.turns) == 2
    assert chat.turns[0]["parts"][0]["text"] == "real question"


def test_history_is_never_silently_trimmed():
    """A conversation that drops old turns to save tokens changes the meaning of "you
    said earlier" without saying so."""
    _patch(_Transport(*[_reply("x") for _ in range(30)]))
    chat = G.Conversation()
    for i in range(15):
        chat.send("q" * 5000 + str(i), "key")
    assert len(chat.turns) == 30
    assert chat.turns[0]["parts"][0]["text"].endswith("0")


# =====================================================================================
# Transcript — the log, which is the part that has to survive a crash
# =====================================================================================

def test_every_exchange_is_on_disk_before_the_next_one_starts():
    """The log is flushed per exchange, so a crash costs nothing that was answered.

    Read back from a SEPARATE handle while the transcript object is still open: that is
    the crash case, and a buffered write would pass a test that only checked at the end.
    """
    with TemporaryDirectory() as tmp:
        log = G.Transcript(Path(tmp), now=datetime(2026, 8, 16, 14, 30))
        log.append("user", "what does attack_ms measure?")
        assert "attack_ms" in log.path.read_text(encoding="utf-8")
        log.append("model", "the first arrival at peak level")
        text = log.path.read_text(encoding="utf-8")
        assert "first arrival" in text
        assert text.index("attack_ms") < text.index("first arrival"), "out of order"


def test_exchanges_are_numbered_by_question_not_by_line():
    with TemporaryDirectory() as tmp:
        log = G.Transcript(Path(tmp))
        for i in range(3):
            log.append("user", f"q{i}")
            log.append("model", f"a{i}")
        text = log.path.read_text(encoding="utf-8")
        assert "## 1. Kim" in text and "## 3. Kim" in text
        assert "## 4." not in text
        log.close()
        assert "3 exchanges" in log.path.read_text(encoding="utf-8")


def test_the_log_filename_is_sortable_and_stamped():
    with TemporaryDirectory() as tmp:
        log = G.Transcript(Path(tmp), label="chat",
                           now=datetime(2026, 8, 16, 9, 5, 1))
        assert log.path.name == "20260816-090501-chat.md"


def test_a_saved_key_lands_outside_the_repo_and_is_readable_by_the_resolver():
    """The dialog's whole safety claim in one test: a pasted key cannot be committed.

    Also checks the comment header does not confuse ``backend._read_key_file``, which
    takes the first non-comment line — a header that broke it would leave the user with
    a saved key the program cannot see.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
    import backend                                                 # noqa: PLC0415
    import gemini_chat                                             # noqa: PLC0415

    assert gemini_chat.KEY_PATH.parent != Path(backend.REPO_ROOT), \
        "the dialog would write a key inside the repository"
    with TemporaryDirectory() as tmp:
        target = Path(tmp) / "gemini_api_key.txt"
        gemini_chat.save_key("AIzaTESTKEY0123456789abcdef", target)
        assert backend._read_key_file(target) == "AIzaTESTKEY0123456789abcdef"


def _run() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    original = urllib.request.urlopen
    passed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:                                          # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"  PASS  {name}")
        finally:
            urllib.request.urlopen = original
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
