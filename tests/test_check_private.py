"""Tests for scripts/check_private.py — the pre-push privacy gate.

A checker nobody tests is a checker that silently stops catching things, and this one
guards a PUBLIC repo. Both directions matter equally: it must catch the real leaks,
and it must stay quiet on install paths that are identical on every machine — because
a checker that cries wolf gets disabled, and a disabled checker protects nothing.
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_private as cp  # noqa: E402


def rules(text, terms=()):
    return {rule for _, rule, _, _ in cp.scan_text(text, list(terms))}


# The fixtures below are invented, but they are BY DESIGN the exact shapes the
# checker hunts for — so scanning this file finds them. That is the checker working,
# not failing: on the very first push it blocked itself. Hence the markers.
def test_catches_real_windows_home():
    assert "windows home" in rules(r"the device points at C:/Users/someone/.x")  # noqa: private
    assert "windows home" in rules(r"see C:\Users\someone\AppData\Local")  # noqa: private


def test_catches_unix_home():
    assert "unix home" in rules("/home/someone/.ableton")  # noqa: private
    assert "unix home" in rules("/Users/someone/Music")  # noqa: private


def test_catches_keys_and_email():
    assert "google api key" in rules("key = AIza" + "b" * 35)
    assert "gemini api key" in rules("key = AQ." + "c" * 45)
    assert "openai key" in rules("key = sk-" + "d" * 25)
    assert "email" in rules("contact me at someone@example.com")  # noqa: private
    assert "private key" in rules("-----BEGIN RSA PRIVATE KEY-----")  # noqa: private


def test_catches_lan_addresses():
    assert "private ip" in rules("the NAS is at 192.168.1.40")  # noqa: private
    assert "private ip" in rules("http://10.0.0.5:8443/")  # noqa: private


def test_loopback_is_not_a_leak():
    """127.0.0.1 is in the docs legitimately — the bridge listens on it."""
    assert rules("the bridge listens on 127.0.0.1:8765") == set()


def test_quiet_on_machine_independent_paths():
    """These are the same on every machine — flagging them would train people to
    ignore the checker, which is worse than not having one."""
    for safe in (r"C:\ProgramData\Ableton\Live 12 Suite",
                 r"%LOCALAPPDATA%\Ableton\Live Database",
                 r"%USERPROFILE%\Documents",
                 r"points at <user home>/.ableton-live-mcp/",
                 r"D:\Packs\kick.wav"):
        assert rules(safe) == set(), f"false positive on {safe!r}"


def test_private_terms_are_matched_case_insensitively():
    assert "private term" in rules("see My-Sample-Pack/kicks", terms=["my-sample-pack"])
    assert rules("see other/kicks", terms=["my-sample-pack"]) == set()


def test_noqa_silences_a_line():
    assert rules(r"C:\Users\someone\x  # noqa: private") == set()


def test_binary_embedded_strings_are_scanned():
    """The leak that motivated this: a built Max device with the builder's home
    directory baked into it. Skipping binaries walked straight past a tracked file
    holding a real path, so binaries now get their strings read."""
    blob = (b"\x00\x01\xff\xfe" + b"maxclass\x00"
            + b'"text": "js tap.js C:/Users/someone/.state/cmd.json"'  # noqa: private
            + b"\x00\xff")
    assert "windows home" in rules(cp.extract_strings(blob))


def test_extracted_strings_ignore_short_noise():
    assert cp.extract_strings(b"\x00ab\x01cd\xff") == ""


def test_the_repo_itself_is_clean():
    """The real entry point: run the checker over every tracked file, as the hook does."""
    assert cp.main([]) == 0


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
