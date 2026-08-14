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


def find(query: str | None = None, genre: str | None = None, mood: str | None = None,
         instrument: str | None = None, min_confidence: float = 0.0,
         limit: int = 20, db_path: str | None = None) -> dict:
    """Search the sidecar's tags. Raises LookupError if it isn't available.

    The caller decides what to do about that — see ``mcp_server`` for the fallback,
    which lives at the tool layer so the policy is in one visible place.
    """
    info = status(db_path)
    if not info.get("available"):
        raise LookupError(info.get("reason", "sidecar unavailable"))

    wheres: list[str] = ["f.error IS NULL"]
    params: list = []
    wanted = [("genre", genre), ("mood", mood), ("instrument", instrument)]
    for namespace, value in wanted:
        if value:
            wheres.append(
                "EXISTS (SELECT 1 FROM tags t WHERE t.file_id = f.id "
                "AND t.namespace = ? AND t.label LIKE ? AND t.confidence >= ?)")
            params += [namespace, f"%{value}%", float(min_confidence)]
    if query:
        wheres.append("f.path LIKE ?")
        params.append(f"%{query}%")

    sql = (f"SELECT f.id, f.path, f.duration_sec, p.bpm, p.key, p.scale "  # noqa: S608
           f"FROM files f LEFT JOIN properties p ON p.file_id = f.id "
           f"WHERE {' AND '.join(wheres)} LIMIT ?")
    params.append(int(limit))

    with _connect(Path(info["database"])) as conn:
        rows = conn.execute(sql, params).fetchall()
        # Fetch tags per row AFTER materializing rows: reusing one cursor for an outer
        # loop and inner queries silently truncates the outer result set (learned the
        # hard way in similar.py, where it dropped every Place but the first).
        out = []
        for row in rows:
            tags = conn.execute(
                "SELECT namespace, label, ROUND(confidence, 3) AS confidence FROM tags "
                "WHERE file_id = ? AND confidence >= ? "
                "ORDER BY confidence DESC LIMIT 8",
                (row["id"], float(min_confidence))).fetchall()
            out.append({
                "path": row["path"],
                "duration_sec": row["duration_sec"],
                "bpm": row["bpm"],
                "key": (f"{row['key']} {row['scale']}".strip()
                        if row["key"] else None),
                "tags": [dict(t) for t in tags],
            })

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
