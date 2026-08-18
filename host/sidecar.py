"""The listening sidecar's database — READ side, and the authoritative schema.

A separate process listens to the sample library and writes what it heard into SQLite.
The bridge never imports it, never loads a model and never writes here: it *reads a
file*, which is the only way a near-zero-dependency bridge can benefit from a
heavyweight one.

That sidecar runs on WINDOWS, in plain Python, on numpy and onnxruntime — about 20 MB,
no TensorFlow, no Essentia, no GPU. This paragraph used to say it ran "Essentia under
WSL2", which was true of a design that was never shipped, and the comment outlived it.
WSL exists in this project for exactly one purpose: an offline bench where Essentia
could be installed to verify our mel spectrograms against it. Nothing at runtime goes
near it.

Worth recording why that matters beyond tidiness: a reviewer reading this file took
the stale sentence as current architecture and designed advice around two Python
environments that do not exist. A comment that lies is not a documentation problem, it
is a correctness problem with a slow fuse.

The rule this module exists to enforce:

    sidecar database present  -> use what the listening module identified
    sidecar database absent   -> fall back to live_similar_sounds
    absence is NEVER an error

The fallback is honest rather than equivalent, and the difference is worth stating
because it decides what the caller gets:

* **With the sidecar** you search by *meaning* — "melancholic", "downtempo" — because
  a trained classifier put those words on the file.
* **Without it** you search by filename, and expand from the best filename match by
  *acoustic* similarity using Live 12's own embeddings. Real results, but the words
  come from whoever named the file, not from anything that listened.

Nothing here needs the sidecar to exist. Everything degrades to "not available".
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ENV_VAR = "AI_BRIDGE_SOUND_DB"
DB_FILENAME = "sound_index.db"
REPO_DB = REPO_ROOT / DB_FILENAME
HOME_DB = Path.home() / ".ai-bridge" / DB_FILENAME

# Bumped only on a BREAKING change. The bridge refuses a major it doesn't know rather
# than guessing at columns, because a wrong guess here is a plausible wrong answer.
SCHEMA_VERSION = 3

#: Authoritative DDL. The sidecar builds to exactly this; the bridge only reads it.
#: Kept here, in the consumer, so the contract cannot drift silently — a producer in
#: another language, on another OS, has one file to conform to.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per analysed audio file.
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    -- WINDOWS path, exactly as the bridge and Live see it (D:\\Packs\\kick.wav).
    -- The producer writes this spelling; it is the join key between the two programs,
    -- so whoever writes the index owns any translation into it. (This said the
    -- sidecar "runs under WSL and sees /mnt/d/..." — it does not, and never has in
    -- the shipped design. See the module docstring.)
    path          TEXT NOT NULL UNIQUE,
    source_path   TEXT,               -- the path as the PRODUCER saw it, for provenance
    size_bytes    INTEGER,
    mtime         REAL,               -- with size_bytes: skip unchanged files on re-run
    duration_sec  REAL,
    sample_rate   INTEGER,
    channels      INTEGER,
    analyzed_at   TEXT,               -- ISO-8601 UTC
    analyzer      TEXT,               -- e.g. "essentia-2.1b6/msd-musicnn-1"
    error         TEXT                -- non-NULL: analysis failed; do not retry blindly
);

-- Labels a model put on a file: genre, mood, instrument, ...
CREATE TABLE IF NOT EXISTS tags (
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    namespace   TEXT NOT NULL,        -- 'genre' | 'mood' | 'instrument' | 'style'
    label       TEXT NOT NULL,
    confidence  REAL NOT NULL,        -- 0..1
    model       TEXT NOT NULL,
    PRIMARY KEY (file_id, namespace, label, model)
);

-- Measured scalars: one row per file, not per label.
-- WHICH of these are populated depends on `kind`. A one-shot gets a fundamental and
-- no tempo; a loop gets a key and a tempo and no fundamental. That is deliberate: a
-- BPM derived from one transient, or a "note" for a four-bar chord bed, is not a weak
-- measurement but a meaningless one, and a NULL says so where a number would lie.
CREATE TABLE IF NOT EXISTS properties (
    file_id        INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    kind           TEXT,              -- 'one_shot' | 'loop', by onset density
    onsets         INTEGER,           -- detected onsets; what `kind` was decided on
    bpm            REAL,              -- loops only
    bpm_confidence REAL,              -- how far the winning lag stood out, 0..1
    -- How many bars the file was found to be. A tempo and its double are NOT the
    -- same tempo: the same pulse written at 85 and at 170 differs in note values, so
    -- the grid, the swing and every quantise decision differ with it. The bar count
    -- is the evidence for which one a file actually is, and it is stored so the
    -- claim can be checked rather than taken on trust. NULL = length did not settle it.
    bars           INTEGER,
    key            TEXT,              -- 'F#'      loops only
    scale          TEXT,              -- 'major' | 'minor'
    key_strength   REAL,              -- margin over the runner-up key, not raw fit
    pitch_hz       REAL,              -- one-shots only: YIN fundamental
    pitch_midi     INTEGER,
    pitch_confidence REAL,
    attack_ms      REAL,              -- time to peak; lets a caller correct placement
    decay_ms       REAL,              -- T60, extrapolated from the first 20 dB
    stereo_width   REAL,              -- side/(mid+side) ABOVE 250 Hz; 0 = mono
    stereo_correlation REAL,          -- broadband; < 0 means the mono sum cancels
    danceability   REAL,
    loudness_lufs  REAL               -- BS.1770-4 integrated
);

-- The embedding the tags were derived from, kept so a classifier can be trained
-- without re-decoding the library. float16: half the size, ample for the range.
CREATE TABLE IF NOT EXISTS embeddings (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    dim     INTEGER NOT NULL,
    dtype   TEXT NOT NULL,            -- 'float16'
    vector  BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tags_lookup ON tags(namespace, label, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_files_path  ON files(path);
CREATE INDEX IF NOT EXISTS idx_props_kind  ON properties(kind);
"""


def resolve_db_path(db_path: str | None = None) -> Path | None:
    """First database that exists: explicit -> $AI_BRIDGE_SOUND_DB -> repo -> home."""
    if db_path:
        p = Path(db_path)
        return p if p.exists() else None
    env = (os.environ.get(ENV_VAR) or "").strip()
    candidates = ([Path(env)] if env else []) + [REPO_DB, HOME_DB]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


#: Live's own preview tree. Every device preset and rack in the Core Library has a short
#: .ogg audition clip under here, in a mirror of the real folder structure:
#:
#:     preview  .../Core Library/Ableton Folder Info/Previews/Devices/.../Bass.adv.ogg
#:     preset   .../Core Library/Devices/.../Bass.adv
#:
#: Deleting the segment gives the preset. Checked against the whole index: 2,550 of 2,550
#: previews map to a preset that exists on disk.
_ABLETON_PREVIEWS = os.sep + "Ableton Folder Info" + os.sep + "Previews" + os.sep


def preset_for(path: str) -> str | None:
    """The loadable preset a PREVIEW demonstrates, or None.

    Nearly half a real index turned out to be preview audio rather than samples — short
    demos shipped so a browser can audition presets without loading them. That is not
    noise: it means the presets themselves become searchable BY SOUND, which is the whole
    point of the listening index. But a caller wants the preset, not the demo, and the two
    are never in the same folder.

    Two layouts, both derived rather than stored, so they work on everything already
    indexed without a re-scan or a schema change:

        NKS      .../presets/.previews/Abyss.nksf.ogg   -> .../presets/Abyss.nksf
        Ableton  .../Ableton Folder Info/Previews/Devices/.../Bass.adv.ogg
                                                      -> .../Devices/.../Bass.adv

    The Ableton case was found when a sub-bass search returned
    ``Synth Organ Long Release.adv.ogg`` and there was no way to act on it. Mapping it
    beats hiding it: 2,550 Ableton device presets become searchable by how they sound,
    and ``live_load_device`` can load what comes back.
    """
    if not path.lower().endswith(".ogg"):
        return None
    if f"{os.sep}.previews{os.sep}" in path.lower():
        previews_dir, filename = os.path.split(path)
        candidate = os.path.join(os.path.dirname(previews_dir), filename[:-4])
        return candidate if os.path.exists(candidate) else None
    if _ABLETON_PREVIEWS in path:
        candidate = path.replace(_ABLETON_PREVIEWS, os.sep)[:-4]
        return candidate if os.path.exists(candidate) else None
    return None


def _connect(path: Path) -> sqlite3.Connection:
    """Read-only, always. The bridge is a consumer here and must never write."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def status(db_path: str | None = None) -> dict:
    """Is the sidecar available, and what does it hold? Never raises."""
    path = resolve_db_path(db_path)
    if path is None:
        return {
            "available": False,
            "reason": "no sidecar database found",
            "looked_in": [f"${ENV_VAR}", str(REPO_DB), str(HOME_DB)],
            "fallback": "live_similar_sounds (Live 12's own embeddings)",
            "note": "This is normal, not an error — the sidecar is optional.",
        }
    try:
        with _connect(path) as conn:
            meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
            version = int(meta.get("schema_version", 0))
            if version != SCHEMA_VERSION:
                return {
                    "available": False,
                    "database": str(path),
                    "reason": (f"schema_version {version} but this bridge reads "
                               f"{SCHEMA_VERSION} — refusing to guess at columns"),
                    "fallback": "live_similar_sounds",
                }
            files = conn.execute("SELECT COUNT(*) FROM files WHERE error IS NULL").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM files WHERE error IS NOT NULL").fetchone()[0]
            tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
            spaces = [r[0] for r in conn.execute(
                "SELECT DISTINCT namespace FROM tags ORDER BY namespace")]
    except sqlite3.Error as exc:
        return {"available": False, "database": str(path),
                "reason": f"database unreadable: {exc}", "fallback": "live_similar_sounds"}

    return {
        "available": True,
        "database": str(path),
        "schema_version": version,
        "files_analyzed": files,
        "files_failed": failed,
        "tags": tags,
        "namespaces": spaces,
        "built_at": meta.get("built_at"),
        "analyzer": meta.get("analyzer"),
    }


#: Which recorded namespaces each search term should reach. Producers name a
#: namespace after the model that made the claim, so the mapping lives here.
_NS_PATTERNS = {
    "genre": ("%genre%", "%style%", "%top50tags%"),
    "mood": ("%mood%", "%theme%"),
    "instrument": ("%instrument%", "%timbre%", "%voice%"),
}

#: AudioSet is an ONTOLOGY, and these are its interior nodes. They are true of almost
#: any musical sample and identify nothing: "a snare is Music" restates the question.
#: Measured over 1,647 drum-named files in a real index, `Music` outranked the specific
#: answer on 81% of them and was the top label on 70%.
#:
#: Demoting them — not deleting them — took hit@1 from 37% to 57% across 6,734 files:
#: cymbal 0.6% -> 51%, snare 5.5% -> 68%, hi-hat 2.9% -> 45%. About ten times what
#: correcting the mel-spectrogram was worth, for a sort order.
_GENERIC_EVENTS = (
    "Music", "Musical instrument", "Sound effect", "Silence", "Speech",
    "Inside, small room", "Inside, large room or hall", "Outside, urban or manmade",
    "Outside, rural or natural", "Electronic music", "Cacophony", "Noise",
    "Environmental noise", "Sound reproduction", "Background music",
    "Soundtrack music",
)

#: What a filename match is worth when `query` scores a file, against tag confidences
#: on a 0-1 scale. Mid-scale on purpose: a confident verdict should outrank a name, a
#: weak one should not. See the `query` branch of :func:`find` for the full reasoning.
_NAME_WEIGHT = 0.5

#: WORDS THE VOCABULARY DOES NOT HAVE, mapped to the ones it does.
#:
#: The tag vocabulary is fixed by the models: 34 mood/theme classes, 29 instruments,
#: two-class timbre and reverb heads. A caller does not know that list and should not
#: have to. Asked for "a melancholic pad", the search matched no tag at all and fell
#: back to filename grep — returning results, ranked, with nothing to say the audio had
#: never been consulted. That is this project's recurring failure shape: plausible and
#: wrong, raising nothing.
#:
#: TWO RULES, both enforced by tests rather than by care:
#:
#: 1. **Only map words that match NOTHING today.** A synonym for a word already in the
#:    vocabulary would SHADOW real verdicts with a guess. This is not hypothetical —
#:    `synth` already matches 81,561 tags and `ambient` 48,125, so the obvious
#:    `synth -> synthesizer` mapping would have been actively harmful.
#: 2. **Only map ONTO labels that exist.** A mapping onto a label no head produces is a
#:    silent no-op that looks like a fix.
#:
#: Deliberately NOT mapped, because no honest target exists: `warm` (a mixing term for
#: low-mid richness; the nearest label is `dark`, which sits on 94% of the library and
#: would mean nothing), `gritty`, `lush`. Better to return a filename match and say so
#: than to invent a verdict.
_SYNONYMS = {
    # mood — the case that started this
    "melancholic": ("sad",), "melancholy": ("sad",), "sombre": ("sad",),
    "somber": ("sad",), "wistful": ("sad",), "mournful": ("sad",),
    "sorrowful": ("sad",), "moody": ("sad", "dark"),
    "upbeat": ("happy",), "uplifting": ("happy", "inspiring"),
    "joyful": ("happy",), "cheerful": ("happy",),
    "calm": ("relaxing",), "chilled": ("relaxing",), "mellow": ("relaxing",),
    "peaceful": ("relaxing",), "tranquil": ("relaxing",),
    "driving": ("energetic",), "punchy": ("energetic",), "powerful": ("energetic",),
    "intense": ("energetic",),
    "airy": ("space",), "spacious": ("space",),
    "cinematic": ("film",), "filmic": ("film",), "trailer": ("film", "epic"),
    "grand": ("epic",), "massive": ("epic",), "heroic": ("epic",),
    "nostalgic": ("retro",), "vintage": ("retro",),
    "dreamy": ("dream",), "hypnotic": ("meditative",), "trippy": ("meditative",),
    # instruments — abbreviations a producer types
    "vox": ("voice",), "rhodes": ("electricpiano",), "gtr": ("guitar",),
    "upright": ("doublebass",), "contrabass": ("doublebass",),
    # space
    "reverberant": ("wet",), "roomy": ("wet",), "tight": ("dry",),
}

#: Below this, a file is a ONE-SHOT rather than music. Not an arbitrary round number:
#: the EffNet patch is 128 frames at a 256-sample hop and 16 kHz = 2.048 s, so a
#: shorter file has to be padded or tiled to fill a window. The music-trained heads
#: therefore never saw a full window of real audio for it.
ONE_SHOT_SECONDS = 2.048

#: Namespaces produced by heads trained on FULL MUSIC TRACKS. On a 0.2 s snare these
#: do not degrade quietly — they answer with confidence from their training priors. A
#: Kora one-shot came back `Non-Music---Audiobook` at 0.912, and a vocal chop scored
#: `instrumental` at 0.87. Suppressed for short files.
_MUSIC_TRAINED = ("%genre%", "%style%", "%theme%", "%top50tags%", "mtt",
                  "mood_%", "danceability", "engagement%", "approachability%",
                  "tonal_atonal", "voice_instrumental", "gender", "timbre",
                  "mtg_jamendo_instrument")

#: Trained on single notes or general audio events, so they stay trustworthy on a
#: one-shot: `audio_event` (AudioSet) and the four `nsynth_*` heads.


def _is_one_shot(row, include_unreliable: bool = False) -> bool:
    """Should music-trained verdicts be hidden for this file?

    Short AND not corroborated as a loop. `row` needs `duration_sec`, and `kind` and
    `bars` when they are available — a mapping without them (an older index, or a
    query that did not select them) degrades to the duration test alone rather than
    raising, since absence of evidence is not evidence of a loop.
    """
    if include_unreliable:
        return False
    keys = row.keys() if hasattr(row, "keys") else row
    duration = row["duration_sec"] if "duration_sec" in keys else None
    if duration is None or duration >= ONE_SHOT_SECONDS:
        return False
    kind = row["kind"] if "kind" in keys else None
    bars = row["bars"] if "bars" in keys else None
    return not (kind == "loop" and bars is not None)


def _note_name(midi: int) -> str:
    """MIDI number -> Ableton's spelling, where 60 is C3 rather than C4."""
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[int(midi) % 12]}{int(midi) // 12 - 2}"


def _music_trained_sql(column: str = "t.namespace") -> str:
    return "(" + " OR ".join(f"{column} LIKE ?" for _ in _MUSIC_TRAINED) + ")"


def find(query: str | None = None, genre: str | None = None, mood: str | None = None,
         instrument: str | None = None, min_confidence: float = 0.0,
         limit: int = 20, db_path: str | None = None,
         event: str | None = None, tag: str | None = None,
         include_unreliable: bool = False) -> dict:
    """Search the sidecar's tags. Raises LookupError if it isn't available.

    ``query`` is free text and searches BOTH what the file was heard as and what it is
    called; every other criterion is restricted to the namespaces that can answer it.
    Each result reports ``matched_by`` so a heard verdict is never mistaken for a
    filename hit.

    The caller decides what to do about an unavailable sidecar — see ``mcp_server``
    for the fallback, which lives at the tool layer so the policy is in one visible
    place. That fallback is why ``query`` keeps its filename half: without the sidecar
    it is the only seed acoustic similarity can start from.
    """
    info = status(db_path)
    if not info.get("available"):
        raise LookupError(info.get("reason", "sidecar unavailable"))

    wheres: list[str] = ["f.error IS NULL"]
    params: list = []
    # RELEVANCE. Each criterion contributes the confidence of its BEST matching tag,
    # and the results are ordered by the total. Without this the query had LIMIT but
    # no ORDER BY, so it returned the first N rows in insertion order — "a dark pad"
    # and "something aggressive" came back with the identical three files.
    #
    # Honest limitation: confidences are strictly comparable only WITHIN a head, and a
    # two-class softmax head saturates near 1.0 where a multi-label sigmoid head sits
    # at 0.3. So a criterion answered by a binary head outweighs one answered by a
    # multi-label head. It is still enormously better than insertion order, and the
    # per-criterion scores are returned so a caller can see what drove the ranking.
    score_parts: list[str] = []
    score_params: list = []

    # A tag from a music-trained head on a one-shot is not weak evidence, it is
    # WRONG evidence — so such tags must not match a search or contribute to its
    # ranking. The guard rides on the criterion itself rather than filtering results
    # afterwards, otherwise a one-shot could still outrank real music on a bogus score.
    # DURATION ALONE WAS THE WRONG TEST. A 1-bar loop at 120 BPM lasts 2.0 s, so 26%
    # of everything the listener classified as a loop (2,153 of 8,226, most of them in
    # the 1.5-2.048 s band) was having its genre and mood stripped as if it were a
    # drum hit. One file came back self-contradicting in a single reply:
    # `one_shot: true` alongside `measured: {kind: loop, bars: 1, bpm: 117.3}`.
    #
    # The listener already answers this question properly, by ONSET DENSITY, and the
    # answer was sitting unused in `properties.kind`. It is trusted here only when a
    # SECOND, independent signal agrees: `bars` is non-NULL, meaning the file's length
    # also fits a whole number of bars at the detected tempo. Requiring both matters,
    # because a false `kind='loop'` on a genuine one-shot would re-admit exactly the
    # confident nonsense this guard exists to block — a 0.1 s tom really does come
    # back `Electronic---Techno` at 0.99 and `danceable` at 0.993.
    guard = ("" if include_unreliable else
             " AND (f.duration_sec IS NULL OR f.duration_sec >= ? "
             "OR EXISTS (SELECT 1 FROM properties gp WHERE gp.file_id = f.id "
             "           AND gp.kind = 'loop' AND gp.bars IS NOT NULL) "
             f"OR NOT {_music_trained_sql()})")
    guard_params: list = ([] if include_unreliable
                          else [float(ONE_SHOT_SECONDS), *_MUSIC_TRAINED])

    def _criterion(ns_sql: str, ns_params: list, value: str) -> None:
        """Add one criterion to both the filter and the relevance score."""
        wheres.append(
            f"EXISTS (SELECT 1 FROM tags t WHERE t.file_id = f.id "        # noqa: S608
            f"AND {ns_sql} AND t.label LIKE ? AND t.confidence >= ?{guard})")
        params.extend([*ns_params, f"%{value}%", float(min_confidence), *guard_params])
        score_parts.append(
            f"COALESCE((SELECT MAX(t.confidence) FROM tags t "            # noqa: S608
            f"WHERE t.file_id = f.id AND {ns_sql} AND t.label LIKE ? "
            f"AND t.confidence >= ?{guard}), 0)")
        score_params.extend([*ns_params, f"%{value}%", float(min_confidence),
                             *guard_params])

    # Namespaces are matched by PATTERN, not equality. A producer records the head
    # that made each claim — `mtg_jamendo_moodtheme`, `mood_happy`, `nsynth_instrument`
    # — because that provenance is worth keeping. Demanding namespace = 'mood' found
    # nothing at all across 897k real tags, which is how this was caught.
    for key, value in (("genre", genre), ("mood", mood), ("instrument", instrument)):
        if value:
            patterns = _NS_PATTERNS[key]
            ns_sql = "(" + " OR ".join("t.namespace LIKE ?" for _ in patterns) + ")"
            _criterion(ns_sql, list(patterns), value)
    if event:
        _criterion("t.namespace = 'audio_event'", [], event)
    if tag:
        # Any namespace at all — the escape hatch for a vocabulary we did not predict.
        _criterion("1=1", [], tag)
    if query:
        # `query` is the parameter a caller reaches for first, and the tool promises to
        # find things BY MEANING — so it searches what was HEARD as well as what the
        # file is called. It used to be a bare `f.path LIKE`, which made
        # query="cymbal" a filename grep wearing a listening result's clothes: files
        # tagged `Steam` came back ranked 0.0 with nothing saying the audio was never
        # consulted. Both halves are kept, because in a real library the NAME is also
        # evidence — `Snares/Snare 08.wav` is a snare — just not the only evidence.
        # Matched WORD BY WORD, not as a phrase. "vinyl crackle" is the kind of thing
        # anyone actually types, and no single label reads that way — the file the user
        # wanted is tagged `Crackle`, so a phrase LIKE finds it by name only and the
        # listening contributes nothing. Words under three characters are dropped as
        # noise ("a", "of"), and if that leaves nothing the whole string is used.
        words = [w for w in re.split(r"[^\w]+", query) if len(w) >= 3] or [query]
        # Each word is widened to the vocabulary's own terms where it has none of its
        # own — see _SYNONYMS. Applied to the HEARD half only: a filename search stays
        # literal, because a file called "Melancholic Pad.wav" should match that word
        # and not every file heard as sad.
        terms = [(w, *_SYNONYMS.get(w.lower(), ())) for w in words]
        name_sql = " OR ".join("f.path LIKE ?" for _ in words)

        def _heard_one(n_terms: int) -> str:
            likes = " OR ".join("t.label LIKE ?" for _ in range(n_terms))
            return (f"EXISTS (SELECT 1 FROM tags t WHERE t.file_id = f.id "  # noqa: S608
                    f"AND ({likes}) AND t.confidence >= ?{guard})")

        heard_sql = " OR ".join(_heard_one(len(t)) for t in terms)
        wheres.append(f"(({name_sql}) OR ({heard_sql}))")
        params.extend([f"%{w}%" for w in words])
        for group in terms:
            params.extend([f"%{t}%" for t in group])
            params.extend([float(min_confidence), *guard_params])
        # Weights, and why. The heard half contributes the tag's own confidence, so it
        # stays on the same 0-1 scale as every other criterion. The name half is a
        # yes/no fact with no confidence attached, so it needs a constant: _NAME_WEIGHT
        # sits mid-scale deliberately, letting a confident verdict (0.8) outrank a
        # filename while a weak one (0.3) does not. A file that is both named and heard
        # as the thing scores highest, which is the ordering anyone would want.
        #
        # Averaged over the words rather than summed, so a two-word query stays on the
        # same scale as a one-word one and cannot outrank other criteria by length
        # alone. The consequence, worth being exact about: a query's scores are
        # comparable only WITHIN that query — adding a word that matches little drags
        # the average down, so `pad ambient` scores below `ambient` on the same file.
        # Ranking only ever compares files within one query, where the ordering is the
        # wanted one: matching more of the query wins, however the words are weighted.
        def _per_word(n_terms: int) -> str:
            likes = " OR ".join("t.label LIKE ?" for _ in range(n_terms))
            return (
                f"COALESCE((SELECT MAX(t.confidence) FROM tags t "            # noqa: S608
                f"WHERE t.file_id = f.id AND ({likes}) "
                f"AND t.confidence >= ?{guard}), 0) + "
                f"(CASE WHEN f.path LIKE ? THEN {_NAME_WEIGHT} ELSE 0 END)")

        score_parts.append(
            "(" + " + ".join(_per_word(len(t)) for t in terms)
            + f") / {float(len(words))}")
        for group, w in zip(terms, words):
            score_params.extend([f"%{t}%" for t in group])
            score_params.extend([float(min_confidence), *guard_params, f"%{w}%"])

    # No criterion carries a confidence (filename-only search) -> nothing to rank by,
    # so fall back to a stable alphabetical order rather than insertion order.
    relevance = " + ".join(score_parts) if score_parts else "0"
    order = "relevance DESC, f.path" if score_parts else "f.path"
    # Report WHICH half of a `query` matched. Without this the caller cannot tell a
    # verdict from a filename — the exact confusion that made a `Steam`-tagged file
    # look like a listening result for "cymbal".
    extra_cols, extra_params = "", []
    if query:
        extra_cols = (f", (CASE WHEN ({name_sql}) THEN 1 ELSE 0 END) AS matched_name"
                      f", (CASE WHEN ({heard_sql}) THEN 1 ELSE 0 END) AS matched_heard")
        extra_params = [f"%{w}%" for w in words]
        for group in terms:
            extra_params.extend([f"%{t}%" for t in group])
            extra_params.extend([float(min_confidence), *guard_params])
    sql = (f"SELECT f.id, f.path, f.duration_sec, p.bpm, p.key, p.scale, "  # noqa: S608
           f"p.kind, p.bars, p.pitch_midi, p.pitch_hz, p.attack_ms, p.decay_ms, "
           f"p.stereo_width, p.stereo_correlation, p.loudness_lufs, "
           f"({relevance}) AS relevance{extra_cols} "
           f"FROM files f LEFT JOIN properties p ON p.file_id = f.id "
           f"WHERE {' AND '.join(wheres)} ORDER BY {order} LIMIT ?")
    # SELECT parameters bind before WHERE parameters, in column order.
    all_params = [*score_params, *extra_params, *params, int(limit)]

    with _connect(Path(info["database"])) as conn:
        rows = conn.execute(sql, all_params).fetchall()
        # Fetch tags per row AFTER materializing rows: reusing one cursor for an outer
        # loop and inner queries silently truncates the outer result set (learned the
        # hard way in similar.py, where it dropped every Place but the first).
        out = []
        for row in rows:
            # NOT "top 8 by confidence". Two-class softmax heads saturate near 1.0, so
            # sorting globally returns `wet=1.00, atonal=1.00, danceable=1.00` for every
            # file while the labels that actually identify the sound — an AudioSet event
            # at 0.3, a genre at 0.4 — never surface. Confidences are only comparable
            # WITHIN a head, so take the best from each namespace instead, most
            # informative namespaces first.
            # Same two-signal test as the WHERE guard above, so the tags a caller SEES
            # match the tags they were allowed to search — a short file the listener
            # heard as a whole-bar loop keeps its genre in both places.
            short = _is_one_shot(row, include_unreliable)
            hide = f" AND NOT {_music_trained_sql('namespace')}" if short else ""
            # Within a namespace, an ontology parent is ranked BELOW anything
            # specific, however confident it is. Otherwise `Music 0.89` wins the
            # audio_event slot and `Drum machine 0.15` — the actual answer — is never
            # shown. Demoted rather than dropped: still returned if nothing else is.
            generic_sql = ", ".join("?" for _ in _GENERIC_EVENTS)
            tags = conn.execute(
                "SELECT namespace, label, confidence FROM ("   # noqa: S608
                "  SELECT namespace, label, ROUND(confidence, 3) AS confidence,"
                "         ROW_NUMBER() OVER (PARTITION BY namespace"
                f"                            ORDER BY (label IN ({generic_sql})),"
                "                                     confidence DESC) AS rank"
                f"  FROM tags WHERE file_id = ? AND confidence >= ?{hide}"
                ") WHERE rank = 1 "
                "ORDER BY CASE"
                "  WHEN namespace = 'audio_event' THEN 0"
                "  WHEN namespace LIKE '%instrument%' THEN 1"
                "  WHEN namespace LIKE '%genre%' OR namespace LIKE '%style%' THEN 2"
                "  WHEN namespace LIKE '%mood%' OR namespace LIKE '%theme%' THEN 3"
                "  ELSE 4 END, confidence DESC LIMIT 8",
                (*_GENERIC_EVENTS, row["id"], float(min_confidence),
                 *(_MUSIC_TRAINED if short else ()))).fetchall()
            entry = {
                "path": row["path"],
                "relevance": round(float(row["relevance"] or 0.0), 3),
                "duration_sec": row["duration_sec"],
                "bpm": row["bpm"],
                "key": (f"{row['key']} {row['scale']}".strip()
                        if row["key"] else None),
                "tags": [dict(t) for t in tags],
            }
            # Measured facts, when the sidecar recorded them. Omitted rather than sent
            # as nulls: which of these EXIST is itself information — a pitch means the
            # file was heard as one hit, a BPM means it was heard as a bar of them.
            measured = {k: row[k] for k in
                        ("kind", "bars", "pitch_midi", "pitch_hz", "attack_ms", "decay_ms",
                         "stereo_width", "stereo_correlation", "loudness_lufs")
                        if row[k] is not None}
            if measured.get("pitch_midi") is not None:
                measured["note"] = _note_name(measured["pitch_midi"])
            if measured:
                entry["measured"] = measured
            if query:
                # "heard" means a tag carried the word; "name" means only the path did.
                entry["matched_by"] = "+".join(
                    w for w, on in (("heard", row["matched_heard"]),
                                    ("name", row["matched_name"])) if on)
            if short:
                # Say so, rather than letting a caller wonder where the genre went.
                entry["one_shot"] = True
            preset = preset_for(row["path"])
            if preset:
                # The match is preview audio; this is the thing worth loading.
                entry["preset"] = preset
                entry["is_preview"] = True
            out.append(entry)

    result = {"source": "sidecar", "database": info["database"],
              "searched": info["files_analyzed"], "matches": len(out),
              "results": out}
    if query:
        # NEVER SILENT. A search that quietly rewrites the question is a search that
        # cannot be debugged — and the caller may have meant the word literally.
        used = {w: list(g[1:]) for w, g in zip(words, terms) if len(g) > 1}
        if used:
            result["interpreted"] = used
            result["note"] = (
                "Some words are not in the tag vocabulary and were widened to the "
                "nearest terms that are: "
                + "; ".join(f"{w} -> {', '.join(v)}" for w, v in used.items())
                + ". Filenames were still searched for the original words.")
    return result


def describe(path: str, db_path: str | None = None,
             include_unreliable: bool = False) -> dict:
    """Everything the listening module recorded about ONE file.

    Applies the SAME one-shot suppression as ``find``. It previously applied none, so
    the two disagreed about the same file in the same database: searching correctly
    hid a 0.1 s tom's `danceable 0.993` and `Electronic---Techno`, while inspecting
    that tom returned them with no caveat at all. A caller checking why a file did not
    match a search was shown exactly the verdicts the search had refused to believe.
    """
    info = status(db_path)
    if not info.get("available"):
        raise LookupError(info.get("reason", "sidecar unavailable"))
    with _connect(Path(info["database"])) as conn:
        row = conn.execute(
            "SELECT f.*, p.kind, p.bars, p.bpm, p.bpm_confidence, p.key, p.scale, "
            "p.key_strength, p.pitch_hz, p.pitch_midi, p.pitch_confidence, "
            "p.attack_ms, p.decay_ms, p.stereo_width, p.stereo_correlation, "
            "p.danceability, p.loudness_lufs FROM files f "
            "LEFT JOIN properties p ON p.file_id = f.id WHERE f.path = ?",
            (path,)).fetchone()
        if row is None:
            return {"found": False, "path": path,
                    "note": "not in the sidecar index — it may be outside the scanned "
                            "folders, or not analysed yet"}
        short = _is_one_shot(row, include_unreliable)
        hide = f" AND NOT {_music_trained_sql('namespace')}" if short else ""
        tags = conn.execute(
            "SELECT namespace, label, ROUND(confidence, 3) AS confidence, model "   # noqa: S608
            f"FROM tags WHERE file_id = ?{hide} ORDER BY namespace, confidence DESC",
            (row["id"], *(_MUSIC_TRAINED if short else ()))).fetchall()
    result = {k: row[k] for k in row.keys() if k != "id"}
    result["found"] = True
    result["tags"] = [dict(t) for t in tags]
    if short:
        result["one_shot"] = True
        result["note"] = ("shorter than 2.048 s and not corroborated as a loop, so "
                          "verdicts from heads trained on full music tracks (genre, "
                          "mood, style, danceability) are HIDDEN — they answer from "
                          "training priors at this length rather than from the sound. "
                          "Pass include_unreliable to see them anyway.")
    return result
