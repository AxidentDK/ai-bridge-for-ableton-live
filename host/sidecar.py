"""The listening sidecar's database — READ side, and the authoritative schema.

A separate process (Essentia under WSL2 — it publishes no Windows wheels) listens to
the sample library and writes what it heard into SQLite. The bridge never imports
Essentia, never loads a model and never writes here: it *reads a file*, which is the
only way a near-zero-dependency bridge can benefit from a heavyweight model.

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
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ENV_VAR = "AI_BRIDGE_SOUND_DB"
DB_FILENAME = "sound_index.db"
REPO_DB = REPO_ROOT / DB_FILENAME
HOME_DB = Path.home() / ".ai-bridge" / DB_FILENAME

# Bumped only on a BREAKING change. The bridge refuses a major it doesn't know rather
# than guessing at columns, because a wrong guess here is a plausible wrong answer.
SCHEMA_VERSION = 1

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
    -- The sidecar runs under WSL and sees /mnt/d/Packs/kick.wav; translating is the
    -- SIDECAR's job, because it is the only side that knows both spellings.
    path          TEXT NOT NULL UNIQUE,
    source_path   TEXT,               -- the /mnt/... spelling, kept for provenance
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
CREATE TABLE IF NOT EXISTS properties (
    file_id        INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    bpm            REAL,
    bpm_confidence REAL,
    key            TEXT,              -- 'F#'
    scale          TEXT,              -- 'major' | 'minor'
    key_strength   REAL,
    danceability   REAL,
    loudness_lufs  REAL
);

CREATE INDEX IF NOT EXISTS idx_tags_lookup ON tags(namespace, label, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_files_path  ON files(path);
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


def preset_for(path: str) -> str | None:
    """The loadable preset a plugin PREVIEW demonstrates, or None.

    Nearly half a real index turned out to be NKS preview audio rather than samples —
    short demos a plugin ships so its browser can audition presets without loading
    them. That is not noise: it means the presets themselves become searchable by
    sound. But a caller wants the preset, not the demo, and the two are not in the
    same folder:

        preview  .../presets/.previews/Abyss.nksf.ogg
        preset   .../presets/Abyss.nksf

    Derived rather than stored, so it works on everything already indexed without a
    re-scan or a schema change.
    """
    lower = path.lower()
    if not lower.endswith(".ogg") or f"{os.sep}.previews{os.sep}" not in lower:
        return None
    previews_dir, filename = os.path.split(path)
    candidate = os.path.join(os.path.dirname(previews_dir), filename[:-4])
    return candidate if os.path.exists(candidate) else None


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


def _music_trained_sql(column: str = "t.namespace") -> str:
    return "(" + " OR ".join(f"{column} LIKE ?" for _ in _MUSIC_TRAINED) + ")"


def find(query: str | None = None, genre: str | None = None, mood: str | None = None,
         instrument: str | None = None, min_confidence: float = 0.0,
         limit: int = 20, db_path: str | None = None,
         event: str | None = None, tag: str | None = None,
         include_unreliable: bool = False) -> dict:
    """Search the sidecar's tags. Raises LookupError if it isn't available.

    The caller decides what to do about that — see ``mcp_server`` for the fallback,
    which lives at the tool layer so the policy is in one visible place.
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
    guard = ("" if include_unreliable else
             " AND (f.duration_sec IS NULL OR f.duration_sec >= ? "
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
        wheres.append("f.path LIKE ?")
        params.append(f"%{query}%")

    # No criterion carries a confidence (filename-only search) -> nothing to rank by,
    # so fall back to a stable alphabetical order rather than insertion order.
    relevance = " + ".join(score_parts) if score_parts else "0"
    order = "relevance DESC, f.path" if score_parts else "f.path"
    sql = (f"SELECT f.id, f.path, f.duration_sec, p.bpm, p.key, p.scale, "  # noqa: S608
           f"({relevance}) AS relevance "
           f"FROM files f LEFT JOIN properties p ON p.file_id = f.id "
           f"WHERE {' AND '.join(wheres)} ORDER BY {order} LIMIT ?")
    # SELECT parameters bind before WHERE parameters.
    all_params = [*score_params, *params, int(limit)]

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
            short = (not include_unreliable and row["duration_sec"] is not None
                     and row["duration_sec"] < ONE_SHOT_SECONDS)
            hide = f" AND NOT {_music_trained_sql('namespace')}" if short else ""
            tags = conn.execute(
                "SELECT namespace, label, confidence FROM ("   # noqa: S608
                "  SELECT namespace, label, ROUND(confidence, 3) AS confidence,"
                "         ROW_NUMBER() OVER (PARTITION BY namespace"
                "                            ORDER BY confidence DESC) AS rank"
                f"  FROM tags WHERE file_id = ? AND confidence >= ?{hide}"
                ") WHERE rank = 1 "
                "ORDER BY CASE"
                "  WHEN namespace = 'audio_event' THEN 0"
                "  WHEN namespace LIKE '%instrument%' THEN 1"
                "  WHEN namespace LIKE '%genre%' OR namespace LIKE '%style%' THEN 2"
                "  WHEN namespace LIKE '%mood%' OR namespace LIKE '%theme%' THEN 3"
                "  ELSE 4 END, confidence DESC LIMIT 8",
                (row["id"], float(min_confidence),
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
            if short:
                # Say so, rather than letting a caller wonder where the genre went.
                entry["one_shot"] = True
            preset = preset_for(row["path"])
            if preset:
                # The match is preview audio; this is the thing worth loading.
                entry["preset"] = preset
                entry["is_preview"] = True
            out.append(entry)

    return {"source": "sidecar", "database": info["database"],
            "searched": info["files_analyzed"], "matches": len(out), "results": out}


def describe(path: str, db_path: str | None = None) -> dict:
    """Everything the listening module recorded about ONE file."""
    info = status(db_path)
    if not info.get("available"):
        raise LookupError(info.get("reason", "sidecar unavailable"))
    with _connect(Path(info["database"])) as conn:
        row = conn.execute(
            "SELECT f.*, p.bpm, p.bpm_confidence, p.key, p.scale, p.key_strength, "
            "p.danceability, p.loudness_lufs FROM files f "
            "LEFT JOIN properties p ON p.file_id = f.id WHERE f.path = ?",
            (path,)).fetchone()
        if row is None:
            return {"found": False, "path": path,
                    "note": "not in the sidecar index — it may be outside the scanned "
                            "folders, or not analysed yet"}
        tags = conn.execute(
            "SELECT namespace, label, ROUND(confidence, 3) AS confidence, model "
            "FROM tags WHERE file_id = ? ORDER BY namespace, confidence DESC",
            (row["id"],)).fetchall()
    result = {k: row[k] for k in row.keys() if k != "id"}
    result["found"] = True
    result["tags"] = [dict(t) for t in tags]
    return result
