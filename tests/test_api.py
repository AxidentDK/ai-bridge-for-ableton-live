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
            # `is_foldable` marks a GROUP track. Real Live exposes it on every track,
            # and cleanup must never propose deleting a group — Live takes the group's
            # children with it. Defaults False so existing cases stay plain tracks.
            return {"devices": t["devices"], "arrangement_clips": t["arr"],
                    "clip_slots": [None] * len(t["slots"]), "name": t["name"],
                    "is_foldable": t.get("is_foldable", False)}[prop]
        if len(parts) == 5 and parts[3] == "clip_slots":
            return t["slots"][int(parts[4])] if prop == "has_clip" else None
        raise KeyError(path + " " + prop)

    def get_many(self, pairs):
        # same semantics as the real client: batched reads, values in order
        return [self.get(p, pr) for p, pr in pairs]

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
        # An empty GROUP track: no devices, no clips — and deleting it in Live takes
        # its children with it, so "unused" must never include it.
        {"name": "emptyGroup", "devices": [], "arr": [], "slots": [False],
         "is_foldable": True},
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
    assert b.deleted == [] and len(b.tracks) == 6


def test_cleanup_removes_only_empty():
    b = make()
    r = Live(b).cleanup_unused_tracks()
    assert r["count"] == 2 and r["remaining"] == 4
    assert set(b.deleted) == {"empty1", "empty2"}
    # The empty GROUP survives: it has no devices and no clips, but deleting it in
    # Live would take its children with it.
    assert [t["name"] for t in b.tracks] == ["hasClip", "instrumented", "hasArrClip",
                                             "emptyGroup"]


class ParamBridge:
    """A device with n parameters; counts wire calls to prove batching."""

    def __init__(self, n):
        self.n = n
        self.wire_calls = 0

    def get(self, path, prop):
        self.wire_calls += 1
        assert prop == "parameters"
        return [None] * self.n

    def get_many(self, pairs):
        self.wire_calls += 1  # one batch = one wire call (chunking aside)
        out = []
        for path, prop in pairs:
            i = int(path.split()[-1])
            out.append({"name": f"P{i}", "value": float(i), "min": 0.0, "max": 1.0}[prop])
        return out


def test_parameters_batched_two_wire_calls():
    b = ParamBridge(66)  # the real Drift case that took ~65 s unbatched
    params = Live(b).parameters(track=0, device=0)
    assert b.wire_calls == 2  # count + one batched read — not 1 + 66*4
    assert len(params) == 66
    assert params[0] == {"index": 0, "name": "P0", "value": 0.0, "min": 0.0, "max": 1.0}
    assert params[65]["name"] == "P65" and params[65]["value"] == 65.0


def test_cleanup_high_index_first_keeps_last():
    # all-empty: deletes high-index-first, but keeps one (Live needs >=1 track)
    b = FakeBridge([{"name": f"e{i}", "devices": [], "arr": [], "slots": [False]} for i in range(4)])
    r = Live(b).cleanup_unused_tracks()
    assert r["count"] == 4 and len(r["removed"]) == 3 and r["remaining"] == 1
    assert "kept_empty" in r and len(b.tracks) == 1
    assert b.deleted == ["e3", "e2", "e1"]  # high index first, e0 survives


class TakesBridge:
    """Fake for the comping flow: two take lanes, records every set/call."""

    def __init__(self):
        self.values = {("live_set", "count_in_duration"): 2,  # READ-ONLY in Live
                       ("live_set", "clip_trigger_quantization"): 4,
                       ("live_set", "signature_numerator"): 4,
                       ("live_set", "tempo"): 60000.0,  # absurd tempo -> ~0s sleep
                       ("live_set tracks 1", "can_be_armed"): True}
        self.log = []

    LANE_CLIP = "live_set tracks 1 take_lanes 0 arrangement_clips 0"

    def get(self, path, prop):
        if prop == "take_lanes":
            return [None, None]
        if path == self.LANE_CLIP:
            return {"start_time": 8.0, "length": 4.0, "is_audio_clip": False,
                    "name": "take 1"}[prop]
        if path.endswith("arrangement_clips 0") and prop == "start_time":
            return 8.0  # the recording's main-lane clip sits in the comp span
        if path.endswith("arrangement_clips 0") and prop == "name":
            return "old main"
        if prop == "arrangement_clips":
            return [{"name": "old main"}]
        return self.values.get((path, prop), 0)

    def get_many(self, pairs):
        return [self.get(p, pr) for p, pr in pairs]

    def set(self, path, prop, value):
        self.log.append(("set", path, prop, value))
        self.values[(path, prop)] = value

    def call(self, path, func, *args):
        self.log.append(("call", path, func, args))

    def request(self, method, **params):
        self.log.append(("request", method, params))
        if method == "clip_get_notes":
            return [{"pitch": 48, "start_time": 0.0, "duration": 4.0,
                     "velocity": 100.0, "mute": False}]
        return 1


def test_record_takes_sequence_and_restore():
    b = TakesBridge()
    r = Live(b).record_takes(track=1, start_beats=8.0, length_beats=4.0, passes=2,
                             extra_wait=0.0)
    assert r["passes"] == 2 and len(r["takes"]) == 2 and r["main_clips"] == 1
    # count-in is read-only and must NEVER be written; quantization RESTORED
    assert not any(e[0] == "set" and e[2] == "count_in_duration" for e in b.log)
    assert b.values[("live_set", "clip_trigger_quantization")] == 4
    sets = [(p, pr, v) for kind, p, pr, v in
            [e for e in b.log if e[0] == "set"] for _ in [0]]
    assert ("live_set tracks 1", "arm", True) in [(p, pr, v) for p, pr, v in sets]
    assert ("live_set tracks 1", "arm", False) in [(p, pr, v) for p, pr, v in sets]
    assert ("live_set", "record_mode", True) in [(p, pr, v) for p, pr, v in sets]
    calls = [e for e in b.log if e[0] == "call"]
    assert ("call", "live_set", "start_playing", ()) in calls
    assert ("call", "live_set", "stop_playing", ()) in calls
    # record_mode came on only AFTER quantization was zeroed
    order = [(e[2], e[3]) for e in b.log if e[0] == "set" and e[1] == "live_set"]
    assert order.index(("clip_trigger_quantization", 0)) < order.index(("record_mode", True))


def test_choose_take_note_copy_never_duplicate():
    b = TakesBridge()
    r = Live(b).choose_take(track=1, lane=0)
    assert r["at_beats"] == 8.0 and r["notes"] == 1 and r["replaced"] == ["old main"]
    calls = [e for e in b.log if e[0] == "call"]
    # the CRASH rule: duplicate_clip_to_arrangement with a lane clip kills Live
    assert not any(c[2] == "duplicate_clip_to_arrangement" for c in calls)
    assert ("call", "live_set tracks 1", "delete_clip",
            ({"$path": "live_set tracks 1 arrangement_clips 0"},)) in calls
    assert ("call", "live_set tracks 1", "create_midi_clip", (8.0, 4.0)) in calls
    adds = [e for e in b.log if e[0] == "request" and e[1] == "clip_add_notes"]
    assert len(adds) == 1 and adds[0][2]["notes"][0]["pitch"] == 48


class PrintBridge(TakesBridge):
    """Extends the comping fake with track creation + routing for print_sequence."""

    ROUTES = ["All Ins", "1-Operator", "No Input"]

    def __init__(self):
        super().__init__()
        self.tracks = [0, 1]
        self.values[("live_set tracks 2", "can_be_armed")] = True

    def get(self, path, prop):
        if path == "live_set" and prop == "tracks":
            return list(self.tracks)
        if prop == "available_input_routing_types":
            return [None] * len(self.ROUTES)
        if "available_input_routing_types" in path and prop == "display_name":
            return self.ROUTES[int(path.split()[-1])]
        return super().get(path, prop)

    def call(self, path, func, *args):
        if func == "create_midi_track":
            self.tracks.append(len(self.tracks))
        super().call(path, func, *args)


def test_print_sequence_routes_records_reads():
    b = PrintBridge()
    r = Live(b).print_sequence(source_track=0, length_beats=8.0, source_slot=0)
    assert r["track"] == 2 and r["notes"] == 1 and r["clip"] is not None
    routed = [e for e in b.log if e[0] == "set" and e[2] == "input_routing_type"]
    assert routed[0][3] == {"$path":
        "live_set tracks 2 available_input_routing_types 1"}  # "1-Operator"
    fired = [e for e in b.log if e[0] == "call" and e[2] == "fire"]
    assert fired and fired[0][1] == "live_set tracks 0 clip_slots 0"
    # the source's clip was fired BEFORE recording started
    assert b.log.index(fired[0]) < b.log.index(
        next(e for e in b.log if e[0] == "set" and e[2] == "record_mode"
             and e[3] is True))


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
