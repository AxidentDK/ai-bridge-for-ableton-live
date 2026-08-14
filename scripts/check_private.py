"""Refuse to publish private paths, secrets or personal details.

This repo is PUBLIC. Development notes drift into it naturally — a real path pasted
into a gotcha, a sample-library folder name in a test fixture, an account detail in a
research note. None of it is dangerous on its own; it is simply nobody else's
business, and once pushed it stays in the history.

Run it directly, or let the pre-push hook run it:

    python scripts/check_private.py            # scan every tracked file
    python scripts/check_private.py --staged   # scan what is staged

Personal terms (your own folder names, machine names, handles) go in a **gitignored**
`.private-terms` file, one per line — because a checker that hard-codes them would
publish the very thing it is meant to hide. See `.private-terms.example`.

Exit code 0 = clean, 1 = findings. False positives are silenced with an inline
`# noqa: private` (or `<!-- noqa: private -->` in Markdown) on the same line.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TERMS_FILE = REPO_ROOT / ".private-terms"
ALLOW_MARKER = "noqa: private"

# (name, pattern, why it matters). Deliberately narrow: a checker that cries wolf
# gets disabled, and a disabled checker protects nothing.
PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("windows home", re.compile(r"[A-Za-z]:[\\/]Users[\\/](?!<)[A-Za-z0-9._-]+", re.I),
     "a real Windows user folder — use <user home> or %USERPROFILE%"),
    ("unix home", re.compile(r"/(?:home|Users)/(?!<)[a-z0-9._-]+", re.I),
     "a real home directory — use ~ or <user home>"),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
     "an email address"),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "a Google API key"),
    ("gemini api key", re.compile(r"\bAQ\.[0-9A-Za-z_\-]{40,}"), "a Gemini API key"),
    ("openai key", re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "an OpenAI-style key"),
    ("private key", re.compile(r"BEGIN\s+(?:RSA\s+|OPENSSH\s+|EC\s+)?PRIVATE KEY"),
     "a private key block"),
    ("private ip", re.compile(r"\b(?:192\.168\.\d{1,3}|10\.\d{1,3}\.\d{1,3}"
                              r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3})\.\d{1,3}\b"),
     "a LAN address from your network"),
]

# Paths that are the SAME on every machine are not private. Without these the
# "windows home" rule would fire on ordinary install locations.
SAFE_SUBSTRINGS = (
    r"C:\ProgramData", r"C:/ProgramData",
    "%APPDATA%", "%LOCALAPPDATA%", "%USERPROFILE%",
    "<user home>", "<username>", "/Users/<", "C:\\Users\\<",
)

# Formats with no meaningful embedded text — genuinely nothing to leak.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".wav", ".aif", ".aiff",
                 ".mp3", ".flac", ".zip", ".pdf"}

# Runs of printable ASCII this long inside a binary are treated as text.
_MIN_STRING_RUN = 8
_ASCII_RUN = re.compile(rb"[\x20-\x7e]{%d,}" % _MIN_STRING_RUN)


def extract_strings(data: bytes) -> str:
    """Printable runs inside a binary, one per line.

    Binaries were originally skipped outright, and that was wrong: a built Max
    device (.amxd) had an absolute path containing the builder's home directory
    baked into it, and skipping the file meant the checker walked straight past a
    real leak sitting in a tracked file. Anything that can hold a string gets read.
    """
    return "\n".join(m.decode("ascii", "replace") for m in _ASCII_RUN.findall(data))


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def load_terms() -> list[str]:
    """Personal terms from the gitignored .private-terms, lower-cased."""
    try:
        raw = TERMS_FILE.read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln.strip().lower() for ln in raw.splitlines()
            if ln.strip() and not ln.startswith("#")]


def scan_text(text: str, terms: list[str]) -> list[tuple[int, str, str, str]]:
    """Findings as (line_no, rule, matched text, why)."""
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        haystack = line.lower()
        for name, pattern, why in PATTERNS:
            for match in pattern.finditer(line):
                hit = match.group(0)
                if any(safe.lower() in haystack for safe in SAFE_SUBSTRINGS
                       if safe.lower() in hit.lower() or hit.lower() in safe.lower()):
                    continue
                if any(haystack.count(safe.lower()) and safe.lower() in line.lower()
                       and hit.lower().startswith(safe.lower()) for safe in SAFE_SUBSTRINGS):
                    continue
                findings.append((lineno, name, hit, why))
        for term in terms:
            if term in haystack:
                findings.append((lineno, "private term", term,
                                 "listed in .private-terms"))
    return findings


def files_to_scan(staged: bool) -> list[str]:
    if staged:
        return _git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    return _git("ls-files")


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    terms = load_terms()
    total = 0
    scanned = 0

    for rel in files_to_scan(staged):
        path = REPO_ROOT / rel
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        binary = False
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Not text — read the strings out of it rather than walking past.
            try:
                text = extract_strings(path.read_bytes())
            except OSError:
                continue
            binary = True
        except OSError:
            continue
        scanned += 1
        findings = scan_text(text, terms)
        if findings:
            print(f"\n{rel}{'   [binary — embedded strings]' if binary else ''}")
            for lineno, rule, hit, why in findings:
                where = f"string {lineno}" if binary else f"line {lineno}"
                print(f"  {where}: [{rule}] {hit}")
                print(f"      -> {why}")
            total += len(findings)

    where = "staged files" if staged else "tracked files"
    if total:
        print(f"\nBLOCKED: {total} finding(s) in {scanned} {where}.")
        print("Fix them, or add '# noqa: private' on the line if it is a false positive.")
        return 1

    note = "" if terms else "  (no .private-terms file — pattern rules only)"
    print(f"clean: no private paths or secrets in {scanned} {where}.{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
