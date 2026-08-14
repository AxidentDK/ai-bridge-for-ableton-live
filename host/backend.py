"""Which model backend should drive the bridge — Gemini, or the connected MCP client?

Why offer a choice at all: **Gemini was trained on music from the start**, and a
large-context tier can hold a whole project at once, so for *musical* judgement it
may simply be the better ear. The MCP client already driving the bridge stays the
default and the fallback — it is what runs today, and it needs no key.

This module answers one question — *is a Gemini key available?* — and nothing else.
It deliberately does **not** call any API, so it stays importable, dependency-free
and instant. Wiring Gemini's function-calling loop onto the bridge's tool registry
is a separate piece of work; this is the switch it will read.

Resolution order (first hit wins), so a temporary override never needs a file edit:

1. ``GEMINI_API_KEY`` environment variable
2. ``gemini_api_key.txt`` in the repo root      <- the simple file to edit
3. ``~/.ai-bridge/gemini_api_key.txt``          <- if you'd rather keep it out of the repo

Blank lines, ``#`` comments and surrounding whitespace are ignored, so the file can
carry its own instructions.

SAFETY: this repo is PUBLIC. Every path above is gitignored, and ``check()`` refuses
to report success if git says the key file is tracked — a key committed once is a key
that must be revoked, no matter how quickly the commit is reverted.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KEY_FILENAME = "gemini_api_key.txt"

ENV_VAR = "GEMINI_API_KEY"
REPO_KEY_FILE = REPO_ROOT / KEY_FILENAME
HOME_KEY_FILE = Path.home() / ".ai-bridge" / KEY_FILENAME

# Default model. Gemini's line-up moves quickly — override with GEMINI_MODEL rather
# than editing this, and verify the id against current docs before trusting it.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

# A key shorter than this is a placeholder or a truncated paste, not a key.
_MIN_PLAUSIBLE_KEY = 20


def _read_key_file(path: Path) -> str | None:
    """First non-comment, non-blank line of ``path``, or None."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def _is_tracked_by_git(path: Path) -> bool:
    """True if git is tracking this file — i.e. it is one commit from being public.

    Unknown (no git, not a repo) is reported as False: this is a safety *alarm*, not
    a permission check, and it must not block a user who has no git at all.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", str(path)],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def resolve_backend() -> dict:
    """Decide which backend to use. Never raises, never calls out to a network.

    Returns a dict with ``backend`` ("gemini" or "assistant"), the ``source`` the
    decision came from, and ``warnings``. The key itself is NOT included — call
    ``gemini_key()`` for that, so a backend report can be logged or printed safely.
    """
    warnings: list[str] = []

    env_key = (os.environ.get(ENV_VAR) or "").strip()
    if env_key:
        return _describe("gemini", f"${ENV_VAR}", env_key, warnings)

    for path in (REPO_KEY_FILE, HOME_KEY_FILE):
        key = _read_key_file(path)
        if not key:
            continue
        if path == REPO_KEY_FILE and _is_tracked_by_git(path):
            warnings.append(
                f"REFUSING the key in {path.name}: git is TRACKING that file, so the "
                "key would be published on the next push. Remove it from the index "
                "(git rm --cached), and treat the key as compromised if it was ever "
                "pushed. Falling back to the assistant."
            )
            continue
        return _describe("gemini", str(path), key, warnings)

    return {
        "backend": "assistant",
        "source": "no Gemini key found",
        "model": None,
        "checked": [f"${ENV_VAR}", str(REPO_KEY_FILE), str(HOME_KEY_FILE)],
        "warnings": warnings,
    }


def _describe(backend: str, source: str, key: str, warnings: list[str]) -> dict:
    if len(key) < _MIN_PLAUSIBLE_KEY:
        warnings.append(
            f"the value in {source} is only {len(key)} characters — that looks like a "
            "placeholder or a truncated paste, not a real key."
        )
    return {
        "backend": backend,
        "source": source,
        "model": DEFAULT_MODEL,
        "checked": [source],
        "warnings": warnings,
    }


def gemini_key() -> str | None:
    """The actual key, or None. Kept separate so reports never carry the secret."""
    env_key = (os.environ.get(ENV_VAR) or "").strip()
    if env_key:
        return env_key
    for path in (REPO_KEY_FILE, HOME_KEY_FILE):
        key = _read_key_file(path)
        if key and not (path == REPO_KEY_FILE and _is_tracked_by_git(path)):
            return key
    return None


def check() -> int:
    """Human-readable report. Exit code 0 = Gemini available, 1 = falling back."""
    info = resolve_backend()
    if info["backend"] == "gemini":
        print(f"backend : gemini  (model {info['model']})")
        print(f"key from: {info['source']}")
    else:
        print("backend : assistant  (the connected MCP client — default, no key needed)")
        print(f"reason  : {info['source']}")
        print("looked in:")
        for place in info["checked"]:
            print(f"  - {place}")
    sys.stdout.flush()  # else the stderr warnings land ABOVE the report they annotate
    for warning in info["warnings"]:
        print(f"\n!! {warning}", file=sys.stderr)
    return 0 if info["backend"] == "gemini" else 1


if __name__ == "__main__":
    raise SystemExit(check())
