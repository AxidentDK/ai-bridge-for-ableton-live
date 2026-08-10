"""Ergonomic conveniences on top of the Bridge primitives.

Everything here COMPOSES the generic wire methods (get/set/call/…) — no new
in-Live capability, just fewer keystrokes. The three ``clip_*`` note methods
are the wire's own (see remote_script/notes.py for why).

Usage::

    from client import Bridge
    from api import Live

    with Bridge() as bridge:
        live = Live(bridge)
        live.play()
        print(live.tempo, live.track_names())
        clip = live.create_clip(track=0, slot=0, length=8.0)
        live.add_notes(clip, [{"pitch": 64, "start_time": 0.0,
                               "duration": 1.0, "velocity": 100}])
"""
from __future__ import annotations


class Live:
    def __init__(self, bridge):
        self.b = bridge

    # --- transport -----------------------------------------------------------------
    @property
    def tempo(self) -> float:
        return self.b.get("live_set", "tempo")

    @tempo.setter
    def tempo(self, bpm: float):
        self.b.set("live_set", "tempo", float(bpm))

    def play(self, from_time: float | None = None):
        if from_time is not None:
            self.b.set("live_set", "current_song_time", float(from_time))
        self.b.call("live_set", "start_playing")

    def stop(self):
        self.b.call("live_set", "stop_playing")

    @property
    def is_playing(self) -> bool:
        return self.b.get("live_set", "is_playing")

    # --- tracks ---------------------------------------------------------------------
    def tracks(self) -> list[dict]:
        return self.b.get("live_set", "tracks")

    def track_names(self) -> list[str]:
        return [t.get("name") for t in self.tracks()]

    def create_midi_track(self, index: int = -1) -> str:
        self.b.call("live_set", "create_midi_track", index)
        i = len(self.tracks()) - 1 if index == -1 else index
        return f"live_set tracks {i}"

    def create_audio_track(self, index: int = -1) -> str:
        self.b.call("live_set", "create_audio_track", index)
        i = len(self.tracks()) - 1 if index == -1 else index
        return f"live_set tracks {i}"

    # --- clips & notes ----------------------------------------------------------------
    def slot_path(self, track: int, slot: int) -> str:
        return f"live_set tracks {track} clip_slots {slot}"

    def has_clip(self, track: int, slot: int) -> bool:
        return bool(self.b.get(self.slot_path(track, slot), "has_clip"))

    def create_clip(self, track: int, slot: int, length: float) -> str:
        """Create a session MIDI clip; returns the clip's path."""
        path = self.slot_path(track, slot)
        self.b.call(path, "create_clip", float(length))
        return f"{path} clip"

    def delete_clip(self, track: int, slot: int):
        self.b.call(self.slot_path(track, slot), "delete_clip")

    def notes(self, clip_path: str, **window) -> list[dict]:
        return self.b.request("clip_get_notes", path=clip_path, **window)

    def add_notes(self, clip_path: str, notes: list[dict]) -> int:
        return self.b.request("clip_add_notes", path=clip_path, notes=notes)

    def remove_notes(self, clip_path: str, **window) -> bool:
        return self.b.request("clip_remove_notes", path=clip_path, **window)

    def fire(self, track: int, slot: int):
        self.b.call(self.slot_path(track, slot), "fire")

    # --- devices & parameters ------------------------------------------------------------
    def devices(self, track: int) -> list[dict]:
        return self.b.get(f"live_set tracks {track}", "devices")

    def parameters(self, track: int, device: int) -> list[dict]:
        path = f"live_set tracks {track} devices {device}"
        params = self.b.get(path, "parameters")
        out = []
        for i, p in enumerate(params):
            ppath = f"{path} parameters {i}"
            out.append({
                "index": i,
                "name": self.b.get(ppath, "name"),
                "value": self.b.get(ppath, "value"),
                "min": self.b.get(ppath, "min"),
                "max": self.b.get(ppath, "max"),
            })
        return out

    def set_parameter(self, track: int, device: int, parameter: int, value: float):
        self.b.set(f"live_set tracks {track} devices {device} parameters {parameter}",
                   "value", float(value))

    # --- mixer ------------------------------------------------------------------------------
    def volume(self, track: int) -> float:
        return self.b.get(f"live_set tracks {track} mixer_device volume", "value")

    def set_volume(self, track: int, value: float):
        self.b.set(f"live_set tracks {track} mixer_device volume", "value", float(value))

    # --- views ---------------------------------------------------------------------------
    def show_view(self, name: str):
        """Switch a main view. Names: 'Arranger', 'Session', 'Browser',
        'Detail', 'Detail/Clip', 'Detail/DeviceChain'."""
        self.b.call("live_app view", "show_view", name)

    def show_arranger(self):
        self.show_view("Arranger")

    def show_session(self):
        self.show_view("Session")

    def is_view_visible(self, name: str) -> bool:
        return bool(self.b.call("live_app view", "is_view_visible", name))

    # --- housekeeping: clean up unused tracks --------------------------------------------
    def track_is_empty(self, i: int) -> bool:
        """A track is 'unused' only if it has no clips AND no devices.

        Conservative on purpose: a track you've loaded an instrument onto, or
        written any clip into (session or arrangement), is never considered
        unused — only truly pristine tracks (like a new set's defaults) are.
        """
        base = f"live_set tracks {i}"
        if self.b.get(base, "devices"):
            return False
        if self.b.get(base, "arrangement_clips"):
            return False
        slots = self.b.get(base, "clip_slots")
        for s in range(len(slots)):
            if self.b.get(f"{base} clip_slots {s}", "has_clip"):
                return False
        return True

    def unused_tracks(self) -> list[dict]:
        n = len(self.b.get("live_set", "tracks"))
        out = []
        for i in range(n):
            if self.track_is_empty(i):
                out.append({"index": i, "name": self.b.get(f"live_set tracks {i}", "name")})
        return out

    def cleanup_unused_tracks(self, dry_run: bool = False) -> dict:
        """Delete unused (no clips + no devices) tracks. dry_run just reports.

        Live requires a set to keep at least one track, so if every track is
        unused, one is left behind (reported as ``kept_empty``).
        """
        unused = self.unused_tracks()
        if dry_run:
            return {"count": len(unused), "would_remove": unused,
                    "remaining": len(self.b.get("live_set", "tracks"))}
        removed, kept = [], None
        total = len(self.b.get("live_set", "tracks"))
        # delete high index first so lower indices don't shift under us
        for t in sorted(unused, key=lambda t: t["index"], reverse=True):
            if total <= 1:
                kept = t  # Live won't delete the final remaining track
                break
            self.b.call("live_set", "delete_track", t["index"])
            removed.append(t)
            total -= 1
        result = {"count": len(unused), "removed": removed,
                  "remaining": len(self.b.get("live_set", "tracks"))}
        if kept is not None:
            result["kept_empty"] = f"{kept['name']} (Live requires at least one track)"
        return result

    # --- beyond-LOM: save / render (see render.py) -----------------------------------------
    def save(self, **kw) -> dict:
        try:
            from . import render
        except ImportError:
            import render
        return render.save_set(self.b, **kw)

    def export(self, output_path: str, start_beats: float | None = None,
               length_beats: float | None = None, **kw) -> dict:
        try:
            from . import render
        except ImportError:
            import render
        return render.export_set(self.b, output_path, start_beats, length_beats, **kw)

    # --- overview ------------------------------------------------------------------------------
    def summary(self) -> dict:
        hello = self.b.hello()
        return {
            "live_version": hello.get("live_version"),
            "tempo": self.tempo,
            "is_playing": self.is_playing,
            "tracks": [
                {"index": i, "name": t.get("name"), "type": t.get("type")}
                for i, t in enumerate(self.tracks())
            ],
        }
