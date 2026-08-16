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

import json
import math
import os
import sys
import time


def _to_db(value: float) -> float | None:
    """Meter value (0..1) -> approximate dBFS. None for true silence."""
    if not value or value <= 1e-6:
        return None
    return round(20.0 * math.log10(min(1.0, float(value))), 1)


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

    _PARAM_PROPS = ("name", "value", "min", "max")

    def parameters(self, track: int, device: int) -> list[dict]:
        """All parameters of a device — batched into TWO round-trips total.

        (Reading each prop as its own request costs one main-thread hop each:
        a 66-parameter device took ~65 s. Batched, it's one hop for all of it.)
        """
        path = f"live_set tracks {track} devices {device}"
        n = len(self.b.get(path, "parameters"))
        pairs = [(f"{path} parameters {i}", prop)
                 for i in range(n) for prop in self._PARAM_PROPS]
        values = self.b.get_many(pairs)
        w = len(self._PARAM_PROPS)
        return [dict({"index": i}, **dict(zip(self._PARAM_PROPS, values[i * w:(i + 1) * w])))
                for i in range(n)]

    def set_parameter(self, track: int, device: int, parameter: int, value: float):
        self.b.set(f"live_set tracks {track} devices {device} parameters {parameter}",
                   "value", float(value))

    # --- surgical note editing (by note id) ----------------------------------------------
    def edit_notes(self, clip_path: str, edits: list[dict] | None = None,
                   delete_ids: list[int] | None = None) -> dict:
        """Change or delete SPECIFIC notes, leaving every other note untouched.

        ``live_transform_notes`` rewrites a whole window — right for "transpose the
        clip", wrong for "make those three notes louder", which would otherwise mean
        reading everything back and rewriting it (and losing anything added
        meanwhile). Live gives every note a stable ``note_id``; this uses
        ``apply_note_modifications`` / ``remove_notes_by_id`` to touch only those.

        Each edit: ``{"note_id": N, "pitch"?, "start_time"?, "duration"?,
        "velocity"?, "mute"?, "probability"?}`` — omitted fields keep their value.
        """
        if not edits and not delete_ids:
            raise ValueError("give edits and/or delete_ids")

        current = {int(n["note_id"]): n for n in
                   (self.b.request("clip_get_notes", path=clip_path) or [])
                   if n.get("note_id") is not None}
        result = {"clip": clip_path}

        if delete_ids:
            missing = [i for i in delete_ids if int(i) not in current]
            if missing:
                raise ValueError("no note with id %s in this clip" % missing)
            # Route through the remote-script method, NOT b.call: Live's
            # remove_notes_by_id wants an iterable, and calling it generically
            # spreads the ids as separate int arguments ("'int' object has no
            # attribute '__iter__'").
            result["deleted"] = self.b.request(
                "clip_remove_notes_by_id", path=clip_path,
                note_ids=[int(i) for i in delete_ids])

        if edits:
            payload = []
            for edit in edits:
                nid = int(edit.get("note_id", -1))
                base = current.get(nid)
                if base is None:
                    raise ValueError("no note with id %d in this clip" % nid)
                merged = {"note_id": nid,
                          "pitch": int(edit.get("pitch", base["pitch"])),
                          "start_time": float(edit.get("start_time", base["start_time"])),
                          "duration": float(edit.get("duration", base["duration"])),
                          "velocity": float(edit.get("velocity", base.get("velocity", 100))),
                          "mute": bool(edit.get("mute", base.get("mute", False))),
                          "probability": float(edit.get("probability",
                                                        base.get("probability", 1.0)))}
                payload.append(merged)
            self.b.request("clip_apply_note_modifications", path=clip_path, notes=payload)
            result["edited"] = len(payload)

        result["notes_now"] = len(self.b.request("clip_get_notes", path=clip_path) or [])
        return result

    # --- automation curves ----------------------------------------------------------------
    CURVES = ("linear", "ease_in", "ease_out", "ease_in_out", "exponential",
              "logarithmic", "sine", "cosine", "s_curve", "triangle", "saw",
              "square", "random")

    @staticmethod
    def _shape(name: str, t: float, seed_state: list) -> float:
        """Map 0..1 through a named curve. Pure maths — no Live involved."""
        import math as _m
        import random as _r
        t = max(0.0, min(1.0, t))
        if name == "linear":
            return t
        if name == "ease_in":
            return t * t
        if name == "ease_out":
            return 1 - (1 - t) ** 2
        if name == "ease_in_out":
            return 3 * t * t - 2 * t ** 3
        if name == "exponential":
            return (_m.exp(3 * t) - 1) / (_m.exp(3) - 1)
        if name == "logarithmic":
            return _m.log1p(9 * t) / _m.log(10)
        if name == "sine":
            return _m.sin(t * _m.pi / 2)
        if name == "cosine":
            return (1 - _m.cos(t * _m.pi)) / 2
        if name == "s_curve":
            return 1 / (1 + _m.exp(-12 * (t - 0.5)))
        if name == "triangle":
            return 1 - abs(2 * t - 1)
        if name == "saw":
            return t % 1.0
        if name == "square":
            return 0.0 if t < 0.5 else 1.0
        if name == "random":
            if not seed_state:
                seed_state.append(_r.Random(0))
            return seed_state[0].random()
        raise ValueError("unknown curve %r; try: %s" % (name, ", ".join(Live.CURVES)))

    def envelope_curve(self, clip_path: str, parameter_path: str,
                       start_value: float, end_value: float,
                       start_time: float = 0.0, length: float = 4.0,
                       curve: str = "linear", steps: int = 32,
                       clear_first: bool = True) -> dict:
        """Write a SHAPED automation sweep, not a straight line.

        Live's envelopes are point lists, so a curve is drawn by sampling its shape
        into points. ``steps`` trades smoothness against clip clutter; 32 over a bar
        is usually indistinguishable from continuous.

        Quirk worth knowing when verifying: reading the envelope at EXACTLY time 0
        returns a stale value rather than the first point (measured 0.85 where 0.2
        was written), while 0.001 onward reads correctly. It is Live's boundary
        behaviour, not a bad write — check just after zero.
        """
        if curve not in self.CURVES:
            raise ValueError("unknown curve %r; try: %s" % (curve, ", ".join(self.CURVES)))
        steps = max(2, min(500, int(steps)))
        state: list = []
        points = []
        for i in range(steps):
            t = i / (steps - 1)
            shaped = self._shape(curve, t, state)
            points.append({"time": round(start_time + t * length, 6),
                           "value": round(start_value + (end_value - start_value) * shaped, 6)})
        if clear_first:
            self.b.request("clip_envelope_clear", path=clip_path, parameter=parameter_path)
        written = self.b.request("clip_envelope_insert", path=clip_path,
                                 parameter=parameter_path, steps=points)
        return {"clip": clip_path, "curve": curve, "points": len(points),
                "from": start_value, "to": end_value,
                "start_time": start_time, "length": length, "written": written}

    # --- watching Live (semantic observer bundles) ----------------------------------------
    # Observers turn the bridge from something that answers questions into something
    # that NOTICES. Raw `observe` needs a path and a property per subscription, which
    # is fine once you know what to watch; these presets encode the useful answers to
    # "what should I watch to know X?".
    WATCH_PRESETS = {
        "transport": [("live_set", "is_playing"), ("live_set", "tempo"),
                      ("live_set", "session_record_status"),
                      ("live_set", "can_capture_midi")],
        "performance": [("live_set tracks {t}", "playing_slot_index"),
                        ("live_set tracks {t}", "fired_slot_index")],
        "edits": [("live_set tracks {t} clip_slots {s}", "has_clip")],
        "mixer": [("live_set tracks {t} mixer_device volume", "value"),
                  ("live_set tracks {t}", "mute"), ("live_set tracks {t}", "solo")],
        "structure": [("live_set", "tracks"), ("live_set", "scenes"),
                      ("live_set tracks {t}", "devices")],
        "key": [("live_set", "scale_name"), ("live_set", "root_note"),
                ("live_set", "scale_mode")],
        "focus": [("live_set", "appointed_device")],
        "dialogs": [("live_app", "open_dialog_count")],
    }

    def watch(self, preset: str, tracks: list | None = None,
              max_slots: int = 8) -> dict:
        """Subscribe to a named bundle of properties worth noticing.

        ``{t}``/``{s}`` in a preset expand over tracks and clip slots. Deliberately
        NOT offered: meter levels, which fire ~245 Hz and belong in
        ``meters_observed`` where they are aggregated rather than streamed.
        """
        if preset not in self.WATCH_PRESETS:
            raise ValueError("preset must be one of: %s"
                             % ", ".join(sorted(self.WATCH_PRESETS)))
        track_count = len(self.b.get("live_set", "tracks") or [])
        wanted = list(range(track_count)) if tracks is None else [int(t) for t in tracks]
        subs, failed = [], []
        for path_tpl, prop in self.WATCH_PRESETS[preset]:
            targets = []
            if "{t}" in path_tpl and "{s}" in path_tpl:
                for t in wanted:
                    slots = len(self.b.get(f"live_set tracks {t}", "clip_slots") or [])
                    targets += [path_tpl.format(t=t, s=s)
                                for s in range(min(slots, max_slots))]
            elif "{t}" in path_tpl:
                targets = [path_tpl.format(t=t) for t in wanted]
            else:
                targets = [path_tpl]
            for target in targets:
                try:
                    subs.append({"subscription": self.b.observe(target, prop),
                                 "path": target, "prop": prop})
                except Exception as exc:
                    failed.append({"path": target, "prop": prop, "error": str(exc)[:80]})
        out = {"preset": preset, "subscriptions": len(subs), "watching": subs}
        if failed:
            out["skipped"] = failed
        out["note"] = "collect with live_events; stop each with live_unobserve"
        return out

    # --- modal dialogs --------------------------------------------------------------------
    def dialog(self, press: int | None = None) -> dict:
        """Is Live showing a modal dialog, and what does it say?

        Live's Application exposes ``open_dialog_count``, ``current_dialog_message``
        and ``press_current_dialog_button``. This matters because a modal dialog
        silently blocks everything else — a save prompt during a restart, a
        "couldn't do that" warning — and without this the only way to notice was to
        take a screenshot and look.

        ``press`` clicks a button by index. Left deliberately explicit: dismissing a
        dialog can discard work, so nothing is pressed unless asked.
        """
        count = self.b.get("live_app", "open_dialog_count") or 0
        info = {"open": int(count) > 0, "count": int(count)}
        if info["open"]:
            for prop, key in (("current_dialog_message", "message"),
                              ("current_dialog_button_count", "buttons")):
                try:
                    info[key] = self.b.get("live_app", prop)
                except Exception:
                    pass
            if press is not None:
                self.b.call("live_app", "press_current_dialog_button", int(press))
                info["pressed"] = int(press)
                info["open_after"] = int(self.b.get("live_app", "open_dialog_count") or 0)
        return info

    # --- scenes -------------------------------------------------------------------------
    def scenes(self) -> list[dict]:
        """Every scene with its name, colour and whether it holds any clips."""
        raw = self.b.get("live_set", "scenes") or []
        pairs = [(f"live_set scenes {i}", p)
                 for i in range(len(raw)) for p in ("name", "is_empty", "is_triggered")]
        values = self.b.get_many(pairs) if raw else []
        return [{"index": i, "name": values[i * 3], "is_empty": values[i * 3 + 1],
                 "is_triggered": values[i * 3 + 2]} for i in range(len(raw))]

    def scene(self, action: str, index: int | None = None,
              name: str | None = None) -> dict:
        """create | delete | duplicate | fire | rename | capture a scene.

        ``capture`` is Live's "Capture and Insert Scene": it grabs whatever is
        currently playing across all tracks into a new scene — the fastest way to
        keep a combination that works.
        """
        count = len(self.b.get("live_set", "scenes") or [])
        if action in ("delete", "duplicate", "fire", "rename") and index is None:
            raise ValueError("%s needs an index" % action)
        if index is not None and not 0 <= int(index) < count:
            raise ValueError("scene %s out of range (0..%d)" % (index, count - 1))

        if action == "create":
            self.b.call("live_set", "create_scene", -1 if index is None else int(index))
        elif action == "capture":
            self.b.call("live_set", "capture_and_insert_scene")
        elif action == "delete":
            self.b.call("live_set", "delete_scene", int(index))
        elif action == "duplicate":
            self.b.call("live_set", "duplicate_scene", int(index))
        elif action == "fire":
            self.b.call(f"live_set scenes {int(index)}", "fire")
        elif action == "rename":
            if name is None:
                raise ValueError("rename needs a name")
            self.b.set(f"live_set scenes {int(index)}", "name", str(name))
        else:
            raise ValueError("action must be create|delete|duplicate|fire|rename|capture")
        return {"action": action, "scenes": self.scenes()}

    # --- track routing / arm / monitoring -------------------------------------------------
    # Live's CURRENT routing is readable as a plain string ("Ext. In", "Master", a
    # track name) via the current_* properties. The settable side is object-based
    # (RoutingType / RoutingChannel), and those objects expose `display_name` — NOT
    # `name` — so they serialize without a label and their names must be read
    # explicitly. Hence: strings for reading, objects for writing.
    _ROUTE_PROPS = ("current_input_routing", "current_input_sub_routing",
                    "current_output_routing", "current_output_sub_routing",
                    "current_monitoring_state", "arm", "mute", "solo")

    def _routing_names(self, path: str, prop: str) -> list[str]:
        """display_name of every entry in an available_* routing list (one batch)."""
        try:
            count = len(self.b.get(path, prop) or [])
        except Exception:
            return []
        if not count:
            return []
        results = self.b.batch([{"method": "get",
                                 "params": {"path": f"{path} {prop} {i}",
                                            "prop": "display_name"}}
                                for i in range(count)])
        return [r.get("result") for r in results if r.get("ok")]

    def routing(self, track: int) -> dict:
        """Where a track takes audio/MIDI from and sends it to, plus arm/monitor.

        Also lists the AVAILABLE choices, because routing names are Live's own
        strings ("Ext. In", "Resampling", a track name) and cannot be guessed.
        """
        path = f"live_set tracks {track}"
        vals = self.b.batch([{"method": "get", "params": {"path": path, "prop": p}}
                             for p in self._ROUTE_PROPS])
        out = {"track": track}
        for prop, res in zip(self._ROUTE_PROPS, vals):
            out[prop] = res.get("result") if res.get("ok") else None
        for prop in ("available_input_routing_types", "available_output_routing_types",
                     "available_input_routing_channels", "available_output_routing_channels"):
            names = self._routing_names(path, prop)
            if names:
                out[prop] = names
        out["monitoring_states"] = {"0": "In", "1": "Auto", "2": "Off"}
        return out

    def set_routing(self, track: int, input_type: str | None = None,
                    output_type: str | None = None, monitoring: int | None = None,
                    arm: bool | None = None) -> dict:
        """Set routing by DISPLAY NAME (as shown in Live), or monitoring/arm.

        Routing properties want a routing OBJECT, not a string, so the matching
        entry is looked up in the track's ``available_*`` list and assigned by
        reference — the same trick the object-reference support in ``set`` exists for.
        """
        path = f"live_set tracks {track}"
        changed = {}
        for wanted, prop, available in (
                (input_type, "input_routing_type", "available_input_routing_types"),
                (output_type, "output_routing_type", "available_output_routing_types")):
            if wanted is None:
                continue
            names = self._routing_names(path, available)
            match = next((i for i, n in enumerate(names)
                          if str(n).lower() == str(wanted).lower()), None)
            if match is None:
                raise ValueError("no routing %r on track %d; available: %s"
                                 % (wanted, track, ", ".join(str(n) for n in names)))
            self.b.set(path, prop, {"$path": f"{path} {available} {match}"})
            changed[prop] = wanted
        if monitoring is not None:
            self.b.set(path, "current_monitoring_state", int(monitoring))
            changed["monitoring"] = int(monitoring)
        if arm is not None:
            self.b.set(path, "arm", bool(arm))
            changed["arm"] = bool(arm)
        return {"track": track, "changed": changed, "now": self.routing(track)}

    # --- browser preview ------------------------------------------------------------------
    def preview(self, name: str | None = None, path: str | None = None,
                stop: bool = False, category: str | None = None) -> dict:
        """Audition a browser item WITHOUT loading it into the set.

        Live's browser exposes ``preview_item``/``stop_preview``; this is what the
        browser's headphone button does. Pairs with ``live_similar_sounds``: find
        something similar, hear it, and only then load it.
        """
        if stop:
            self.b.call("live_app browser", "stop_preview")
            return {"stopped": True}
        if path:
            item_path = path
            found = path.rsplit(" children ", 1)[-1]
        else:
            if not name:
                raise ValueError("give name (or path), or stop=true")
            hit = self.browse(category or "sounds", query=name, limit=1, max_depth=3)
            if not hit["items"]:
                for cat in self.BROWSER_CATEGORIES:
                    hit = self.browse(cat, query=name, limit=1, max_depth=3)
                    if hit["items"]:
                        break
            if not hit["items"]:
                raise ValueError("no browser item matching %r" % name)
            item_path, found = hit["items"][0]["path"], hit["items"][0]["name"]
        self.b.call("live_app browser", "preview_item", {"$path": item_path})
        return {"previewing": found, "path": item_path,
                "note": "call again with stop=true to silence it"}

    # --- semantic description (MIDI tier) ------------------------------------------------
    def describe_clip(self, clip_path: str) -> dict:
        """Describe ONE clip musically, from its notes."""
        import describe as _describe
        info = _describe.describe_notes(self.notes(clip_path), float(self.tempo))
        try:
            info["clip"] = self.b.get(clip_path, "name")
        except Exception:
            pass
        return info

    def describe_clips(self, include_arrangement: bool = True,
                       limit: int = 200) -> dict:
        """Describe EVERY clip in the set — a searchable index of your own material.

        This is the MIDI half of the "semantic browser index" idea: a list of clips
        named 'Idea 12' tells you nothing, while 'F minor, chordal, sparse, low
        register' is something you can actually search. Audio clips are listed but
        not described — that needs a model that hears the waveform; measuring their
        character is a separate (feature-based) job.
        """
        # Batched throughout: a per-track/per-clip read costs a hop onto Live's main
        # thread, and finding clips one call at a time made even a 2-clip set take
        # 11 s (the same N+1 that made the browser walk slow).
        tracks = self.b.get("live_set", "tracks") or []
        props = ["clip_slots"] + (["arrangement_clips"] if include_arrangement else [])
        layout = self.b.batch([{"method": "get",
                                "params": {"path": f"live_set tracks {t}", "prop": p}}
                               for t in range(len(tracks)) for p in props])

        found: list[tuple[str, str]] = []
        slot_probes: list[tuple[int, int, str]] = []
        width = len(props)
        for t_index, track in enumerate(tracks):
            name = track.get("name")
            got = layout[t_index * width]
            slots = (got.get("result") or []) if got.get("ok") else []
            slot_probes += [(t_index, s, name) for s in range(len(slots))]
            if include_arrangement:
                got_arr = layout[t_index * width + 1]
                arr = (got_arr.get("result") or []) if got_arr.get("ok") else []
                found += [(f"live_set tracks {t_index} arrangement_clips {a}",
                           f"{name} / arrangement {a + 1}") for a in range(len(arr))]

        if slot_probes:
            has = self.b.get_many([(f"live_set tracks {t} clip_slots {s}", "has_clip")
                                   for t, s, _ in slot_probes])
            found += [(f"live_set tracks {t} clip_slots {s} clip", f"{n} / slot {s + 1}")
                      for (t, s, n), ok in zip(slot_probes, has) if ok]

        found = found[:limit]
        if not found:
            return {"midi_clips": 0, "audio_clips": 0, "clips": []}

        meta = self.b.batch([{"method": "get", "params": {"path": p, "prop": prop}}
                             for p, _ in found for prop in ("is_midi_clip", "name")])

        described, audio = [], []
        for idx, (path, where) in enumerate(found):
            m_kind, m_name = meta[idx * 2], meta[idx * 2 + 1]
            if not m_kind.get("ok"):
                continue
            is_midi = m_kind.get("result")
            name = m_name.get("result") if m_name.get("ok") else None
            if not is_midi:
                audio.append({"where": where, "name": name, "path": path})
                continue
            info = self.describe_clip(path)
            described.append({"where": where, "name": name, "path": path,
                              "summary": info.get("summary"),
                              "key": (info.get("key") or {}).get("key"),
                              "bars": info.get("length_bars"),
                              "texture": info.get("texture")})
        out = {"midi_clips": len(described), "audio_clips": len(audio),
               "clips": described}
        if audio:
            out["audio_not_described"] = audio
            out["note"] = ("Audio clips are listed, not described — that needs a model "
                           "that hears the waveform. Their measurable character "
                           "(loudness, brightness, dominant pitch) is available via "
                           "live_analyze_wav on a rendered/captured file.")
        return out

    # --- note transforms ----------------------------------------------------------------
    # Arbitrary musical surgery on a clip's notes. Live's stock MIDI devices act on
    # the LIVE stream and are stateless per note, so they cannot express anything
    # that depends on context or on the phrase as a whole — a diatonic harmony line,
    # a retrograde, swing that alternates by position. Reading the notes out,
    # transforming them with real logic and writing them back can.
    SCALES = {
        "major": (0, 2, 4, 5, 7, 9, 11),
        "minor": (0, 2, 3, 5, 7, 8, 10),          # natural minor / aeolian
        "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
        "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
        "dorian": (0, 2, 3, 5, 7, 9, 10),
        "phrygian": (0, 1, 3, 5, 7, 8, 10),
        "lydian": (0, 2, 4, 6, 7, 9, 11),
        "mixolydian": (0, 2, 4, 5, 7, 9, 10),
        "locrian": (0, 1, 3, 5, 6, 8, 10),
        "pentatonic_major": (0, 2, 4, 7, 9),
        "pentatonic_minor": (0, 3, 5, 7, 10),
        "blues": (0, 3, 5, 6, 7, 10),
        "chromatic": tuple(range(12)),
    }
    NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

    def _scale_pitches(self, root: int, scale: str) -> tuple:
        if scale not in self.SCALES:
            raise ValueError("unknown scale %r; known: %s"
                             % (scale, ", ".join(sorted(self.SCALES))))
        return tuple((root + s) % 12 for s in self.SCALES[scale])

    def _snap(self, pitch: int, allowed: tuple, prefer_down: bool = False) -> int:
        """Nearest pitch whose class is in the scale (ties go up, or down if asked)."""
        if pitch % 12 in allowed:
            return pitch
        for delta in range(1, 7):
            down, up = pitch - delta, pitch + delta
            first, second = (down, up) if prefer_down else (up, down)
            if first % 12 in allowed:
                return first
            if second % 12 in allowed:
                return second
        return pitch

    def _degree(self, pitch: int, allowed_sorted: list) -> int | None:
        pc = pitch % 12
        return allowed_sorted.index(pc) if pc in allowed_sorted else None

    def transform_notes(self, clip_path: str, operation: str,
                        semitones: int = 0, root: str | int = "C",
                        scale: str = "major", degrees: list | None = None,
                        intervals: list | None = None, amount: float = 1.0,
                        grid: float = 0.25, swing: float = 0.0,
                        timing_ms: float = 12.0, velocity_range: int = 12,
                        seed: int = 0, axis: int | None = None,
                        preview: bool = False) -> dict:
        """Transform a clip's notes musically. See ``operation`` for what it does.

        Operations:
          ``transpose``      shift by ``semitones``; with ``scale`` set to
                             something other than chromatic, shifts by SCALE
                             DEGREES instead (``degrees``), so the line stays in key.
          ``scale_fold``     snap every note into ``root``/``scale`` — turns an
                             out-of-key phrase diatonic without moving its shape.
          ``harmonize``      add a second voice at ``degrees`` scale steps (a
                             diatonic third is ``[2]``), or exact ``intervals``
                             in semitones. This is "play one line, get harmony".
          ``invert``         mirror pitches around ``axis`` (default: the first
                             note), preserving rhythm.
          ``retrograde``     reverse the phrase in time, preserving durations.
          ``quantize``       pull starts toward a ``grid`` by ``amount`` (0..1 —
                             partial quantize keeps feel), with optional ``swing``.
          ``humanize``       nudge timing (±``timing_ms``) and velocity
                             (±``velocity_range``); ``seed`` makes it repeatable.
          ``velocity_ramp``  linear crescendo/diminuendo across the phrase.
          ``duration_scale`` multiply durations by ``amount`` (legato > 1, staccato < 1).

        ``preview=True`` returns the result WITHOUT writing, so a transform can be
        inspected before it replaces the clip.
        """
        import random

        if isinstance(root, str):
            name = root.strip().upper().replace("♯", "#")
            if name not in self.NOTE_NAMES:
                raise ValueError("root must be one of %s" % (", ".join(self.NOTE_NAMES),))
            root_pc = self.NOTE_NAMES.index(name)
        else:
            root_pc = int(root) % 12

        notes = sorted(self.notes(clip_path), key=lambda n: (n["start_time"], n["pitch"]))
        if not notes:
            return {"changed": 0, "note": "clip has no notes"}
        allowed = self._scale_pitches(root_pc, scale)
        allowed_sorted = sorted(allowed)
        out: list[dict] = []

        def base(n, **over):
            # `mute` and `probability` are CARRIED, not dropped. Every transform here
            # removes the clip's notes and re-adds them, so anything this function
            # forgets is silently erased from the user's clip. It used to copy only
            # pitch, start, duration and velocity — so transposing a part reset every
            # muted note to audible and every probability back to 1.0, which is a
            # musical edit nobody asked for and nothing reported.
            row = {"pitch": int(n["pitch"]), "start_time": float(n["start_time"]),
                   "duration": float(n["duration"]),
                   "velocity": float(n.get("velocity", 100)),
                   "mute": bool(n.get("mute", False)),
                   "probability": float(n.get("probability", 1.0))}
            row.update(over)
            return row

        if operation == "transpose":
            if scale == "chromatic" or not degrees:
                out = [base(n, pitch=max(0, min(127, n["pitch"] + int(semitones))))
                       for n in notes]
            else:
                step = int(degrees[0])
                for n in notes:
                    snapped = self._snap(int(n["pitch"]), allowed)
                    deg = self._degree(snapped, allowed_sorted)
                    if deg is None:
                        out.append(base(n))
                        continue
                    size = len(allowed_sorted)
                    target = deg + step
                    octave = (snapped // 12) + (target // size)
                    pc = allowed_sorted[target % size]
                    out.append(base(n, pitch=max(0, min(127, octave * 12 + pc))))
        elif operation == "scale_fold":
            out = [base(n, pitch=self._snap(int(n["pitch"]), allowed)) for n in notes]
        elif operation == "harmonize":
            for n in notes:
                out.append(base(n))
                if intervals:
                    for semis in intervals:
                        out.append(base(n, pitch=max(0, min(127, n["pitch"] + int(semis)))))
                else:
                    for step in (degrees or [2]):
                        snapped = self._snap(int(n["pitch"]), allowed)
                        deg = self._degree(snapped, allowed_sorted)
                        if deg is None:
                            continue
                        size = len(allowed_sorted)
                        target = deg + int(step)
                        octave = (snapped // 12) + (target // size)
                        pc = allowed_sorted[target % size]
                        out.append(base(n, pitch=max(0, min(127, octave * 12 + pc))))
        elif operation == "invert":
            centre = int(axis) if axis is not None else int(notes[0]["pitch"])
            out = [base(n, pitch=max(0, min(127, 2 * centre - int(n["pitch"]))))
                   for n in notes]
        elif operation == "retrograde":
            span_start = min(n["start_time"] for n in notes)
            span_end = max(n["start_time"] + n["duration"] for n in notes)
            for n in notes:
                new_start = span_start + (span_end - (n["start_time"] + n["duration"]))
                out.append(base(n, start_time=round(new_start, 6)))
        elif operation == "quantize":
            strength = max(0.0, min(1.0, float(amount)))
            for n in notes:
                target = round(n["start_time"] / grid) * grid
                if swing and int(round(target / grid)) % 2 == 1:
                    target += grid * float(swing)
                moved = n["start_time"] + (target - n["start_time"]) * strength
                out.append(base(n, start_time=round(max(0.0, moved), 6)))
        elif operation == "humanize":
            rng = random.Random(seed)
            beats = float(timing_ms) / 1000.0 * (float(self.tempo) / 60.0)
            for n in notes:
                jitter = rng.uniform(-beats, beats)
                vel = n.get("velocity", 100) + rng.uniform(-velocity_range, velocity_range)
                out.append(base(n, start_time=round(max(0.0, n["start_time"] + jitter), 6),
                                velocity=max(1.0, min(127.0, vel))))
        elif operation == "velocity_ramp":
            first, last = notes[0]["start_time"], notes[-1]["start_time"]
            span = (last - first) or 1.0
            start_v = float(notes[0].get("velocity", 100))
            end_v = max(1.0, min(127.0, start_v * float(amount)))
            for n in notes:
                t = (n["start_time"] - first) / span
                out.append(base(n, velocity=round(start_v + (end_v - start_v) * t, 1)))
        elif operation == "duration_scale":
            out = [base(n, duration=round(max(0.01, n["duration"] * float(amount)), 6))
                   for n in notes]
        else:
            raise ValueError(
                "unknown operation %r; try: transpose, scale_fold, harmonize, invert, "
                "retrograde, quantize, humanize, velocity_ramp, duration_scale" % operation)

        summary = {"operation": operation, "notes_in": len(notes), "notes_out": len(out),
                   "key": f"{self.NOTE_NAMES[root_pc]} {scale}",
                   "preview": bool(preview)}
        if preview:
            summary["notes"] = out
            return summary

        # Replace wholesale: a transform can move any note anywhere, so clearing
        # the window first is the only way to avoid stacking old notes under new.
        self.remove_notes(clip_path, from_time=0.0, time_span=1e6,
                          from_pitch=0, pitch_span=128)
        summary["written"] = self.add_notes(clip_path, out)
        return summary

    # --- modulation matrix -------------------------------------------------------------
    # Live's modern instruments (Wavetable, Drift, Meld...) expose their modulation
    # MATRIX to the LOM: which sources modulate which parameters, and by how much.
    # That is patch *architecture*, not just knob values — a snapshot captures where
    # a knob sits, this decides what moves it.
    def _modulation_source_count(self, device_path: str, probe_limit: int = 64) -> int:
        """Live exposes no source count, so find it by probing until it refuses."""
        count = 0
        for source in range(probe_limit):
            try:
                self.b.call(device_path, "get_modulation_value", 0, source)
            except Exception:
                break
            count = source + 1
        return count

    def modulation_matrix(self, track: int, device: int,
                          include_zero: bool = False) -> dict:
        """Read a device's modulation matrix: every source -> target amount.

        Only NON-ZERO routings are listed by default — a full grid is mostly
        zeros (Wavetable is 5 targets x 13 sources = 65 cells) and the wiring is
        what matters. Live exposes target names but NOT source names, so sources
        are reported by index; ``sources`` gives the count.
        """
        path = f"live_set tracks {track} devices {device}"
        targets = self.b.get(path, "visible_modulation_target_names") or []
        if not targets:
            return {"device": self.b.get(path, "name"), "modulatable": False,
                    "note": "this device exposes no modulation matrix to the LOM"}
        sources = self._modulation_source_count(path)
        routings = []
        for t_index, t_name in enumerate(targets):
            for s_index in range(sources):
                amount = self.b.call(path, "get_modulation_value", t_index, s_index)
                if include_zero or (amount not in (None, 0, 0.0)):
                    routings.append({"target_index": t_index, "target": t_name,
                                     "source_index": s_index,
                                     "amount": round(float(amount), 4)})
        return {"device": self.b.get(path, "name"), "modulatable": True,
                "targets": targets, "sources": sources,
                "active_routings": len(routings), "routings": routings,
                "note": ("Live exposes target names but not source names, so sources "
                         "are by index (0..%d)." % (sources - 1))}

    def modulate(self, track: int, device: int, parameter: str | int,
                 source: int, amount: float) -> dict:
        """Route a modulation source to a parameter at a given amount.

        Adds the parameter to the matrix first when it isn't already a target —
        ``add_parameter_to_modulation_matrix`` returns the new target index — so
        the caller just names a parameter and an amount without knowing whether
        the device is already wired for it.
        """
        path = f"live_set tracks {track} devices {device}"
        params = self.b.get(path, "parameters") or []

        if isinstance(parameter, int):
            p_index = parameter
        else:
            needle = str(parameter).lower()
            matches = [i for i, p in enumerate(params)
                       if str(p.get("name", "")).lower() == needle]
            if not matches:
                matches = [i for i, p in enumerate(params)
                           if needle in str(p.get("name", "")).lower()]
            if not matches:
                raise ValueError("no parameter matching %r on %s"
                                 % (parameter, self.b.get(path, "name")))
            p_index = matches[0]
        p_path = f"{path} parameters {p_index}"
        p_name = params[p_index].get("name")

        if not self.b.call(path, "is_parameter_modulatable", {"$path": p_path}):
            raise ValueError("%r is not modulatable on this device" % p_name)

        targets = self.b.get(path, "visible_modulation_target_names") or []
        if p_name in targets:
            t_index = targets.index(p_name)
            added = False
        else:
            t_index = int(self.b.call(path, "add_parameter_to_modulation_matrix",
                                      {"$path": p_path}))
            added = True

        self.b.call(path, "set_modulation_value", t_index, int(source), float(amount))
        applied = self.b.call(path, "get_modulation_value", t_index, int(source))
        return {"device": self.b.get(path, "name"), "parameter": p_name,
                "parameter_index": p_index, "target_index": t_index,
                "added_to_matrix": added, "source_index": int(source),
                "requested": float(amount),
                "amount": round(float(applied), 4),
                "ok": abs(float(applied) - float(amount)) < 1e-3}

    # --- metering ---------------------------------------------------------------------
    def meters_observed(self, duration_ms: float = 1000.0,
                        include_return_tracks: bool = False) -> dict:
        """Peak-hold that catches level CHANGES Live pushes, plus a polled baseline.

        How Live's meter listener actually behaves (measured, after an earlier wrong
        guess): it fires **on value change, not at a fixed rate**. A decaying sound
        produces a burst of hundreds of events; a steady sustained note produced
        **one event in three seconds**. So it is excellent at catching transients and
        useless as a "what is the level right now" source.

        Therefore both are used: an immediate polled reading establishes the
        baseline, and the observed stream can only raise the peak. That way a steady
        tone still reports its level, and a kick between polls is still caught.

        Events are collapsed here into peak/mean per track — a busy passage can emit
        hundreds per second per track (~57 KB of JSON in one burst), which is no use
        to a caller. Subscriptions are torn down even on error, so a stray listener
        cannot keep flooding.
        """
        names = [t.get("name") for t in (self.b.get("live_set", "tracks") or [])]
        candidates = [(f"live_set tracks {i}", names[i]) for i in range(len(names))]
        if include_return_tracks:
            returns = self.b.get("live_set", "return_tracks") or []
            candidates += [(f"live_set return_tracks {i}", r.get("name") or f"Return {i}")
                           for i, r in enumerate(returns)]
        flags = self.b.get_many([(p, "has_audio_output") for p, _ in candidates])
        watched = [(p, n) for (p, n), ok in zip(candidates, flags) if ok]
        watched.append(("live_set master_track", "Main"))

        # Baseline first: a steady tone emits almost no change-events, so without
        # this a sustained sound would report as silent.
        baseline = self.b.get_many([(p, "output_meter_level") for p, _ in watched])
        samples: dict[str, list] = {
            label: [float(v)] if isinstance(v, (int, float)) else []
            for (_, label), v in zip(watched, baseline)}

        subs: dict[int, str] = {}
        try:
            for path, label in watched:
                subs[self.b.observe(path, "output_meter_level")] = label
            deadline = time.monotonic() + max(0.05, float(duration_ms) / 1000.0)
            while time.monotonic() < deadline:
                for event in self.b.drain_events(0.05):
                    label = subs.get(event.get("sub"))
                    if label is not None and isinstance(event.get("value"), (int, float)):
                        samples[label].append(float(event["value"]))
        finally:
            for sub in subs:
                try:
                    self.b.unobserve(sub)
                except Exception:
                    pass
            self.b.drain_events(0.0)          # discard anything still in flight

        rows = []
        total = 0
        for _, label in watched:
            values = samples.get(label) or []
            total += len(values)
            peak = max(values) if values else 0.0
            rows.append({"track": label, "peak": round(peak, 4),
                         "mean": round(sum(values) / len(values), 4) if values else 0.0,
                         "db": _to_db(peak), "samples": len(values),
                         "silent": peak < 1e-4})
        seconds = max(0.001, float(duration_ms) / 1000.0)
        return {"mode": "observed_peak", "duration_ms": duration_ms,
                "change_events": max(0, total - len(watched)),
                "event_rate_hz": round(max(0, total - len(watched)) / seconds, 1),
                "tracks": rows,
                "note": ("Peak of a polled baseline plus every level CHANGE Live "
                         "pushed during the window — Live's meter listener fires on "
                         "change, not at a fixed rate, so a steady tone emits almost "
                         "nothing while a decay emits a burst. Values are Live's "
                         "normalized 0..1 scale; 'db' is an estimate.")}

    def meters(self, duration_ms: float = 0.0, interval_ms: float = 25.0,
               include_return_tracks: bool = False) -> dict:
        """Read every track's output level — instantaneously, or peak-held.

        A single read is one instant of a moving signal, which says almost
        nothing: catch a kick between hits and the track reads silent. So a
        non-zero ``duration_ms`` samples repeatedly and keeps the PEAK per
        track, which is the number that actually answers "how hot is this?".

        Each sample is ONE batched round-trip for all tracks, so the tracks are
        read together rather than smeared across a sweep.

        Live's meter is a normalized 0..1 value on its own display scale, not
        dBFS. ``db`` is therefore reported as an ESTIMATE (20·log10) — good for
        comparing tracks against each other and for spotting something running
        hot, but not a substitute for measuring a rendered file with
        ``live_analyze_wav`` when an exact number matters.
        """
        names = [t.get("name") for t in (self.b.get("live_set", "tracks") or [])]
        candidates = [(f"live_set tracks {i}", names[i]) for i in range(len(names))]
        if include_return_tracks:
            returns = self.b.get("live_set", "return_tracks") or []
            candidates += [(f"live_set return_tracks {i}", r.get("name") or f"Return {i}")
                           for i, r in enumerate(returns)]

        # A track routed to MIDI has NO output meter at all — reading one raises
        # ("Tracks with MIDI output have no 'output_meter_left' property"), which
        # would fail the whole batch. Ask first, meter only what makes audio.
        audio_flags = self.b.get_many([(p, "has_audio_output") for p, _ in candidates])
        skipped = [label for (_, label), ok in zip(candidates, audio_flags) if not ok]
        kept = [(p, label) for (p, label), ok in zip(candidates, audio_flags) if ok]

        paths = [p for p, _ in kept] + ["live_set master_track"]
        labels = [label for _, label in kept] + ["Main"]

        pairs = [(p, prop) for p in paths
                 for prop in ("output_meter_left", "output_meter_right")]

        def sample() -> list[float]:
            return [float(v or 0.0) for v in self.b.get_many(pairs)]

        peak = sample()
        samples = 1
        if duration_ms and duration_ms > 0:
            deadline = time.monotonic() + float(duration_ms) / 1000.0
            while time.monotonic() < deadline:
                current = sample()
                peak = [max(a, b) for a, b in zip(peak, current)]
                samples += 1
                time.sleep(max(0.0, float(interval_ms) / 1000.0))

        rows = []
        for i, label in enumerate(labels):
            left, right = peak[i * 2], peak[i * 2 + 1]
            loudest = max(left, right)
            rows.append({
                "track": label,
                "left": round(left, 4),
                "right": round(right, 4),
                "db": _to_db(loudest),
                "silent": loudest < 1e-4,
            })
        result = {"mode": "peak" if samples > 1 else "instant",
                  "samples": samples,
                  "duration_ms": duration_ms,
                  "tracks": rows}
        if skipped:
            result["no_audio_output"] = skipped
        result.update({
            "sample_rate_hz": (round(samples / (duration_ms / 1000.0), 1)
                               if duration_ms else None),
            "note": ("Live's meter is a normalized 0..1 display scale; 'db' is an "
                     "estimate (20*log10) for comparison, not calibrated dBFS. "
                     "Sampling is limited by bridge round-trip latency (a few Hz), "
                     "so this is a coarse peak-hold that CAN miss a short transient "
                     "— good for balance, gain staging and 'which track is making "
                     "sound', not for catching true peaks. For an exact figure, "
                     "capture with live_tap_capture (or render) and measure with "
                     "live_analyze_wav, which is sample-accurate.")})
        return result

    # --- loading devices from the browser ---------------------------------------------
    # Live's browser IS in the LOM (`app.browser`) with a `load_item` function, so
    # devices can be loaded without touching the UI. Everything below — every
    # instrument, MIDI effect and audio effect — was previously a manual
    # drag/double-click, which made otherwise-scriptable work need a human.
    BROWSER_CATEGORIES = ("instruments", "midi_effects", "audio_effects",
                          "plugins", "max_for_live", "drums", "sounds")

    def _scan_level(self, paths: list[str]) -> list[dict]:
        """Read one level of the browser tree: children of every given node.

        ``children`` gives names but not flags, so ``is_loadable``/``is_folder``
        are fetched for the whole level in ONE batched round-trip rather than
        two calls per item.
        """
        # Fetch EVERY parent's children in one round-trip. Doing it per parent is
        # an N+1 query against a bridge whose cost is dominated by the hop onto
        # Live's main thread: scanning `plugins` to depth 2 (32 vendor folders)
        # took 15.2 s that way, and `sounds` 21.8 s — not because of data volume
        # but because of 32 separate hops.
        items: list[dict] = []
        results = self.b.batch([{"method": "get",
                                 "params": {"path": p, "prop": "children"}}
                                for p in paths])
        for parent, res in zip(paths, results):
            if not res.get("ok"):
                continue
            for i, kid in enumerate(res.get("result") or []):
                items.append({"path": f"{parent} children {i}",
                              "name": str((kid or {}).get("name") or "")})
        if not items:
            return []
        flags = self.b.get_many([(it["path"], prop) for it in items
                                 for prop in ("is_loadable", "is_folder")])
        for n, it in enumerate(items):
            it["loadable"] = bool(flags[n * 2])
            it["folder"] = bool(flags[n * 2 + 1])
        return items

    # --- browser index (disk cache) ----------------------------------------------------
    # Walking the live tree costs a main-thread hop per FOLDER. Batching the level
    # reads took `plugins` to depth 2 from 15.2s to 2.3s, but `sounds` (1872 items)
    # still costs ~15s and every search re-pays it. The index is that walk done
    # once and written to disk, so later searches are a local file read.
    #
    # It is a SNAPSHOT, deliberately: it records when it was built and of what, and
    # goes stale when plugins/packs are installed. Staleness is reported rather
    # than hidden, and `refresh=True` rebuilds.
    def browser_index_path(self) -> str:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            return os.path.join(base, "AI-Bridge", "browser_index.json")
        if sys.platform == "darwin":
            return os.path.expanduser(
                "~/Library/Application Support/AI-Bridge/browser_index.json")
        return os.path.expanduser("~/.ai-bridge/browser_index.json")

    def build_browser_index(self, categories: list[str] | None = None,
                            max_depth: int = 2) -> dict:
        """Walk the browser once and persist it. Slow by nature — that's the point."""
        cats = [c for c in (categories or self.BROWSER_CATEGORIES)
                if c in self.BROWSER_CATEGORIES]
        started = time.monotonic()
        index: dict = {"version": 1, "max_depth": int(max_depth), "categories": {}}
        total = 0
        for cat in cats:
            frontier = [f"live_app browser {cat}"]
            rows = []
            for _ in range(max(1, int(max_depth))):
                level = self._scan_level(frontier)
                if not level:
                    break
                rows.extend({"name": it["name"], "path": it["path"],
                             "loadable": it["loadable"], "folder": it["folder"]}
                            for it in level)
                frontier = [it["path"] for it in level if it["folder"]]
                if not frontier:
                    break
            index["categories"][cat] = rows
            total += len(rows)
        index["built_at"] = time.time()
        index["item_count"] = total
        index["build_seconds"] = round(time.monotonic() - started, 2)

        path = self.browser_index_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(index, fh)
        return {"built": True, "path": path, "categories": cats,
                "items": total, "max_depth": int(max_depth),
                "seconds": index["build_seconds"]}

    def _load_browser_index(self) -> dict | None:
        try:
            with open(self.browser_index_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("categories") else None

    def browser_index_status(self) -> dict:
        index = self._load_browser_index()
        if not index:
            return {"exists": False, "path": self.browser_index_path(),
                    "hint": "run live_browser_index with refresh=true to build it"}
        age = time.time() - float(index.get("built_at") or 0)
        return {"exists": True, "path": self.browser_index_path(),
                "items": index.get("item_count"),
                "max_depth": index.get("max_depth"),
                "categories": sorted(index.get("categories", {})),
                "age_hours": round(age / 3600.0, 1),
                "note": ("A snapshot — install a plugin or pack and it goes stale; "
                         "rebuild with refresh=true.")}

    def browse(self, category: str, query: str | None = None,
               limit: int = 60, max_depth: int = 1) -> dict:
        """List/filter browser items, descending ``max_depth`` levels.

        Depth matters: the top level of ``plugins`` is VENDOR FOLDERS, not
        plugins (32 vendors hiding ~150 plugins here), so a depth-1 listing of
        that category shows no plugins at all.
        """
        if category not in self.BROWSER_CATEGORIES:
            raise ValueError("category must be one of %s"
                             % (", ".join(self.BROWSER_CATEGORIES),))
        needle = query.lower() if query else None

        # Use the index when it covers this category to at least the depth asked
        # for; a shallower index would silently under-report, so fall through to
        # a live walk rather than answer from it.
        index = self._load_browser_index()
        if index and category in index.get("categories", {}) \
                and int(index.get("max_depth", 0)) >= int(max_depth):
            rows = [{"name": it["name"], "path": it["path"], "folder": it["folder"]}
                    for it in index["categories"][category]
                    if it.get("loadable")
                    and (not needle or needle in str(it["name"]).lower())]
            return {"category": category, "source": "index",
                    "index_age_hours": round(
                        (time.time() - float(index.get("built_at") or 0)) / 3600.0, 1),
                    "scanned": len(index["categories"][category]),
                    "matched": len(rows), "items": rows[:max(1, int(limit))]}

        frontier = [f"live_app browser {category}"]
        rows, scanned = [], 0
        for _ in range(max(1, int(max_depth))):
            level = self._scan_level(frontier)
            if not level:
                break
            scanned += len(level)
            for it in level:
                if it["loadable"] and (not needle or needle in it["name"].lower()):
                    rows.append({"name": it["name"], "path": it["path"],
                                 "folder": it["folder"]})
            frontier = [it["path"] for it in level if it["folder"]]
            if not frontier:
                break
        return {"category": category, "source": "live", "scanned": scanned,
                "matched": len(rows), "items": rows[:max(1, int(limit))]}

    def load_device(self, name: str, track: int | None = None,
                    category: str | None = None, max_depth: int = 3) -> dict:
        """Load a device onto a track by NAME — no UI, no dragging.

        Live places it correctly for its type (a MIDI effect lands before the
        instrument). Verified by counting the track's devices before and after,
        so a silent no-op is reported as a failure rather than assumed to have
        worked. Exact name matches win over substring ones, so "Chord" doesn't
        accidentally load "Chorus".
        """
        categories = [category] if category else list(self.BROWSER_CATEGORIES)
        needle = str(name).lower()
        exact: tuple | None = None
        loose: tuple | None = None

        # Consult the disk index first — it turns a multi-second tree walk into a
        # file read. Only LOADABLE entries are considered, same rule as the live
        # walk, and a miss falls through to walking rather than failing.
        index = self._load_browser_index()
        if index:
            for cat in categories:
                for it in index.get("categories", {}).get(cat, []):
                    if not it.get("loadable"):
                        continue
                    low = str(it["name"]).lower()
                    if low == needle:
                        exact = (cat, it["path"], it["name"])
                        break
                    if needle in low and loose is None:
                        loose = (cat, it["path"], it["name"])
                if exact:
                    break

        # Breadth-first, because the interesting items are not all at the top:
        # `plugins` opens on VENDOR FOLDERS, so a top-level-only search finds the
        # folder "Vital Audio" and never the plugin inside it (field-hit
        # 2026-08-14 — it "loaded" the folder, which did nothing).
        # Only LOADABLE items can match; folders are for descending into.
        for cat in ([] if exact else categories):
            frontier = [f"live_app browser {cat}"]
            for _ in range(max(1, int(max_depth))):
                level = self._scan_level(frontier)
                if not level:
                    break
                for it in level:
                    if not it["loadable"]:
                        continue
                    low = it["name"].lower()
                    if low == needle:
                        exact = exact or (cat, it["path"], it["name"])
                        break
                    if needle in low and loose is None:
                        loose = (cat, it["path"], it["name"])
                if exact:
                    break
                frontier = [it["path"] for it in level if it["folder"]]
                if not frontier:
                    break
            if exact:
                break
        hit = exact or loose
        if not hit:
            raise ValueError(
                "no LOADABLE browser item matching %r in %s (searched %d level(s); "
                "raise max_depth for deeply nested items)"
                % (name, ", ".join(categories), max_depth))
        cat, item_path, found_name = hit

        if track is not None:
            before = len(self.b.get(f"live_set tracks {track}", "devices") or [])
            self.b.set("live_set view", "selected_track",
                       {"$path": f"live_set tracks {track}"})
        else:
            before = None

        self.b.call("live_app browser", "load_item",
                    {"$path": item_path})

        out = {"loaded": True, "name": found_name, "category": cat,
               "matched": "exact" if exact else "substring"}
        if track is not None:
            devices = self.b.get(f"live_set tracks {track}", "devices") or []
            out["track"] = track
            out["devices"] = [d.get("name") for d in devices]
            out["verified"] = True
            if len(devices) <= before:
                out["loaded"] = False
                out["error"] = (
                    "the browser reported no error but the track's device count did "
                    "not change — the item may not be loadable onto this track type")
        else:
            # WITHOUT a track there is nothing to compare, so `loaded` is the browser's
            # word rather than an observation — and the device landed on whatever track
            # Live happened to have selected. Saying so matters: the tool description
            # used to promise verification unconditionally, so a caller omitting
            # `track` (it is not required) would read `loaded: true` as proof.
            out["verified"] = False
            out["note"] = ("no `track` given: loaded onto Live's CURRENTLY SELECTED "
                           "track, and NOT verified — this is the browser reporting "
                           "success, not a device count that changed. Pass `track` to "
                           "choose the destination and have the result checked.")
        return out

    # --- plugin (VST/AU) parameters --------------------------------------------------
    def plugin_parameters(self, track: int, device: int, query: str | None = None,
                          limit: int = 60, offset: int = 0) -> dict:
        """Search a plugin's FULL parameter list — including the ones Live hides.

        A third-party plugin exposes almost nothing through the LOM: Live lists
        only the parameters that have been *configured* on the device, so a
        freshly loaded Vital reports exactly one (``Device On``) out of **2855**
        (measured 2026-08-13; matches the plugin's real count independently
        known from the APC project). ``PluginDevice.get_parameter_names()``
        returns the whole list, which is how the hidden ones become visible.

        Why this is a search and not a dump: that call returned ~52 KB for
        Vital — enough to blow a model's context on its own. Filtering happens
        here, so a question like "which parameters mention 'cutoff'" costs a
        handful of rows instead of thousands.

        ``controllable`` marks the parameters that can actually be read/written
        today (i.e. present in ``device.parameters``); everything else is
        name-only until it is configured on the device in Live.
        """
        path = f"live_set tracks {track} devices {device}"
        names = self.b.call(path, "get_parameter_names") or []
        exposed = self.b.get(path, "parameters") or []
        exposed_names = {p.get("name") for p in exposed if isinstance(p, dict)}

        rows = [{"index": i, "name": n, "controllable": n in exposed_names}
                for i, n in enumerate(names)]
        if query:
            needle = str(query).lower()
            rows = [r for r in rows if needle in str(r["name"]).lower()]

        total_matched = len(rows)
        offset = max(0, int(offset))
        limit = max(1, int(limit))
        page = rows[offset:offset + limit]

        out = {
            "device_name": self.b.get(path, "name"),
            "total_parameters": len(names),
            "controllable_now": len(exposed_names),
            "matched": total_matched,
            "returned": len(page),
            "offset": offset,
            "parameters": page,
        }
        if query:
            out["query"] = query
        if total_matched > offset + len(page):
            out["more"] = True
        if len(exposed_names) < len(names):
            out["note"] = (
                "Live exposes only CONFIGURED plugin parameters to the LOM, so "
                "%d of %d are name-only: their values cannot be read or set until "
                "they are added on the device in Live (Configure). Names are still "
                "useful for finding what a plugin has."
                % (len(names) - len(exposed_names), len(names)))
        return out

    # --- snapshots / morph ----------------------------------------------------------
    # Capture every parameter of a device or rack, recall it, or interpolate
    # between two captures. Stateless: capture returns the snapshot; the caller
    # holds it and hands it back to apply/morph. For a rack, the parameters are
    # its macros + chain selector (v1 does not descend into nested chain devices).
    _SNAPSHOT_PROPS = ("name", "value", "min", "max", "is_quantized")

    def snapshot(self, track: int, device: int) -> dict:
        """Capture every parameter of a device/rack as a recallable snapshot.

        One batched read (see ``parameters``). The returned object round-trips
        straight into ``apply_snapshot`` / ``morph``.
        """
        path = f"live_set tracks {track} devices {device}"
        name = self.b.get(path, "name")
        n = len(self.b.get(path, "parameters"))
        pairs = [(f"{path} parameters {i}", prop)
                 for i in range(n) for prop in self._SNAPSHOT_PROPS]
        values = self.b.get_many(pairs)
        w = len(self._SNAPSHOT_PROPS)
        params = [dict({"index": i}, **dict(zip(self._SNAPSHOT_PROPS, values[i * w:(i + 1) * w])))
                  for i in range(n)]
        return {"track": track, "device": device, "device_name": name,
                "count": n, "params": params}

    def apply_snapshot(self, track: int, device: int, snapshot: dict) -> dict:
        """Recall a snapshot: set every parameter back to its captured value."""
        return self._write_params(track, device, snapshot,
                                   lambda p, i: float(p["value"]))

    def morph(self, track: int, device: int, snapshot_a: dict, snapshot_b: dict,
              amount: float) -> dict:
        """Interpolate between two snapshots and apply the blend (``amount`` 0..1).

        Per parameter (matched by index): ``v = a + (b - a) * amount``. Quantized
        parameters (stepped — on/off, selectors) snap to the nearest step so the
        blend never lands on an invalid in-between. One batched write.
        """
        t = max(0.0, min(1.0, float(amount)))
        a_by_i = {int(p["index"]): p for p in snapshot_a.get("params", [])}
        b_by_i = {int(p["index"]): p for p in snapshot_b.get("params", [])}

        def blended(p, i):
            pa, pb = a_by_i.get(i, p), b_by_i.get(i, p)
            av, bv = float(pa["value"]), float(pb["value"])
            v = av + (bv - av) * t
            if pa.get("is_quantized") or pb.get("is_quantized"):
                v = round(v)
            return v

        return self._write_params(track, device, snapshot_a, blended)

    def _write_params(self, track: int, device: int, snapshot: dict, value_of) -> dict:
        """Set each snapshot parameter via one batched write.

        Guards against applying to the wrong device (parameter-count mismatch),
        clamps to each parameter's min/max, and uses ``batch`` (not the strict
        ``set_many``) so a read-only parameter is reported in ``failed`` instead
        of aborting the whole recall.
        """
        path = f"live_set tracks {track} devices {device}"
        live_n = len(self.b.get(path, "parameters"))
        params = snapshot.get("params", [])
        if len(params) != live_n:
            raise ValueError(
                "snapshot has %d parameters but the device at track %d / device %d "
                "has %d — refusing to apply (wrong device?)"
                % (len(params), track, device, live_n))
        ops = []
        for p in params:
            i = int(p["index"])
            v = float(value_of(p, i))
            lo, hi = p.get("min"), p.get("max")
            if lo is not None and hi is not None:
                v = max(float(lo), min(float(hi), v))
            ops.append({"method": "set", "params": {
                "path": f"{path} parameters {i}", "prop": "value", "value": v}})
        results = self.b.batch(ops)
        failed = [{"index": int(params[k]["index"]), "name": params[k].get("name"),
                   "error": r.get("error")}
                  for k, r in enumerate(results) if not r.get("ok")]
        out = {"applied": True, "track": track, "device": device,
               "device_name": snapshot.get("device_name"), "count": len(ops)}
        if failed:
            out["failed"] = failed
        return out

    # --- clip automation envelopes ----------------------------------------------------------
    def insert_envelope(self, clip_path: str, parameter_path: str,
                        steps: list[dict]) -> int:
        """Write automation steps into a clip's envelope for one parameter.

        Each step: {"time": beats, "value": v, "length": beats?}. Use ``ramp``
        to generate a smooth sweep's steps.
        """
        return self.b.request("clip_envelope_insert", path=clip_path,
                              parameter=parameter_path, steps=steps)

    def read_envelope(self, clip_path: str, parameter_path: str,
                      times: list[float]) -> list[float]:
        """Sample the envelope's value at each time (beats) — for verification."""
        return self.b.request("clip_envelope_read", path=clip_path,
                              parameter=parameter_path, times=times)

    def clear_envelope(self, clip_path: str, parameter_path: str | None = None) -> bool:
        """Clear one parameter's envelope, or all envelopes of the clip."""
        return self.b.request("clip_envelope_clear", path=clip_path,
                              parameter=parameter_path)

    @staticmethod
    def ramp(start_time: float, end_time: float, start_value: float,
             end_value: float, steps: int = 16) -> list[dict]:
        """Steps approximating a linear sweep — feed to ``insert_envelope``.

        Envelope steps are flat holds, so this is a staircase: step starts
        spread over [start_time, end_time), values rise to exactly
        ``end_value`` on the last step. Lengths are left for ``insert_envelope``
        to auto-fill (each runs to the next step).
        """
        if steps < 2:
            raise ValueError("a ramp needs at least 2 steps")
        return [{"time": start_time + (end_time - start_time) * i / steps,
                 "value": start_value + (end_value - start_value) * i / (steps - 1)}
                for i in range(steps)]

    # --- comping: loop-record takes into take lanes (Live 11+) -----------------------------
    def record_takes(self, track: int, start_beats: float, length_beats: float,
                     passes: int = 2, extra_wait: float = 1.5) -> dict:
        """Loop-record ``passes`` passes over a section — each lands as a take.

        The Cubase-style comping workflow: loop the section, record while it
        cycles, then audition the take lanes and promote the keeper
        (``choose_take``). Blocks for the recording's real duration.

        Clip-launch quantization is disabled during the recording (it silently
        shifts where things land) and restored after. Count-in is READ-ONLY in
        the LOM (found live-validating) — it can't be disabled, so its bars are
        added to the wait window instead.
        """
        import time as _time

        base = f"live_set tracks {track}"
        if not self.b.get(base, "can_be_armed"):
            raise ValueError(f"track {track} cannot be armed (group/master?)")
        # count_in_duration: 0=None, 1=1 bar, 2=2 bars, 3=4 bars — not writable
        count_in_bars = {0: 0, 1: 1, 2: 2, 3: 4}.get(
            int(self.b.get("live_set", "count_in_duration") or 0), 0)
        count_in_beats = count_in_bars * float(
            self.b.get("live_set", "signature_numerator") or 4)
        prev_quant = self.b.get("live_set", "clip_trigger_quantization")
        self.b.set("live_set", "clip_trigger_quantization", 0)
        try:
            self.b.set(base, "arm", True)
            self.b.set("live_set", "loop_start", float(start_beats))
            self.b.set("live_set", "loop_length", float(length_beats))
            self.b.set("live_set", "loop", True)
            self.b.set("live_set", "current_song_time", float(start_beats))
            self.b.set("live_set", "record_mode", True)
            self.b.call("live_set", "start_playing")
            tempo = float(self.b.get("live_set", "tempo"))
            _time.sleep((int(passes) * float(length_beats) + count_in_beats)
                        * 60.0 / tempo + float(extra_wait))
            self.b.call("live_set", "stop_playing")
            self.b.set("live_set", "record_mode", False)
            self.b.set(base, "arm", False)
        finally:
            self.b.set("live_set", "clip_trigger_quantization", prev_quant)
        return {"passes": int(passes), "takes": self.takes(track),
                "main_clips": len(self.b.get(base, "arrangement_clips"))}

    def print_sequence(self, source_track: int, length_beats: float,
                       start_beats: float = 0.0, source_slot: int | None = None,
                       name: str | None = None) -> dict:
        """Print what a track's arp/sequencer PLAYS into editable MIDI notes.

        A generative device (Arpeggiator, a sequencer preset) turns held notes
        into a stream that only exists while playing. This captures that stream
        onto a NEW MIDI track: route its input from the source track (post
        MIDI-effects), fire the source clip, loop-record one pass — the pattern
        becomes ordinary notes you can see and edit, the same way a preset's
        sound becomes knobs you can tweak. Real-time: N beats take N beats.
        """
        src_display = f"{source_track + 1}-"
        dest_path = self.create_midi_track()
        dest = int(dest_path.split()[-1])
        if name:
            self.b.set(dest_path, "name", name)
        # route the new track's MIDI input from the source track (match by
        # display name prefix "N-", the routing list's naming scheme)
        base = f"{dest_path} available_input_routing_types"
        n = len(self.b.get(dest_path, "available_input_routing_types"))
        names = self.b.get_many([(f"{base} {i}", "display_name") for i in range(n)])
        matches = [i for i, nm in enumerate(names)
                   if str(nm).startswith(src_display)]
        if not matches:
            raise ValueError(f"no MIDI-From routing matching {src_display!r} "
                             f"(have: {names})")
        self.b.set(dest_path, "input_routing_type", {"$path": f"{base} {matches[0]}"})
        prev_quant = self.b.get("live_set", "clip_trigger_quantization")
        if source_slot is not None:
            self.b.set("live_set", "clip_trigger_quantization", 0)
            self.b.call(f"live_set tracks {source_track} clip_slots {source_slot}",
                        "fire")
        try:
            result = self.record_takes(dest, start_beats, length_beats, passes=1)
        finally:
            if source_slot is not None:
                self.b.call(f"live_set tracks {source_track}", "stop_all_clips")
                self.b.set("live_set", "clip_trigger_quantization", prev_quant)
        clips = self.b.get(dest_path, "arrangement_clips")
        notes = []
        if clips:
            notes = self.b.request("clip_get_notes",
                                   path=f"{dest_path} arrangement_clips 0")
        return {"track": dest, "track_path": dest_path,
                "clip": f"{dest_path} arrangement_clips 0" if clips else None,
                "notes": len(notes), "takes": result["takes"]}

    def takes(self, track: int) -> list[dict]:
        """List a track's take lanes and the clips on them."""
        base = f"live_set tracks {track}"
        n = len(self.b.get(base, "take_lanes"))
        names = self.b.get_many([(f"{base} take_lanes {i}", "name")
                                 for i in range(n)])
        all_clips = self.b.get_many([(f"{base} take_lanes {i}", "arrangement_clips")
                                     for i in range(n)])
        return [{"index": i, "name": names[i],
                 "clips": [{"path": f"{base} take_lanes {i} arrangement_clips {j}",
                            "name": c.get("name")}
                           for j, c in enumerate(all_clips[i])]}
                for i in range(n)]

    def choose_take(self, track: int, lane: int, clip: int = 0) -> dict:
        """Promote a MIDI take-lane clip onto the track's main lane (the comp pick).

        Implemented as a NOTE COPY through proven primitives: read the lane
        clip's notes, recreate the main-lane clip over the same span, write the
        notes. ⚠️ Never use ``duplicate_clip_to_arrangement`` with a take-lane
        clip — that call CRASHES Live outright (found live-validating,
        2026-08-11, Live 12.4.3). Audio takes can't be promoted this way — comp
        audio in Live's own UI.
        """
        base = f"live_set tracks {track}"
        lane_clip = f"{base} take_lanes {lane} arrangement_clips {clip}"
        if bool(self.b.get(lane_clip, "is_audio_clip")):
            raise ValueError(
                "audio takes cannot be promoted through the bridge (MIDI only) — "
                "pick the take in Live's own comping UI")
        start = float(self.b.get(lane_clip, "start_time"))
        length = float(self.b.get(lane_clip, "length"))
        notes = self.b.request("clip_get_notes", path=lane_clip)
        # replace whatever the recording left on the main lane over that span
        removed = []
        for i in reversed(range(len(self.b.get(base, "arrangement_clips")))):
            cpath = f"{base} arrangement_clips {i}"
            c_start = float(self.b.get(cpath, "start_time"))
            if start <= c_start < start + length:
                removed.append(self.b.get(cpath, "name"))
                self.b.call(base, "delete_clip", {"$path": cpath})
        self.b.call(base, "create_midi_clip", start, length)
        # the new clip is the one starting at `start`
        target = None
        for i in range(len(self.b.get(base, "arrangement_clips"))):
            if float(self.b.get(f"{base} arrangement_clips {i}", "start_time")) == start:
                target = f"{base} arrangement_clips {i}"
                break
        if target is None:
            raise RuntimeError("created the comp clip but cannot find it back")
        if notes:
            self.b.request("clip_add_notes", path=target, notes=[
                {"pitch": n["pitch"], "start_time": n["start_time"],
                 "duration": n["duration"], "velocity": n["velocity"],
                 "mute": n["mute"]} for n in notes])
        return {"chosen": lane_clip, "at_beats": start, "length_beats": length,
                "notes": len(notes), "replaced": removed, "comp_clip": target}

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
        """Batched: 3 round-trips for the whole set, regardless of size.

        GROUP TRACKS ARE NEVER CANDIDATES. An empty group has no devices and no clips,
        so "has neither" called it unused — but deleting a group in Live takes its
        CHILDREN with it, which is the opposite of conservative. A routing-only
        utility track is the same shape and is likewise excluded, since something is
        almost certainly feeding it.
        """
        n = len(self.b.get("live_set", "tracks"))
        props = ("name", "devices", "arrangement_clips", "clip_slots", "is_foldable")
        vals = self.b.get_many([(f"live_set tracks {i}", p)
                                for i in range(n) for p in props])
        candidates = []
        grouped = set()
        for i in range(n):
            row = vals[i * len(props):(i + 1) * len(props)]
            name, devices, arrangement, slots, foldable = row
            # A track type that does not expose `is_foldable` is treated as NOT a
            # group: absent evidence must not make a plain track undeletable.
            if bool(foldable):
                grouped.add(i)
                continue
            if not devices and not arrangement:
                candidates.append({"index": i, "name": name, "slots": len(slots)})
        flags = self.b.get_many([
            (f"live_set tracks {c['index']} clip_slots {s}", "has_clip")
            for c in candidates for s in range(c["slots"])])
        out, at = [], 0
        for c in candidates:
            used = any(flags[at:at + c["slots"]])
            at += c["slots"]
            if not used:
                out.append({"index": c["index"], "name": c["name"]})
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

    def export_stems(self, output_dir: str, start_beats: float | None = None,
                     length_beats: float | None = None, **kw) -> dict:
        try:
            from . import render
        except ImportError:
            import render
        return render.export_stems(self.b, output_dir, start_beats=start_beats,
                                   length_beats=length_beats, **kw)

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
