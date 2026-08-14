"""Control plugin parameters by MIDI CC — routing around Live's Configure ceiling.

Live exposes a plugin's parameters to the LOM only after each one has been added
manually on the device (Configure mode). ``live_plugin_parameters`` can SEE all of
them — a fresh Vital reports 2,855 — but can only set the handful that have been
configured, one click each.

Most synths also accept MIDI CC directly. Sending real CC messages on a virtual
MIDI port that Live listens to therefore reaches parameters the LOM cannot, with no
clicking. The cost is honest and worth stating: **the plugin must be told which CC
maps to which parameter** (its own MIDI-learn), and the track's MIDI input must be
set to this port. Nothing here can do those two steps for you.

Optional by design: needs ``mido`` + ``python-rtmidi``, which are NOT core
dependencies. Without them every function here fails with an explanatory message
and the rest of the bridge is unaffected.

Maps live in ``midi_maps/<name>.json`` as ``{"parameter name": cc_number}`` so a
plugin's own learned layout can be written down once and reused by name.
"""
from __future__ import annotations

import glob
import json
import os

PORT_NAME = "AI Bridge"
_MAPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "midi_maps")


class MidiError(RuntimeError):
    pass


def _mido():
    try:
        import mido  # noqa: F401
        import rtmidi  # noqa: F401
    except ImportError as exc:
        raise MidiError(
            "MIDI CC control needs the optional 'midi' extra: "
            "pip install mido python-rtmidi  (nothing else in the bridge needs it)"
        ) from exc
    import mido
    return mido


_port = None


def _open_port(name: str = PORT_NAME):
    """Open (once) a virtual MIDI output Live can be pointed at."""
    global _port
    mido = _mido()
    if _port is not None:
        return _port
    try:
        _port = mido.open_output(name, virtual=True)
    except (OSError, NotImplementedError) as exc:
        # Windows has no native virtual-port API; a loopback driver is required.
        existing = mido.get_output_names()
        match = next((n for n in existing if name.lower() in n.lower()), None)
        if match is None:
            raise MidiError(
                "could not create a virtual MIDI port (%s). On Windows this needs a "
                "loopback driver such as loopMIDI — create a port there, then pass its "
                "name as port_name. Ports currently visible: %s"
                % (exc, ", ".join(existing) or "none")) from exc
        _port = mido.open_output(match)
    return _port


def ports() -> dict:
    """List MIDI outputs, so a loopback port can be found by its real name."""
    mido = _mido()
    return {"outputs": list(mido.get_output_names()),
            "open": getattr(_port, "name", None),
            "maps": sorted(os.path.splitext(os.path.basename(p))[0]
                           for p in glob.glob(os.path.join(_MAPS_DIR, "*.json")))}


def load_map(name: str) -> dict:
    path = os.path.join(_MAPS_DIR, f"{name}.json")
    if not os.path.isfile(path):
        available = ", ".join(sorted(os.path.splitext(os.path.basename(p))[0]
                                     for p in glob.glob(os.path.join(_MAPS_DIR, "*.json"))))
        raise MidiError("no CC map %r (have: %s). A map is a JSON file of "
                        "{\"parameter name\": cc_number}." % (name, available or "none"))
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def send_cc(cc: int | None = None, value: float = 0.0, channel: int = 0,
            parameter: str | None = None, map_name: str | None = None,
            port_name: str = PORT_NAME) -> dict:
    """Send one CC. Give ``cc`` directly, or a ``parameter`` name plus ``map_name``.

    ``value`` is 0..1 (scaled to 0..127) if it is fractional, otherwise taken as a
    raw 0..127 controller value — because both are natural to ask for.
    """
    mido = _mido()
    if parameter is not None:
        table = load_map(map_name) if map_name else {}
        key = next((k for k in table if k.lower() == parameter.lower()), None)
        if key is None:
            raise MidiError("parameter %r not in map %r" % (parameter, map_name))
        cc = int(table[key])
    if cc is None:
        raise MidiError("give cc, or parameter plus map_name")
    raw = int(round(value * 127)) if 0.0 <= float(value) <= 1.0 and float(value) != int(value) \
        else int(value)
    raw = max(0, min(127, raw))
    port = _open_port(port_name)
    port.send(mido.Message("control_change", channel=int(channel),
                           control=int(cc), value=raw))
    return {"sent": True, "cc": int(cc), "value": raw, "channel": int(channel),
            "port": getattr(port, "name", port_name),
            "note": ("The plugin must have this CC learned, and the track's MIDI From "
                     "must be set to this port — neither can be done from here.")}
