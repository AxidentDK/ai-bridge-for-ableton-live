"""Tests for host/backend.py — which model backend drives the bridge.

Pure logic over env vars and files: no Live, no bridge, no pytest.
The case that matters most is the FALLBACK — no key must never be an error,
because that is the normal state for anyone who has not set one up.
"""
import contextlib
import os
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))

import backend  # noqa: E402

# Long enough to pass the plausibility check, but deliberately NOT key-shaped: a
# realistic-looking literal would trip every future secret scan of this repo.
PLAUSIBLE = "NOT-A-KEY-test-fixture-0123456789"


@contextlib.contextmanager
def isolated(*, env=None, repo_text=None, home_text=None, tracked=False):
    """Point every lookup at a temp dir, so a REAL key on this machine can't leak in.

    Restores the environment and the patched module attributes unconditionally —
    a test that failed half-way must not poison the ones after it.
    """
    saved_env = os.environ.get(backend.ENV_VAR)
    saved = (backend.REPO_KEY_FILE, backend.HOME_KEY_FILE, backend._is_tracked_by_git)
    tmp = tempfile.mkdtemp(prefix="ai-bridge-backend-test-")
    try:
        os.environ.pop(backend.ENV_VAR, None)
        if env is not None:
            os.environ[backend.ENV_VAR] = env

        repo_file = os.path.join(tmp, "gemini_api_key.txt")
        home_file = os.path.join(tmp, "home_gemini_api_key.txt")
        for path, text in ((repo_file, repo_text), (home_file, home_text)):
            if text is not None:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)

        backend.REPO_KEY_FILE = _P(repo_file)
        backend.HOME_KEY_FILE = _P(home_file)
        backend._is_tracked_by_git = lambda path: tracked
        yield
    finally:
        backend.REPO_KEY_FILE, backend.HOME_KEY_FILE, backend._is_tracked_by_git = saved
        os.environ.pop(backend.ENV_VAR, None)
        if saved_env is not None:
            os.environ[backend.ENV_VAR] = saved_env
        shutil.rmtree(tmp, ignore_errors=True)


def _P(path):
    from pathlib import Path
    return Path(path)


def test_no_key_falls_back_to_assistant():
    with isolated():
        info = backend.resolve_backend()
        assert info["backend"] == "assistant", info
        assert info["warnings"] == []       # absence of a key is NOT a warning
        assert backend.gemini_key() is None


def test_env_var_wins_over_file():
    with isolated(env=PLAUSIBLE, repo_text="KEY_FROM_FILE_XXXXXXXXXX"):
        info = backend.resolve_backend()
        assert info["backend"] == "gemini", info
        assert info["source"] == f"${backend.ENV_VAR}", info
        assert backend.gemini_key() == PLAUSIBLE


def test_key_read_from_file_ignoring_comments():
    with isolated(repo_text=f"# a comment\n\n   {PLAUSIBLE}   \n# trailing\n"):
        assert backend.resolve_backend()["backend"] == "gemini"
        assert backend.gemini_key() == PLAUSIBLE   # stripped, comments skipped


def test_comment_only_file_is_treated_as_no_key():
    """The file we ship is all comments — that must read as 'no key', not a key."""
    with isolated(repo_text="# paste it below\n#\n\n"):
        assert backend.resolve_backend()["backend"] == "assistant"


def test_home_file_used_when_repo_file_absent():
    with isolated(home_text=PLAUSIBLE):
        info = backend.resolve_backend()
        assert info["backend"] == "gemini", info
        assert info["source"].endswith("home_gemini_api_key.txt"), info


def test_tracked_key_file_is_refused():
    """A tracked key file is one push from being public — refuse and fall back."""
    with isolated(repo_text=PLAUSIBLE, tracked=True):
        info = backend.resolve_backend()
        assert info["backend"] == "assistant", info
        assert any("TRACKING" in w for w in info["warnings"]), info
        assert backend.gemini_key() is None


def test_short_key_is_used_but_warns():
    with isolated(env="abc123"):
        info = backend.resolve_backend()
        assert info["backend"] == "gemini", info
        assert any("characters" in w for w in info["warnings"]), info


def test_report_never_contains_the_key():
    """resolve_backend() output gets printed and logged — it must stay safe."""
    with isolated(env=PLAUSIBLE):
        assert PLAUSIBLE not in repr(backend.resolve_backend())


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
