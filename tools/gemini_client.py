"""Talk to Gemini: transport, conversation state, and a transcript that survives a crash.

Split out of ``ask_gemini.py`` when the chat window arrived, because the two front ends
need the same three things and the alternative was a second copy of them. That copy is
the mistake this project has already made three times with its DSP code, and the cost
was three fixes that had to be found twice.

Three pieces, each with one job:

  ``ask``          one request, with retries. Raises ``GeminiError`` — NOT ``SystemExit``,
                   which was fine for a CLI and would take a window down with it.
  ``Conversation`` the turn history, so Gemini remembers the previous question. The CLI
                   sends one turn and throws the history away; the window keeps it.
  ``Transcript``   an append-as-you-go markdown log. Written after EVERY exchange rather
                   than at the end, because a session log that only exists if you close
                   the window cleanly is a session log you will one day not have.

Stdlib only. The bridge is deliberately near-zero-dependency, and neither a review tool
nor a chat window is a reason to break that.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

#: Verified against the live model list for this key rather than recalled: a Pro tier
#: with a 1M-token context window, so a 1,700-line module arrives whole instead of in
#: fragments. Override with $GEMINI_MODEL; `ask_gemini.py --models` prints what is
#: actually reachable, which is the only trustworthy source for these ids.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")

#: What the reviewer needs to know before reading a single line. Without it the advice
#: is confidently wrong about the architecture — the first review assumed CLAP and
#: Essentia were running at runtime, when neither is used.
PREAMBLE = """You are peer-reviewing a two-program system for Ableton Live, as an
advisor with strong musical knowledge. Be blunt; the author would rather be corrected
than flattered.

ARCHITECTURE, so you do not have to infer it:
- The LISTENER is a standalone sidecar. It walks a sample library once and writes what
  it heard into SQLite. Runtime is numpy + onnxruntime only, about 20 MB. No
  TensorFlow, no Essentia, no CUDA, no GPU. It uses Essentia's MODELS converted to
  ONNX; Essentia itself has no Windows build and is used only as an offline reference.
- The BRIDGE is 60 MCP tools into a running Ableton Live over a socket. It only ever
  READS that SQLite file: it never imports the listener and never loads a model. If the
  file is absent, search degrades to Live 12's own 64-dim embeddings and says so.
- The MEASUREMENTS both programs make live in ONE file, `shared_dsp.py`, byte-identical
  in both repos with a SHA-256 check in both test suites. Do not suggest "keep these in
  sync" comments; that was tried and it failed three times.
- Index today: 29,870 files, 1.37M tags. Tempo is 69% within 2.5 BPM of a
  filename-stated tempo (octave errors counted as errors, deliberately). Pitch class
  matches a filename-named note 75% of the time.

WHAT IS USEFUL TO SAY:
- Musical or DSP correctness above all: a measurement that is plausible and wrong is
  the failure mode this project keeps hitting, and every such bug so far raised no
  error at all.
- Constants that encode a musical assumption, and whether that assumption holds.
- Anything that would mislead a producer searching the library.
- Where a comment claims something the code does not do.

WHAT IS NOT USEFUL: style, formatting, type annotations, or generic "add error
handling" advice. The author is a strong programmer; you are here for the ear.
"""

#: Transient server-side conditions. 503 is "high demand", 429 is rate limiting; both
#: mean try again rather than give up, and a review of thirty modules will meet them.
_RETRY_CODES = (429, 500, 502, 503, 504)


class GeminiError(RuntimeError):
    """A request failed. Carries enough to tell the user WHICH kind of failure."""

    def __init__(self, message: str, *, status: int | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _is_out_of_credit(detail: str) -> bool:
    """Tell "you are going too fast" apart from "you have no quota left".

    Both arrive as HTTP 429 and only one is worth waiting for. A per-minute limit clears
    in seconds; an exhausted daily or billing quota does not clear at all today, so
    retrying it burns 155 seconds of backoff to reach the same failure with a vaguer
    message.

    Google marks the per-minute limits in the quota id (``...PerMinute...``), so the
    presence of that string is the discriminator. Anything else that says
    RESOURCE_EXHAUSTED is treated as out-of-credit — the conservative way round, since
    the cost of being wrong is one manual retry rather than a hang.
    """
    if "PerMinute" in detail:
        return False
    return "RESOURCE_EXHAUSTED" in detail or "billing" in detail.lower()


def ask(prompt_or_turns, key: str, *, model: str = DEFAULT_MODEL,
        system: str | None = PREAMBLE, timeout: int = 300, attempts: int = 5,
        on_retry=None) -> str:
    """One request. ``prompt_or_turns`` is either a string or a list of API turns.

    ``on_retry(message)`` is called before each backoff sleep so a window can say what
    it is waiting for instead of appearing to hang for two and a half minutes.
    """
    turns = ([{"role": "user", "parts": [{"text": prompt_or_turns}]}]
             if isinstance(prompt_or_turns, str) else list(prompt_or_turns))
    body: dict = {"contents": turns}
    if system:
        # systemInstruction rather than a first user turn: it stays in force for every
        # turn of a long chat, where a prepended message drifts out of attention as the
        # history grows — and would be re-sent, or lost, depending on how history is
        # trimmed later.
        body["systemInstruction"] = {"parts": [{"text": system}]}

    url = f"{API_ROOT}/models/{model}:generateContent"
    encoded = json.dumps(body).encode("utf-8")
    payload = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url, data=encoded,
            # The key travels in a header, never in the URL, so it cannot appear in an
            # error message, a log line or a proxy's access log.
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:900]
            if exc.code == 429 and _is_out_of_credit(detail):
                raise GeminiError(
                    "Gemini rejected the request for lack of quota, not for speed. "
                    "A newly bought credit can take about a day to register, and the "
                    "free tier resets on Google's clock, so this is worth re-checking "
                    "later rather than retrying now.\n\n" + detail,
                    status=429) from None
            if exc.code in _RETRY_CODES and attempt < attempts:
                # Exponential backoff. Three requests fired at once all came back 503,
                # so the fix is to wait AND to stop running reviews in parallel.
                delay = 2 ** attempt * 5
                if on_retry:
                    on_retry(f"HTTP {exc.code}, retrying in {delay}s "
                             f"(attempt {attempt}/{attempts})")
                time.sleep(delay)
                continue
            raise GeminiError(f"Gemini API HTTP {exc.code}: {detail}",
                              status=exc.code) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < attempts:
                delay = 2 ** attempt * 5
                if on_retry:
                    on_retry(f"{type(exc).__name__}, retrying in {delay}s")
                time.sleep(delay)
                continue
            raise GeminiError(f"network error after {attempts} attempts: {exc}",
                              retryable=True) from None
    if payload is None:
        raise GeminiError("no response")

    candidates = payload.get("candidates") or []
    if not candidates:
        # A blocked prompt returns no candidates and a promptFeedback explaining why;
        # printing the raw payload is more use than inventing a category for it.
        raise GeminiError(f"no candidates returned: {json.dumps(payload)[:400]}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts).strip()
    if not text:
        reason = candidates[0].get("finishReason", "unknown")
        if reason == "MAX_TOKENS":
            raise GeminiError(
                "the reply hit the output limit before any text survived — the "
                "question is probably too broad for one turn")
        raise GeminiError(f"empty reply (finishReason={reason})")
    return text


def list_models(key: str) -> list[tuple[str, int]]:
    """Models this key can actually reach, as (name, input token limit)."""
    request = urllib.request.Request(f"{API_ROOT}/models",
                                     headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise GeminiError(f"HTTP {exc.code} listing models: "
                          f"{exc.read().decode('utf-8', 'replace')[:400]}",
                          status=exc.code) from None
    return sorted(
        (m["name"].removeprefix("models/"), m.get("inputTokenLimit", 0))
        for m in payload.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    )


@dataclass
class Conversation:
    """Turn history in the API's own shape, so no translation happens at send time.

    Kept deliberately dumb: it appends, it never edits and it never summarises. A
    conversation that quietly drops old turns to save tokens would change the meaning
    of "you said earlier" without saying so, and this is a tool for checking claims.
    """

    model: str = DEFAULT_MODEL
    system: str | None = PREAMBLE
    turns: list = field(default_factory=list)

    def send(self, text: str, key: str, *, on_retry=None) -> str:
        """Add a user turn, get the reply, remember both. Nothing is stored on failure.

        On error the user turn is rolled back. Otherwise a failed send would leave a
        question in the history with no answer after it, and the NEXT reply would be
        read as an answer to both — which is a subtle way to be lied to about what
        Gemini actually said.
        """
        self.turns.append({"role": "user", "parts": [{"text": text}]})
        try:
            reply = ask(self.turns, key, model=self.model, system=self.system,
                        on_retry=on_retry)
        except Exception:
            self.turns.pop()
            raise
        self.turns.append({"role": "model", "parts": [{"text": reply}]})
        return reply

    @property
    def characters(self) -> int:
        """Total characters in the history — the honest half of a token estimate."""
        return sum(len(p.get("text", ""))
                   for turn in self.turns for p in turn.get("parts", []))

    def estimated_tokens(self) -> int:
        """~4 chars per token. An estimate, and labelled as one wherever it is shown."""
        return (self.characters + len(self.system or "")) // 4


class Transcript:
    """A markdown log written as the session happens, not when it ends.

    Every exchange is appended and flushed immediately. The file is therefore complete
    and readable at all times, including after a crash, a power cut, or the window being
    closed with the X — which is how windows are usually closed.
    """

    def __init__(self, directory: Path, label: str = "chat", model: str = DEFAULT_MODEL,
                 now: datetime | None = None):
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{stamp}-{label}.md"
        self.exchanges = 0
        self.path.write_text(
            f"# Gemini session — {(now or datetime.now()):%Y-%m-%d %H:%M}\n\n"
            f"model: `{model}`\n\n"
            "Written as the session ran: every exchange was appended and flushed at the\n"
            "moment it happened, so this file is complete even if the window was not\n"
            "closed cleanly.\n",
            encoding="utf-8", newline="")

    def append(self, role: str, text: str, *, note: str = "") -> None:
        if role == "user":
            self.exchanges += 1
        heading = {"user": f"## {self.exchanges}. Kim",
                   "model": "### Gemini",
                   "error": "### ⚠ failed"}.get(role, f"### {role}")
        stamp = datetime.now().strftime("%H:%M:%S")
        block = f"\n\n{heading} · {stamp}{f' · {note}' if note else ''}\n\n{text.rstrip()}\n"
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(block)
            handle.flush()

    def close(self, reason: str = "session ended") -> None:
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(f"\n---\n\n_{reason} — {self.exchanges} exchange"
                         f"{'' if self.exchanges == 1 else 's'}, "
                         f"{datetime.now():%Y-%m-%d %H:%M}_\n")


def read_key():
    """The bridge's own key resolution, not a second copy that could disagree with it."""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "host"))
    import backend  # noqa: PLC0415
    return backend.gemini_key(), backend
