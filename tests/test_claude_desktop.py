"""install.py's Claude Desktop connection, offline, against a settings file in a temp folder.

What must never break: other servers and settings survive byte-for-byte in meaning, an
unreadable file is never written, the first write leaves a backup, uninstall removes only
an entry that points at this copy, and the Mac's 'auto' mode does not overrule a user who
removed the entry.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import install  # noqa: E402


class Desk:
    """Stand-ins for the machine: whether Claude Desktop runs, the helper, RunOnce. The
    real ones must never act from a test - Claude Desktop may well be running here."""
    running = False
    helpers = 0
    run_once = False


PATCHED = ("claude_desktop_config", "claude_desktop_present", "claude_desktop_running",
           "_start_helper", "_set_run_once")


def with_config(fn):
    def run():
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "Claude" / "claude_desktop_config.json"
            saved = {name: getattr(install, name) for name in PATCHED}
            Desk.running, Desk.helpers, Desk.run_once = False, 0, False
            install.claude_desktop_config = lambda: cfg
            install.claude_desktop_present = lambda: True
            install.claude_desktop_running = lambda: Desk.running
            install._start_helper = lambda: setattr(Desk, "helpers", Desk.helpers + 1)
            install._set_run_once = lambda on: setattr(Desk, "run_once", on)
            os.environ["AI_BRIDGE_HOME"] = str(Path(td) / "home")
            try:
                fn(cfg)
            finally:
                for name, value in saved.items():
                    setattr(install, name, value)
                del os.environ["AI_BRIDGE_HOME"]
    run.__name__ = fn.__name__
    return run


def claude_desktop_saves(cfg, before):
    """What Claude Desktop does when it quits: writes its own copy from memory."""
    cfg.write_text(json.dumps(before), encoding="utf-8")
    Desk.running = False


@with_config
def test_running_claude_desktop_gets_a_helper_and_the_entry_survives_its_save(cfg):
    cfg.parent.mkdir(parents=True)
    in_memory = {"mcpServers": dict(OTHER), "preferences": {"x": 1}}
    cfg.write_text(json.dumps(in_memory), encoding="utf-8")
    Desk.running = True
    assert install.connect_claude_desktop("on") == 0
    assert Desk.helpers == 1 and Desk.run_once
    assert install._wish_path().read_text() == "on"
    claude_desktop_saves(cfg, in_memory)                     # the entry is gone again here
    assert "ai-bridge" not in read(cfg)["mcpServers"]
    assert install.wait_for_claude_desktop() == 0            # the helper, after the quit
    after = read(cfg)
    assert after["mcpServers"]["ai-bridge"] == install.claude_server_entry()
    assert after["preferences"] == {"x": 1} and after["mcpServers"]["blender"] == OTHER["blender"]
    assert not install._wish_path().exists() and not Desk.run_once


@with_config
def test_a_later_wish_replaces_an_earlier_one(cfg):
    Desk.running = True
    install.connect_claude_desktop("on")
    install.disconnect_claude_desktop()                      # untick before Claude Desktop quit
    Desk.running = False
    install.wait_for_claude_desktop()
    assert "ai-bridge" not in read(cfg)["mcpServers"]


@with_config
def test_not_running_needs_no_helper(cfg):
    install.connect_claude_desktop("on")
    assert Desk.helpers == 0 and not Desk.run_once and not install._wish_path().exists()


@with_config
def test_uninstall_starts_no_helper(cfg):
    install.connect_claude_desktop("on")
    Desk.running = True
    install.disconnect_claude_desktop(repeat=False)
    assert Desk.helpers == 0 and "ai-bridge" not in read(cfg)["mcpServers"]


@with_config
def test_auto_counts_as_connected_only_after_the_entry_survived(cfg):
    Desk.running = True
    install.connect_claude_desktop("auto")
    assert not install._connect_marker().exists()            # not yet: Claude Desktop may undo it
    claude_desktop_saves(cfg, {"mcpServers": {}})
    install.connect_claude_desktop("auto")                   # the next launch tries again
    assert "ai-bridge" in read(cfg)["mcpServers"] and install._connect_marker().exists()


def read(cfg):
    return json.loads(cfg.read_text(encoding="utf-8"))


OTHER = {"blender": {"command": "uv", "args": ["run", "blender-mcp"]}}


@with_config
def test_creates_file_when_none(cfg):
    assert install.connect_claude_desktop("on") == 0
    assert read(cfg)["mcpServers"]["ai-bridge"] == install.claude_server_entry()
    assert not cfg.with_name(install.CLAUDE_BACKUP).exists()


@with_config
def test_keeps_other_servers_and_settings_and_backs_up(cfg):
    cfg.parent.mkdir(parents=True)
    before = {"mcpServers": dict(OTHER), "globalShortcut": "Ctrl+Space"}
    cfg.write_text("\ufeff" + json.dumps(before), encoding="utf-8")   # Notepad's BOM
    assert install.connect_claude_desktop("on") == 0
    after = read(cfg)
    assert after["globalShortcut"] == "Ctrl+Space"
    assert after["mcpServers"]["blender"] == OTHER["blender"]
    assert list(after["mcpServers"]) == ["blender", "ai-bridge"]
    assert cfg.with_name(install.CLAUDE_BACKUP).exists()


@with_config
def test_unreadable_file_is_left_alone(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"mcpServers": {"blender": ', encoding="utf-8")
    assert install.connect_claude_desktop("on") == 3
    assert cfg.read_text(encoding="utf-8") == '{"mcpServers": {"blender": '
    assert not cfg.with_name(install.CLAUDE_BACKUP).exists()


@with_config
def test_second_connect_writes_nothing(cfg):
    install.connect_claude_desktop("on")
    stamp = cfg.stat().st_mtime_ns
    install.connect_claude_desktop("on")
    assert cfg.stat().st_mtime_ns == stamp


@with_config
def test_disconnect_removes_only_our_entry(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": dict(OTHER)}), encoding="utf-8")
    install.connect_claude_desktop("on")
    install.disconnect_claude_desktop()
    assert read(cfg)["mcpServers"] == OTHER


@with_config
def test_disconnect_keeps_an_entry_for_another_copy(cfg):
    other_copy = {"command": "python", "args": [r"C:\dev\ai-bridge\host\mcp_server.py"]}
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"ai-bridge": other_copy}}), encoding="utf-8")
    install.disconnect_claude_desktop()
    assert read(cfg)["mcpServers"]["ai-bridge"] == other_copy


@with_config
def test_auto_connects_once_then_respects_removal(cfg):
    assert install.connect_claude_desktop("auto") == 0
    assert "ai-bridge" in read(cfg)["mcpServers"]
    install.disconnect_claude_desktop()
    install.connect_claude_desktop("auto")
    assert "ai-bridge" not in read(cfg)["mcpServers"]


@with_config
def test_auto_follows_a_moved_app(cfg):
    install.connect_claude_desktop("auto")
    data = read(cfg)
    data["mcpServers"]["ai-bridge"] = {
        "command": "/Volumes/Downloads/Gemini Studio (Ableton).app/Contents/Resources/python/bin/python3",
        "args": ["/Volumes/Downloads/Gemini Studio (Ableton).app/Contents/Resources/app/host/mcp_server.py"]}
    cfg.write_text(json.dumps(data), encoding="utf-8")
    install.connect_claude_desktop("auto")
    assert read(cfg)["mcpServers"]["ai-bridge"] == install.claude_server_entry()


@with_config
def test_auto_does_nothing_without_claude_desktop(cfg):
    install.claude_desktop_present = lambda: False
    assert install.connect_claude_desktop("auto") == 0
    assert not cfg.exists()


def test_windows_picks_the_claude_data_folder_in_use():
    if os.name != "nt":
        return
    with tempfile.TemporaryDirectory() as td:
        saved = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "APPDATA")}
        os.environ["LOCALAPPDATA"] = str(Path(td) / "Local")
        os.environ["APPDATA"] = str(Path(td) / "Roaming")
        try:
            assert install.claude_desktop_dir() == Path(td) / "Roaming" / "Claude"
            data = Path(td) / "Local" / "Claude-Data"
            for name, age in (("claude_old", 100), ("claude_now", 0)):
                (data / name).mkdir(parents=True)
                cfg = data / name / "config.json"
                cfg.write_text("{}")
                t = cfg.stat().st_mtime - age
                os.utime(cfg, (t, t))
            (data / "no_config").mkdir()
            assert install.claude_desktop_dir() == data / "claude_now"
            assert install.claude_desktop_present()
        finally:
            for k, v in saved.items():
                os.environ[k] = v


def test_entry_uses_console_python():
    saved = sys.executable
    try:
        sys.executable = r"C:\AI Bridge\python\pythonw.exe"
        assert Path(install.claude_server_entry()["command"]).name == "python.exe"
    finally:
        sys.executable = saved


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
