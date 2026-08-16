"""Send source files to Gemini for review, over the API instead of a browser tab.

WHY THIS EXISTS. The peer review was being conducted by typing into gemini.google.com.
That works for a paragraph and fails for a codebase: ~9,200 lines across 30 modules is
a hundred-odd pasted chunks, roughly a third of which vanished silently when the page
dropped its connection, with no way to tell a delivered message from a lost one until
the reply came back about the wrong thing. Gemini also cannot fetch URLs — it was asked
directly and said so rather than guessing — so pointing it at the public repo was out.

Over the API a whole module goes in one call, the reply is saved to disk, and a failure
is an exception instead of an empty text box.

This is the ONE-SHOT front end: ask about some files, get an answer, done. For a
conversation — follow-ups, "what did you mean by that", a session you can read back —
use ``gemini_chat.py``, which shares this file's transport rather than copying it.

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
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "host"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backend  # noqa: E402

# Transport, preamble and model default live in `gemini_client`, shared with the chat
# window. They were defined here first; they moved the day a second front end appeared,
# rather than being copied — this project has already paid for that copy three times in
# its DSP code.
from gemini_client import DEFAULT_MODEL, GeminiError, ask, list_models  # noqa: E402

OUT_DIR = REPO_ROOT / "gemini_reviews"


def print_models(key: str) -> int:
    rows = list_models(key)
    for name, limit in rows:
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
        return print_models(key)
    if not args.files and not args.question:
        parser.error("give at least one file, or --question")

    sections = []
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
    try:
        # PREAMBLE goes as the system instruction now, not as the first line of the
        # prompt, so it is `ask`'s default rather than something spliced in here.
        reply = ask(prompt, key, model=args.model,
                    on_retry=lambda m: print(f"  {m}", file=sys.stderr))
    except GeminiError as exc:
        raise SystemExit(str(exc)) from None

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
