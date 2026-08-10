"""Host API tests — the cleanup-unused-tracks logic. No Live, no pytest."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))

from api import Live  # noqa: E402


class FakeBridge:
    """Models a set of tracks: {name, devices, arr(clips), slots(list of has_clip)}."""

    def __init__(self, tracks):
        self.tracks = tracks
        self.deleted = []

    def get(self, path, prop):
        if path == "live_set" and prop == "tracks":
            return list(range(len(self.tracks)))
        parts = path.split()
        t = self.tracks[int(parts[2])]
        if len(parts) == 3:
            return {"devices": t["devices"], "arrangement_clips": t["arr"],
                    "clip_slots": [None] * len(t["slots"]), "name": t["name"]}[prop]
        if len(parts) == 5 and parts[3] == "clip_slots":
            return t["slots"][int(parts[4])] if prop == "has_clip" else None
        raise KeyError(path + " " + prop)

    def call(self, path, func, *args):
        if func == "delete_track":
            self.deleted.append(self.tracks.pop(args[0])["name"])


def make():
    return FakeBridge([
        {"name": "empty1", "devices": [], "arr": [], "slots": [False, False]},
        {"name": "hasClip", "devices": [], "arr": [], "slots": [True, False]},
        {"name": "instrumented", "devices": [{"name": "Operator"}], "arr": [], "slots": [False]},
        {"name": "hasArrClip", "devices": [], "arr": [{"x": 1}], "slots": [False]},
        {"name": "empty2", "devices": [], "arr": [], "slots": [False]},
    ])


class RecordingBridge(FakeBridge):
    def __init__(self):
        super().__init__([])
        self.calls = []

    def call(self, path, func, *args):
        self.calls.append((path, func, args))


def test_view_switch_helpers():
    b = RecordingBridge()
    live = Live(b)
    live.show_arranger()
    live.show_session()
    live.show_view("Detail/Clip")
    assert b.calls == [
        ("live_app view", "show_view", ("Arranger",)),
        ("live_app view", "show_view", ("Session",)),
        ("live_app view", "show_view", ("Detail/Clip",)),
    ]


def test_unused_detection():
    live = Live(make())
    unused = [t["name"] for t in live.unused_tracks()]
    assert unused == ["empty1", "empty2"]  # not hasClip / instrumented / hasArrClip


def test_dry_run_removes_nothing():
    b = make()
    r = Live(b).cleanup_unused_tracks(dry_run=True)
    assert r["count"] == 2 and [t["name"] for t in r["would_remove"]] == ["empty1", "empty2"]
    assert b.deleted == [] and len(b.tracks) == 5


def test_cleanup_removes_only_empty():
    b = make()
    r = Live(b).cleanup_unused_tracks()
    assert r["count"] == 2 and r["remaining"] == 3
    assert set(b.deleted) == {"empty1", "empty2"}
    assert [t["name"] for t in b.tracks] == ["hasClip", "instrumented", "hasArrClip"]


def test_cleanup_high_index_first_keeps_last():
    # all-empty: deletes high-index-first, but keeps one (Live needs >=1 track)
    b = FakeBridge([{"name": f"e{i}", "devices": [], "arr": [], "slots": [False]} for i in range(4)])
    r = Live(b).cleanup_unused_tracks()
    assert r["count"] == 4 and len(r["removed"]) == 3 and r["remaining"] == 1
    assert "kept_empty" in r and len(b.tracks) == 1
    assert b.deleted == ["e3", "e2", "e1"]  # high index first, e0 survives


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
