"""THE DIVERGENCE CHECK. ``shared_dsp.py`` is duplicated; this is what keeps it honest.

Two programs measure the same things and the DSP that does it lives in one file. That
file is copied rather than packaged, because the bridge ships with `dependencies = []`
and is not going to grow a pip dependency on the listener. A copy with no check is
exactly what has already failed here — three times, most memorably a tempo fix made in
the listener at 01:00 and undone in the bridge at 03:00 by a hand-port that carried the
warning comment across intact.

So the copy is checked, byte for byte, by SHA-256 of the whole file.

WHY THE WHOLE FILE AND NOT A SUBSTRING SEARCH. The obvious cheap version looks for a
few marker strings and passes when it finds them. It would also pass a copy in which
`_FLUX_FFT = 1024` had become `8192`, or a minus sign had moved — which is the entire
class of bug this is for. A whole-file hash has the further merit of FORCING
``shared_dsp.py`` to stay self-contained: it cannot grow a relative import or a
database handle without the other repo's copy failing to run, and a file that only runs
in one repo is not shared.

THIS FILE IS ITSELF IDENTICAL IN BOTH REPOS. It finds ``shared_dsp.py`` wherever the
local layout puts it, so it can be copied across with no edit — one less thing to
diverge.

No pytest: run the file.
"""
import hashlib
import os
import sys
import traceback

#: SHA-256 of the agreed contents of ``shared_dsp.py``.
#:
#: ⚠️ UPDATING THIS IS THE LAST STEP OF A DSP CHANGE, NOT THE FIRST. The order is:
#: edit the listener's copy, copy the file to the bridge, then put the new hash here —
#: in BOTH repos, in the same commit-worth of work. Changing this constant to make a
#: red test go green, without copying the file, defeats the whole arrangement and
#: recreates the exact failure it was built to prevent.
EXPECTED_SHA256 = "d7895fd99f417386019c010743fe3a0ae1ecc3665b51f84a379f77d0bd97f8e1"

#: Where the file lives in each repo. The listener owns it; the bridge carries the
#: copy. Both are listed so this test file needs no per-repo edit.
LAYOUTS = ("listener/shared_dsp.py", "host/shared_dsp.py")

#: The source of truth, stated in one place so every message below can quote it.
POLICY = (
    "shared_dsp.py has DIVERGED.\n"
    "\n"
    "  The listener repo (ai-bridge-listener/listener/shared_dsp.py) is the SOURCE OF\n"
    "  TRUTH. Make DSP changes there, run its tests, then copy the whole file to the\n"
    "  bridge (ai-bridge-for-ableton-live/host/shared_dsp.py) and update\n"
    "  EXPECTED_SHA256 in tests/test_shared_dsp_sync.py in BOTH repos.\n"
    "\n"
    "  Do not hand-port a change from one file to the other. That has been tried\n"
    "  three times in this project and failed three times — most recently a tempo fix\n"
    "  that was reproduced complete with the comment warning about the very bug the\n"
    "  port reintroduced. Copy the file.\n"
    "\n"
    "  If you meant to change the DSP, the listener's own suite is what says whether\n"
    "  the change is right:\n"
    "      python tests/test_features.py      (the maths still does what it claimed)\n"
    "      python tests/test_shared_dsp.py    (both programs still agree)\n")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _local_copy():
    for layout in LAYOUTS:
        path = os.path.join(ROOT, *layout.split("/"))
        if os.path.exists(path):
            return path
    return None


def _digest(path):
    with open(path, "rb") as handle:                  # BINARY: no newline translation
        return hashlib.sha256(handle.read()).hexdigest()


def _sibling_copy():
    """The other repo's copy, if it happens to be checked out next to this one.

    A direct file-to-file comparison is stronger than each side matching a constant,
    because it cannot be satisfied by editing the constant. It is opportunistic: CI
    with one repo checked out still gets the hash check.
    """
    override = os.environ.get("AI_BRIDGE_SIBLING_REPO")
    parent = os.path.dirname(ROOT)
    candidates = [override] if override else [
        os.path.join(parent, "ai-bridge-listener"),
        os.path.join(parent, "ai-bridge-for-ableton-live"),
    ]
    for repo in candidates:
        if not repo or os.path.abspath(repo) == os.path.abspath(ROOT):
            continue
        for layout in LAYOUTS:
            path = os.path.join(repo, *layout.split("/"))
            if os.path.exists(path):
                return path
    return None


def test_shared_dsp_matches_the_agreed_contents():
    """The local copy hashes to the value both repos record."""
    path = _local_copy()
    if path is None:
        raise AssertionError(
            "shared_dsp.py is not present in this repo.\n"
            f"  Looked for: {', '.join(LAYOUTS)} under {ROOT}\n"
            "  If the shared DSP core has not been installed here yet, copy\n"
            "  ai-bridge-listener/listener/shared_dsp.py to host/shared_dsp.py.\n\n"
            + POLICY)
    actual = _digest(path)
    if actual == EXPECTED_SHA256:
        return
    with open(path, "rb") as handle:
        raw = handle.read()
    hint = ""
    if b"\r\n" in raw:
        # Worth naming explicitly: a CRLF checkout changes every line and the hash with
        # it, while the file reads as byte-identical in every editor and diff tool.
        hint = ("\n  NOTE: this copy has CRLF line endings. The file is stored with LF.\n"
                "  Check core.autocrlf / .gitattributes before assuming a real edit.\n")
    raise AssertionError(
        f"{POLICY}\n"
        f"  file     {path}\n"
        f"  expected {EXPECTED_SHA256}\n"
        f"  actual   {actual}{hint}")


def test_both_repos_carry_the_same_bytes():
    """When both checkouts are present, compare them directly — no constant involved."""
    mine, theirs = _local_copy(), _sibling_copy()
    if mine is None or theirs is None:
        # Not a failure: one repo may legitimately be checked out alone, and the other
        # may not have received the copy yet. Said out loud so nobody reads a green
        # run as "both repos agree" when only one was looked at.
        print("        (the other repo's copy was not found — hash check alone)")
        return
    if _digest(mine) == _digest(theirs):
        print(f"        matches {theirs}")
        return
    raise AssertionError(
        f"{POLICY}\n"
        f"  this repo  {mine}  {_digest(mine)}\n"
        f"  other repo {theirs}  {_digest(theirs)}")


def test_the_shared_core_has_no_project_imports():
    """Self-containment, checked rather than trusted.

    A whole-file hash only works if the file can actually RUN in both repos. One
    `from .db import ...` and the bridge's copy is a syntactically valid file that
    raises on import — which the hash would happily call identical.
    """
    path = _local_copy()
    if path is None:
        return                                    # the test above already failed
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    allowed = {"import numpy as np", "from math import gcd",
               "from __future__ import annotations"}
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        if stripped in allowed or stripped.startswith("#"):
            continue
        raise AssertionError(
            f"{path}:{number} imports something outside numpy and the stdlib:\n"
            f"    {stripped}\n"
            "  The shared core must run unchanged in both repos. Anything it needs\n"
            "  from a project belongs in the caller, not here.")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
