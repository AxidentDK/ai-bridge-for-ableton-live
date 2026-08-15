"""Send source files to Gemini for review, over the API instead of a browser tab.

WHY THIS EXISTS. The peer review was being conducted by typing into gemini.google.com.
That works for a paragraph and fails for a codebase: ~9,200 lines across 30 modules is
a hundred-odd pasted chunks, roughly a third of which vanished silently when the page
dropped its connection, with no way to tell a delivered message from a lost one until
the reply came back about the wrong thing. Gemini also cannot fetch URLs — it was asked
directly and said so rather than guessing — so pointing it at the public repo was out.

Over the API a whole module goes in one call, the reply is saved to disk, and a failure
is an exception instead of an empty text box.

    python tools/ask_gemini.py listener/features.py --focus "DSP and musical correctness"
    python tools/ask_gemini.py host/describe.py host/audio_features.py
    python tools/ask_gemini.py --models          # what this key can actually reach

The key comes from ``host/backend.py`` — the same resolution and the same safety check
the bridge itself uses, rather than a second copy that could disagree with it. Nothing
here is ever written back into the repo: replies land in ``gemini_reviews/``, which is
gitignored.

Stdlib only. The bridge is deliberately near-zero-dependency and a review tool is no
reason to break that.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "host"))

import backend  # noqa: E402

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
OUT_DIR = REPO_ROOT / "gemini_reviews"

#: Verified against the live model list for this key rather than recalled: a Pro tier
#: with a 1M-token context window, so a 1,700-line module arrives whole instead of in
#: fragments. Override with --model or $GEMINI_MODEL; `--models` prints what is
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
- Index today: 29,869 files, 1.36M tags. Tempo is 69% within 2.5 BPM of a
  filename-stated tempo (octave errors counted as errors, deliberately). Pitch class
  matches a filename-named note 74% of the time.

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


def _post(model: str, key: str, prompt: str, timeout: int = 300) -> str:
    url = f"{API_ROOT}/models/{model}:generateContent"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        # The key is never echoed: it travels in a header, so it cannot appear in a
        # URL that an error message might quote back.
        raise SystemExit(f"Gemini API HTTP {exc.code}: {detail}") from None
    candidates = payload.get("candidates") or []
    if not candidates:
        raise SystemExit(f"no candidates returned: {json.dumps(payload)[:400]}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts).strip()
    if not text:
        reason = candidates[0].get("finishReason", "unknown")
        raise SystemExit(f"empty reply (finishReason={reason})")
    return text


def list_models(key: str) -> int:
    url = f"{API_ROOT}/models"
    request = urllib.request.Request(url, headers={"x-goog-api-key": key})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    rows = [
        (m["name"].removeprefix("models/"), m.get("inputTokenLimit", 0))
        for m in payload.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    for name, limit in sorted(rows):
        print(f"  {name:<44} {limit:>9,} input tokens")
    print(f"\n{len(rows)} models accept generateContent. Default: {DEFAULT_MODEL}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path,
                        help="source files to review (relative to either repo)")
    parser.add_argument("--focus", default="",
                        help="what to concentrate on for these particular files")
    parser.add_argument("--question", default="",
                        help="ask something directly instead of reviewing files")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--models", action="store_true",
                        help="list the models this key can reach, and stop")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    key = backend.gemini_key()
    if not key:
        info = backend.resolve_backend()
        for warning in info.get("warnings", []):
            print(f"  {warning}", file=sys.stderr)
        print("No Gemini key found. Set $GEMINI_API_KEY or create "
              f"{backend.KEY_FILENAME} in the repo root.", file=sys.stderr)
        return 2

    if args.models:
        return list_models(key)
    if not args.files and not args.question:
        parser.error("give at least one file, or --question")

    sections = [PREAMBLE]
    if args.question:
        sections.append(args.question)
    for path in args.files:
        candidate = path if path.exists() else REPO_ROOT / path
        if not candidate.exists():
            # The listener lives in a sibling repo; look there before giving up.
            sibling = REPO_ROOT.parent / "ai-bridge-listener" / path
            candidate = sibling if sibling.exists() else candidate
        if not candidate.exists():
            print(f"not found: {path}", file=sys.stderr)
            return 2
        code = candidate.read_text(encoding="utf-8")
        sections.append(f"--- FILE: {path} ({len(code.splitlines())} lines) ---\n"
                        f"```python\n{code}\n```")
    if args.focus:
        sections.append(f"FOCUS FOR THIS REVIEW: {args.focus}")
    if args.files:
        sections.append(
            "Review the file(s) above. Lead with anything you believe is WRONG, "
            "musically or numerically, and say how you would test it. If you find "
            "nothing wrong, say so plainly rather than inventing something.")

    prompt = "\n\n".join(sections)
    print(f"model {args.model} | {len(prompt):,} chars "
          f"(~{len(prompt)//4:,} tokens) | {len(args.files)} file(s)")
    reply = _post(args.model, key, prompt)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    label = "-".join(p.stem for p in args.files) or "question"
    target = args.out / f"{stamp}-{label}.md"
    target.write_text(f"# Gemini review: {label}\n\n"
                      f"model: {args.model}\n\n{reply}\n", encoding="utf-8")
    print(f"\n{reply}\n")
    print(f"[saved to {target}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
