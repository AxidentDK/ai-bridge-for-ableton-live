"""Phase 4b: the Max for Live audio tap — measure audio ANYWHERE in a chain.

The LOM exposes no audio. `live_export` renders only the finished arrangement,
so everything upstream — how a synth sounds *before* the compressor, what one
group bus is doing, whether a send is muddying the low mids — is invisible.
The tap closes that gap: an M4L audio effect (`plugin~ -> sfrecord~ 2 ->
plugout~`) records stereo audio at its own insertion point, so a capture can be
taken at ANY link in the signal chain and fed to `audio_analysis`.

**Transport is a file handshake, not a socket.** The device polls a command
file every 100 ms and writes a status file back after every action. That looks
primitive next to the bridge's TCP, and it is deliberate: Max's [js] has no
socket API worth trusting, files need no ports or permissions, and the status
file doubles as a liveness proof. That proof matters — a stale device instance
(observed after Live's "Collect All and Save") silently ignores commands, and a
freshly loaded one records digital silence until Max finishes wiring the audio
graph. A command that gets no status echo is a dead device, and we say so
rather than returning a zero-byte WAV.

Every command carries a unique id; the device echoes it as ``last_command_id``
and bumps ``seq``. We wait for that echo before believing anything happened.

Device-side notes worth knowing (from `m4l/agent_audio_tap.js`, verified in
Live 12.4.1): `sfrecord~`'s documented "record <ms>" self-terminate does NOT
fire, so the device schedules its own stop; and only an explicit stop finalizes
the WAV header. A capture therefore isn't readable until the stop lands.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import wave
from typing import Any

COMMAND_BASENAME = "agent_audio_tap_command.json"


class TapError(RuntimeError):
    pass


def state_dir() -> str:
    """Fallback location for the tap's command/status files.

    NOTE the authoritative source is ``command_file_from_amxd`` — the built
    device carries its command-file path as the second argument of its [js]
    object, which overrides the .js default entirely. This directory is only
    used when a device is built WITHOUT that argument (then ``install.py``'s
    injected .js default applies).
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "AI-Bridge", "tap")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/AI-Bridge/tap")
    return os.path.expanduser("~/.ai-bridge/tap")


def default_command_file() -> str:
    return os.path.join(state_dir(), COMMAND_BASENAME)


def status_path_for(command_file: str) -> str:
    """Mirror the device's own command->status name derivation.

    The JS does ``commandFile.replace(/command(\\.json)?$/, "status$1")`` and
    falls back to appending ``.status``. Any divergence here means we poll a
    file the device never writes, so this stays byte-compatible with it.
    """
    command_file = str(command_file)
    for suffix, replacement in (("command.json", "status.json"), ("command", "status")):
        if command_file.endswith(suffix):
            return command_file[: -len(suffix)] + replacement
    return command_file + ".status"


def command_file_from_amxd(amxd_path: str) -> str | None:
    """Read the command-file path the DEVICE actually uses, out of the .amxd.

    Authoritative, because the [js] object's second argument wins over the .js
    default: ``js agent_audio_tap.js <command-file>``. Guessing at this cost a
    long detour on 2026-08-13 — the .maxpat in the repo carries no argument,
    but the built .amxd does, so the device was writing to a path nothing was
    watching.

    The .amxd is a chunked container: 4-byte id + 4-byte little-endian length +
    body, repeated; the patcher JSON lives in the ``ptch`` chunk.
    """
    try:
        with open(amxd_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    off = 0
    while off + 8 <= len(data):
        cid = data[off:off + 4]
        length = int.from_bytes(data[off + 4:off + 8], "little")
        if cid == b"ptch":
            body = data[off + 8:off + 8 + length]
            try:
                patch = json.loads(body.decode("utf-8", "replace").rstrip("\x00"))
            except ValueError:
                return None
            for box in patch.get("patcher", {}).get("boxes", []):
                text = (box.get("box") or {}).get("text") or ""
                parts = text.split()
                if len(parts) >= 3 and parts[0] == "js" and parts[1].endswith(".js"):
                    return " ".join(parts[2:])
            return None
        off += 8 + length
    return None


def installed_amxd_paths() -> list[str]:
    """Where install.py puts the device (both platforms' User Library layouts)."""
    home = os.path.expanduser("~")
    subdir = os.path.join("Presets", "Audio Effects", "Max Audio Effect",
                          "AgentAudioTap.amxd")
    return [os.path.join(home, "Documents", "Ableton", "User Library", subdir),
            os.path.join(home, "Music", "Ableton", "User Library", subdir)]


def candidate_dirs() -> list[str]:
    """Where a relative filename written by Max's [js] File plausibly lands.

    Max resolves a relative path against its own search path, which on Windows
    is neither the .amxd's folder nor Live's cwd in any documented way — so the
    location is discovered empirically (see ``discover``) rather than assumed.
    """
    home = os.path.expanduser("~")
    out = [
        state_dir(),          # the injected, agreed location — normal case
        os.getcwd(),
        home,
        os.path.join(home, "Documents"),
        os.path.join(home, "Documents", "Max 9", "Library"),
        os.path.join(home, "Documents", "Max 8", "Library"),
        os.path.join(home, "Documents", "Ableton", "User Library"),
        os.path.join(home, "Documents", "Ableton", "User Library", "Presets",
                     "Audio Effects", "Max Audio Effect"),
        os.environ.get("TEMP") or "",
        os.environ.get("TMP") or "",
        "/tmp",
    ]
    seen, uniq = set(), []
    for d in out:
        if d and d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def discover(extra_dirs: list[str] | None = None,
             basename: str = COMMAND_BASENAME) -> dict[str, Any]:
    """Locate the tap's command/status pair by finding the STATUS file.

    The device writes a status file on ``loadbang``, so once it has been loaded
    onto a track it has already announced where its files live — no guessing at
    Max's path semantics. Returns the resolved paths plus the parsed status, or
    ``found: False`` with the directories searched.
    """
    # 1. Ask the DEVICE where it writes — its [js] argument is authoritative and
    #    beats any search. Only fall back to scanning if it carries no argument.
    for amxd in installed_amxd_paths():
        declared = command_file_from_amxd(amxd)
        if declared:
            status_file = status_path_for(declared)
            return {"found": True, "source": "amxd", "amxd": amxd,
                    "dir": os.path.dirname(declared),
                    "command_file": declared,
                    "status_file": status_file,
                    "status": _read_json(status_file)}

    status_name = os.path.basename(status_path_for(basename))
    searched = list(extra_dirs or []) + candidate_dirs()
    for d in searched:
        candidate = os.path.join(d, status_name)
        if os.path.isfile(candidate):
            status = _read_json(candidate)
            return {"found": True, "dir": d,
                    "command_file": os.path.join(d, basename),
                    "status_file": candidate,
                    "status": status}
    return {
        "found": False,
        "searched": searched,
        "hint": (
            "No %s found. Load the AgentAudioTap device onto a track in Live "
            "(it writes this file on load), then retry. If the device IS "
            "loaded, pass the folder explicitly — Max resolved the relative "
            "path somewhere outside the search list." % status_name),
    }


def _read_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        return None


class AudioTap:
    """Client for one AgentAudioTap device instance.

    Stateless across calls: every command is an atomic write + status wait, so
    a dropped/reloaded device surfaces as a timeout rather than a silent no-op.
    """

    def __init__(self, command_file: str, status_file: str | None = None,
                 ack_timeout: float = 5.0):
        self.command_file = str(command_file)
        self.status_file = str(status_file or status_path_for(command_file))
        self.ack_timeout = float(ack_timeout)

    # --- protocol ------------------------------------------------------------
    def read_status(self) -> dict | None:
        return _read_json(self.status_file)

    def _send(self, command: str, path: str | None = None,
              duration_ms: float | None = None,
              ack_timeout: float | None = None) -> dict:
        """Write one command and wait for the device to echo its id back.

        The echo is the liveness proof: no echo within the timeout means the
        device is missing, stale, or not polling — never "probably fine".
        """
        before = self.read_status() or {}
        before_seq = before.get("seq", -1)
        cid = uuid.uuid4().hex
        payload: dict[str, Any] = {"id": cid, "command": command}
        if path is not None:
            payload["path"] = str(path)
        if duration_ms is not None:
            payload["duration_ms"] = float(duration_ms)

        parent = os.path.dirname(self.command_file) or "."
        if not os.path.isdir(parent):
            raise TapError("command-file folder %r does not exist" % parent)
        # Write whole-file: the device polls at 100 ms and parses the entire
        # contents, so a partial write would be read as invalid JSON.
        with open(self.command_file, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload))

        deadline = time.monotonic() + float(
            self.ack_timeout if ack_timeout is None else ack_timeout)
        while time.monotonic() < deadline:
            status = self.read_status()
            if status and (status.get("last_command_id") == cid
                           or status.get("seq", -1) > before_seq):
                return status
            time.sleep(0.05)
        raise TapError(
            "the tap did not acknowledge %r within %.1fs — the AgentAudioTap "
            "device is not loaded, is a stale instance (reload it), or writes "
            "its status somewhere other than %r"
            % (command, self.ack_timeout if ack_timeout is None else ack_timeout,
               self.status_file))

    def status(self) -> dict:
        return self._send("status")

    def open(self, path: str) -> dict:
        return self._send("open", path=path)

    def start(self, path: str | None = None,
              duration_ms: float | None = None) -> dict:
        return self._send("start", path=path, duration_ms=duration_ms)

    def stop(self) -> dict:
        return self._send("stop")

    # --- capture -------------------------------------------------------------
    def capture(self, output_path: str, duration_ms: float,
                settle_s: float = 0.6) -> dict:
        """Record ``duration_ms`` of audio at the tap's insertion point.

        Verifies the finished WAV rather than trusting the device: a capture
        that produced no readable file, or silence, is reported as such. NOTE:
        the device records what is PLAYING — start Live's transport (or fire a
        clip) around this call, or the result is legitimately silent.
        """
        output_path = str(output_path)
        if not output_path.lower().endswith(".wav"):
            raise TapError("output_path must end in .wav")
        parent = os.path.dirname(output_path) or "."
        if not os.path.isdir(parent):
            raise TapError("output folder %r does not exist" % parent)
        if os.path.exists(output_path):
            raise TapError(
                "output_path %r already exists; refusing to overwrite" % output_path)

        duration_ms = float(duration_ms)
        if duration_ms <= 0:
            raise TapError("duration_ms must be positive")

        self.open(output_path)
        # The device defers its own start by 500 ms after an open (its comment:
        # sfrecord~ needs the fresh open to settle), so the capture wall-clock
        # is that deferral + the requested duration.
        self.start(duration_ms=duration_ms)
        deadline = time.monotonic() + (duration_ms / 1000.0) + 0.5 + max(1.0, settle_s) + 5.0
        while time.monotonic() < deadline:
            status = self.read_status() or {}
            if status.get("recording") is False and os.path.exists(output_path):
                info = _read_wav(output_path)
                if info is not None:
                    info["requested_duration_ms"] = duration_ms
                    info["path"] = output_path
                    return info
            time.sleep(0.1)

        # The timed stop should have fired; force one so the header finalizes.
        try:
            self.stop()
        except TapError:
            pass
        info = _read_wav(output_path)
        if info is None:
            raise TapError(
                "capture produced no readable WAV at %r — the device may not be "
                "on an audio-carrying track, or the path is not writable by Max"
                % output_path)
        info["requested_duration_ms"] = duration_ms
        info["path"] = output_path
        info["warning"] = "capture_needed_forced_stop"
        return info


def _read_wav(path: str) -> dict | None:
    try:
        with wave.open(path, "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            if frames > 0 and rate > 0:
                return {"captured": True,
                        "duration_s": round(frames / float(rate), 3),
                        "sample_rate": rate,
                        "channels": handle.getnchannels(),
                        "bit_depth": handle.getsampwidth() * 8}
    except (wave.Error, EOFError, OSError):
        pass
    return None
