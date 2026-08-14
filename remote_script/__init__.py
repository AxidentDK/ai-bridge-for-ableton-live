"""AI Bridge for Ableton Live — remote script (runs inside Live's Python 3.11).

Live loads this package as a Control Surface and calls ``create_instance`` with a
``c_instance`` handle. We import the Live-facing glue (``bridge.py``) *lazily*
inside ``create_instance`` so that the pure modules — ``framing``, ``lom``,
``dispatch``, ``server`` — can be imported and unit-tested OUTSIDE Live (where
the ``Live`` / ``_Framework`` modules don't exist).
"""


def create_instance(c_instance):
    """Entry point Live calls to instantiate the control surface."""
    from .bridge import Bridge

    return Bridge(c_instance)
