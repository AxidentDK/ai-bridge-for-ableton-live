"""ableton-live-bridge — remote script (runs inside Live's Python 3.11).

Live loads this package as a Control Surface and calls ``create_instance`` from
here. That entry point arrives in the next Phase-1 increment; for now this
package holds the wire framing (``framing.py``), which is pure stdlib and
independent of the Live API.
"""
