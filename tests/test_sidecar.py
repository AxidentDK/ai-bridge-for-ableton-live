"""Tests for host/sidecar.py — the listening sidecar's READ side.

No Live, no Essentia, no pytest: the tests build a small database to the schema the
module publishes, which is also a check that the published DDL is actually valid SQL.

The case that matters most is ABSENCE. The sidecar is optional, so "not installed"
must be an ordinary answer, never an exception and never a warning.
"""
import contextlib
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))

import sidecar  # noqa: E402

# Invented paths — never a real folder from the machine this was developed on.
KICK = r"D:\Packs\Tech House\kick_deep.wav"
PAD = r"C:\Samples\Pads\melancholy_pad.wav"


def _build(path, *, schema_version=sidecar.SCHEMA_VERSION):
    """Create a fixture database using the module's OWN published DDL."""
    conn = sqlite3.connect(path)
    conn.executescript(sidecar.SCHEMA_SQL)
    conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(schema_version),))
    conn.execute("INSERT INTO meta VALUES ('analyzer', 'essentia-test/msd-musicnn-1')")
    conn.execute("INSERT INTO meta VALUES ('built_at', '2026-08-15T00:00:00Z')")

    conn.execute("INSERT INTO files (id, path, duration_sec) VALUES (1, ?, 0.9)", (KICK,))
    conn.execute("INSERT INTO files (id, path, duration_sec) VALUES (2, ?, 8.4)", (PAD,))
    # A failed analysis: present, but must never surface as a result.
    conn.execute("INSERT INTO files (id, path, error) VALUES (3, 'X:\\broken.wav', 'decode failed')")

    conn.executemany(
        "INSERT INTO tags (file_id, namespace, label, confidence, model) VALUES (?,?,?,?,?)",
        # File 1 is 0.9 s — a ONE-SHOT. It carries both kinds of verdict: ones from
        # heads trained on full tracks (genre, mood_*, style) which are not to be
        # believed at that length, and ones from heads trained on events and single
        # notes (audio_event, nsynth_*) which are.
        [(1, "genre", "tech house", 0.91, "m"),
         (1, "mood_happy", "happy", 0.88, "m"),
         (1, "style_discogs400", "Non-Music---Audiobook", 0.93, "m"),
         (1, "audio_event", "Bass drum", 0.62, "m"),
         (1, "nsynth_instrument", "mallet", 0.80, "m"),
         (1, "instrument", "kick drum", 0.97, "m"),
         # File 2 is 8.4 s — real music, so every head is fair game.
         (2, "mood", "melancholic", 0.88, "m"), (2, "genre", "ambient", 0.74, "m"),
         (2, "mood", "calm", 0.31, "m")])
    conn.execute("INSERT INTO properties (file_id, bpm, key, scale) VALUES (2, 82.0, 'D', 'minor')")
    conn.commit()
    conn.close()


@contextlib.contextmanager
def db(**kwargs):
    """A temp database, with every default lookup path pointed away from the machine."""
    saved = (sidecar.REPO_DB, sidecar.HOME_DB, os.environ.get(sidecar.ENV_VAR))
    tmp = tempfile.mkdtemp(prefix="ai-bridge-sidecar-test-")
    try:
        os.environ.pop(sidecar.ENV_VAR, None)
        path = os.path.join(tmp, "sound_index.db")
        if kwargs.pop("create", True):
            _build(path, **kwargs)
        sidecar.REPO_DB = Path(path)
        sidecar.HOME_DB = Path(tmp) / "absent.db"
        yield path
    finally:
        sidecar.REPO_DB, sidecar.HOME_DB = saved[0], saved[1]
        os.environ.pop(sidecar.ENV_VAR, None)
        if saved[2] is not None:
            os.environ[sidecar.ENV_VAR] = saved[2]
        shutil.rmtree(tmp, ignore_errors=True)


def test_absent_sidecar_is_a_normal_answer():
    with db(create=False):
        info = sidecar.status()
        assert info["available"] is False, info
        assert "live_similar_sounds" in info["fallback"]
        assert "not an error" in info["note"]      # absence must not read as a failure


def test_status_reports_contents():
    with db():
        info = sidecar.status()
        assert info["available"] is True, info
        assert info["files_analyzed"] == 2          # the errored file is not counted
        assert info["files_failed"] == 1
        assert info["namespaces"] == ["audio_event", "genre", "instrument", "mood",
                                      "mood_happy", "nsynth_instrument",
                                      "style_discogs400"]


def test_schema_version_mismatch_is_refused_not_guessed():
    with db(schema_version=sidecar.SCHEMA_VERSION + 1):
        info = sidecar.status()
        assert info["available"] is False, info
        assert "schema_version" in info["reason"]


def test_find_by_mood():
    with db():
        got = sidecar.find(mood="melanch")
        assert got["matches"] == 1, got
        assert got["results"][0]["path"] == PAD
        assert got["results"][0]["bpm"] == 82.0
        assert got["results"][0]["key"] == "D minor"


def test_find_by_genre_and_filename_together():
    """The kick is 0.9 s, so its genre verdict is suppressed as unreliable — visible
    only with include_unreliable. This changed deliberately; the old assertion
    (matches == 1) is what a genre-trained model claiming to know a snare looks like."""
    with db():
        assert sidecar.find(genre="tech house", query="kick")["matches"] == 0
        assert sidecar.find(genre="tech house", query="kick",
                            include_unreliable=True)["matches"] == 1
        assert sidecar.find(genre="tech house", query="pad")["matches"] == 0


def test_one_shot_suppresses_music_trained_verdicts():
    """A head trained on full tracks does not degrade quietly on a 0.9 s sample — it
    answers from its training priors. Those answers must not match a search."""
    with db():
        for kwargs in ({"genre": "tech house"}, {"mood": "happy"},
                       {"tag": "Audiobook"}):
            assert sidecar.find(**kwargs)["matches"] == 0, kwargs
            assert sidecar.find(**kwargs, include_unreliable=True)["matches"] == 1, kwargs


def test_one_shot_keeps_event_and_single_note_verdicts():
    """AudioSet and NSynth are trained on events and single notes, so they stay
    trustworthy at that length — and they are the whole reason a drum library is
    worth indexing."""
    with db():
        got = sidecar.find(event="Bass drum")
        assert got["matches"] == 1, got
        assert got["results"][0]["one_shot"] is True
        labels = {t["label"] for t in got["results"][0]["tags"]}
        assert "Bass drum" in labels and "mallet" in labels
        # ...and the untrustworthy ones are not shown alongside them.
        assert "Non-Music---Audiobook" not in labels
        assert "tech house" not in labels


def test_longer_files_keep_everything():
    with db():
        got = sidecar.find(mood="melancholic")
        assert got["matches"] == 1, got
        assert got["results"][0].get("one_shot") is None


def test_results_are_ranked_by_relevance():
    with db():
        got = sidecar.find(tag="a", limit=5, include_unreliable=True)
        scores = [r["relevance"] for r in got["results"]]
        assert scores == sorted(scores, reverse=True), scores


def test_min_confidence_filters_weak_tags():
    with db():
        assert sidecar.find(mood="calm")["matches"] == 1            # 0.31 present
        assert sidecar.find(mood="calm", min_confidence=0.5)["matches"] == 0


def test_failed_files_never_surface():
    with db():
        assert all("broken" not in r["path"] for r in sidecar.find()["results"])


def test_find_raises_when_unavailable_so_the_tool_can_fall_back():
    with db(create=False):
        try:
            sidecar.find(mood="calm")
        except LookupError:
            return
        raise AssertionError("find() must raise LookupError when the sidecar is absent")


def test_describe_known_and_unknown():
    with db():
        known = sidecar.describe(PAD)
        assert known["found"] is True
        assert known["bpm"] == 82.0
        assert {t["label"] for t in known["tags"]} == {"melancholic", "ambient", "calm"}
        unknown = sidecar.describe(r"Z:\nope.wav")
        assert unknown["found"] is False and "not in the sidecar index" in unknown["note"]


def test_unreadable_database_degrades_instead_of_raising():
    with db() as path:
        with open(path, "wb") as fh:
            fh.write(b"this is not a database")
        info = sidecar.status()
        assert info["available"] is False, info
        assert "unreadable" in info["reason"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
