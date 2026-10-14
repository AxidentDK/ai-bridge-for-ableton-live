"""sound_search_setup, offline: layout, idempotence, update bookkeeping, scan command.

The network half (release lookup, downloads, pip) was proven end to end against the
real GitHub release before this file existed; these tests pin the parts that can
break silently in a refactor — paths, the never-overwrite rule, and what the scan
subprocess is told to do. No network, no pip, no Live.
"""
import json
import os
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))

import sound_search_setup as sss  # noqa: E402


def with_home(fn):
    def run():
        with tempfile.TemporaryDirectory() as td:
            os.environ["AI_BRIDGE_HOME"] = td
            try:
                fn(Path(td))
            finally:
                del os.environ["AI_BRIDGE_HOME"]
    run.__name__ = fn.__name__
    return run


@with_home
def test_status_names_whats_missing(home):
    st = sss.status()
    assert not st["installed"]
    assert set(st["missing"]) == {"listener", "dependencies", "models", "wrapper"}
    assert st["indexed"] is None and st["release"] is None


@with_home
def test_extract_never_overwrites(home):
    zpath = home / "a.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("models/kept.txt", "NEW")
        z.writestr("models/fresh.txt", "fresh")
    (home / "models").mkdir()
    (home / "models" / "kept.txt").write_text("MINE")
    written = sss._extract_missing(zpath, home, lambda m: None, "models")
    assert written == 1
    assert (home / "models" / "kept.txt").read_text() == "MINE"
    assert (home / "models" / "fresh.txt").read_text() == "fresh"


@with_home
def test_release_marker_and_update_check(home):
    assert sss.installed_release() is None
    (home / "listener").mkdir(parents=True)
    sss._marker().write_text("v1.0.0")
    assert sss.installed_release() == "v1.0.0"

    import urllib.request
    real = urllib.request.urlopen

    class Fake:
        def __init__(self, tag):
            self.tag = tag
        def read(self):
            return json.dumps({"tag_name": self.tag}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    try:
        urllib.request.urlopen = lambda req, **kw: Fake("v1.0.0")
        assert not sss.check_update()["update_available"]
        urllib.request.urlopen = lambda req, **kw: Fake("v1.1.0")
        upd = sss.check_update()
        assert upd["update_available"] and upd["latest"] == "v1.1.0"
    finally:
        urllib.request.urlopen = real


@with_home
def test_scan_command_speaks_listener_cli(home):
    cmd = sss.scan_command()
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("run_listener.py")
    assert "--out" in cmd and str(home / "sound_index.db") in cmd
    assert "--folder" not in cmd              # no folder means Live's own database
    cmd = sss.scan_command(["A", "B"], limit=9)
    assert cmd.count("--folder") == 2 and cmd[-1] == "9"


@with_home
def test_wrapper_reaches_src_and_site(home):
    assert "src" in sss._WRAPPER and "site" in sss._WRAPPER
    assert "runpy" in sss._WRAPPER and "listener" in sss._WRAPPER


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception:
            print(f"  FAIL  {t.__name__}")
            traceback.print_exc()
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        sys.exit(1)
