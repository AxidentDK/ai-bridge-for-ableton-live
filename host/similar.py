"""Similar-sound search over Live 12's own audio embeddings.

Live 12's Sound Similarity Search analyses audio in the background and stores, for
every analysed file, a **64-dimensional embedding** from a trained model — the one
piece of real audio *understanding* on the machine. It is not in the Live Object
Model, so the bridge cannot ask Live for it. It is, however, a plain SQLite file,
and reading it needs nothing beyond the standard library.

That makes this the cheapest capability in the project by a wide margin: no model
to download, no dependency, no inference cost, and the index is maintained by Live
itself and stays current as the library grows.

**Format (verified directly against the database, 2026-08-14).** Table
``fe_values(file_id, data, hash)``; each blob is 12 bytes of header —
``(fe_version, dims, 0)`` little-endian int32, observed ``(18, 64, 0)`` — followed
by ``dims`` little-endian float32. Every row was exactly 268 bytes. Because this is
reverse-engineered rather than documented, the header is CHECKED on every read and
a change in ``fe_version`` or ``dims`` is reported as an error rather than being
silently decoded into nonsense.

**Always opened read-only** (``mode=ro``): Live owns this file and is usually
running.

Credit: the sister project (MIT-licensed ableton-live-mcp fork) demonstrated that
this database is usable for similarity search. This implementation is our own,
written against the format as verified here.
"""
from __future__ import annotations

import glob
import math
import os
import sqlite3
import struct

_HEADER = struct.Struct("<3i")
EXPECTED_VERSION = 18
EXPECTED_DIMS = 64


class SimilarError(RuntimeError):
    pass


def default_database() -> str | None:
    """Newest ``Live-files-*.db`` in Live's database folder, if present."""
    roots = []
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            roots.append(os.path.join(base, "Ableton", "Live Database"))
    roots.append(os.path.expanduser("~/Library/Application Support/Ableton/Live Database"))
    found: list[str] = []
    for root in roots:
        found += glob.glob(os.path.join(root, "Live-files-*.db"))
    if not found:
        return None
    return max(found, key=os.path.getmtime)


def _connect(db_path: str | None) -> tuple[sqlite3.Connection, str]:
    path = db_path or default_database()
    if not path or not os.path.isfile(path):
        raise SimilarError(
            "no Live database found. Live 12+ writes it to "
            "%LOCALAPPDATA%/Ableton/Live Database/Live-files-*.db once it has "
            "analysed some audio; pass db_path explicitly if it lives elsewhere.")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True), path


def _decode(blob: bytes) -> list[float]:
    if len(blob) < _HEADER.size:
        raise SimilarError("embedding blob too short (%d bytes)" % len(blob))
    version, dims, _ = _HEADER.unpack_from(blob, 0)
    if version != EXPECTED_VERSION or dims != EXPECTED_DIMS:
        raise SimilarError(
            "unexpected embedding format (fe_version=%d dims=%d, expected %d/%d). "
            "Live's analysis format has probably changed in an update — the decoder "
            "needs revisiting rather than trusting these numbers."
            % (version, dims, EXPECTED_VERSION, EXPECTED_DIMS))
    need = _HEADER.size + dims * 4
    if len(blob) < need:
        raise SimilarError("embedding truncated: %d bytes, expected %d" % (len(blob), need))
    return list(struct.unpack_from("<%df" % dims, blob, _HEADER.size))


def _catalogue(con: sqlite3.Connection) -> dict:
    """file_id -> {name, place, path} for every analysed file."""
    cur = con.cursor()
    places = {pid: name for pid, name in cur.execute("select file_id, name from places")}
    rows = cur.execute(
        "select f.file_id, f.name, f.parent_id, f.place_id from files f "
        "join fe_values v on v.file_id = f.file_id").fetchall()
    parents = {fid: (name, parent) for fid, name, parent, _ in
               cur.execute("select file_id, name, parent_id, place_id from files")}

    out = {}
    for fid, name, parent, place_id in rows:
        parts, walk, guard = [], parent, 0
        while walk and guard < 64:                    # guard: never trust a cycle
            entry = parents.get(walk)
            if not entry:
                break
            parts.append(entry[0])
            walk = entry[1]
            guard += 1
        out[fid] = {"name": name,
                    "place": places.get(place_id),
                    "folder": "/".join(reversed([p for p in parts if p]))}
    return out


def _vectors(con: sqlite3.Connection) -> tuple[dict, dict]:
    vecs, hashes = {}, {}
    for fid, data, digest in con.execute("select file_id, data, hash from fe_values"):
        try:
            vecs[fid] = _decode(data)
        except SimilarError:
            raise
        hashes[fid] = digest
    return vecs, hashes


def index_status(db_path: str | None = None) -> dict:
    """What Live has analysed, per Place.

    Worth surfacing: Live only analyses folders added as **Places**, so anything
    outside them is invisible to similarity search. Comparing analysed vs total
    per Place shows exactly what is missing.
    """
    con, path = _connect(db_path)
    try:
        cur = con.cursor()
        total, = cur.execute("select count(*) from fe_values").fetchone()
        # fetchall() FIRST: running another query on the same cursor invalidates the
        # outer result set, so iterating it lazily silently drops every row after the
        # first (caught in testing — only one Place was ever reported).
        places = cur.execute("select file_id, name from places").fetchall()
        rows = []
        for pid, name in places:
            files, = cur.execute("select count(*) from files where place_id=?", (pid,)).fetchone()
            done, = cur.execute(
                "select count(*) from fe_values v join files f on f.file_id=v.file_id "
                "where f.place_id=?", (pid,)).fetchone()
            if files:
                rows.append({"place": name, "files": files, "analysed": done})
        rows.sort(key=lambda r: -r["analysed"])
        sample = cur.execute("select data from fe_values limit 1").fetchone()
        version, dims, _ = _HEADER.unpack_from(sample[0], 0) if sample else (None, None, None)
        return {"database": path, "analysed_total": total, "dims": dims,
                "fe_version": version, "places": rows,
                "note": ("Live analyses only folders added as Places, and uses just the "
                         "first ~2 seconds of each file. Add a folder as a Place in "
                         "Live's browser to have it indexed.")}
    finally:
        con.close()


def find_similar(query: str | None = None, path: str | None = None,
                 limit: int = 10, db_path: str | None = None,
                 include_self: bool = False) -> dict:
    """Find sounds similar to a base file, using Live's own embeddings.

    Pick the base by ``query`` (case-insensitive substring of the filename, the
    shortest match wins so "kick" prefers "Kick.wav" over "Kickdrum Loop 12.wav")
    or by ``path`` (substring of the full path, for when names collide).

    Distance is Euclidean in the 64-d space; smaller is more similar. Results are
    deduplicated by the content ``hash``, so the same sample sitting in two folders
    is reported once.
    """
    con, resolved = _connect(db_path)
    try:
        vecs, hashes = _vectors(con)
        if not vecs:
            raise SimilarError("the Live database contains no analysed audio yet")
        catalogue = _catalogue(con)
    finally:
        con.close()

    def label(fid):
        info = catalogue.get(fid, {})
        return info.get("name") or f"file {fid}"

    base_id = None
    if path:
        needle = str(path).lower().replace("\\", "/")
        hits = [fid for fid in vecs
                if needle in f"{catalogue.get(fid, {}).get('folder', '')}/{label(fid)}".lower()]
        base_id = min(hits, key=lambda f: len(label(f))) if hits else None
    elif query:
        needle = str(query).lower()
        hits = [fid for fid in vecs if needle in str(label(fid)).lower()]
        base_id = min(hits, key=lambda f: len(str(label(f)))) if hits else None
    else:
        raise SimilarError("give either query (filename substring) or path")

    if base_id is None:
        raise SimilarError(
            "no analysed file matches %r. It may exist but not be analysed — Live only "
            "analyses folders added as Places (see live_sound_index)."
            % (path or query))

    base_vec = vecs[base_id]
    scored = []
    seen = {hashes.get(base_id)} if not include_self else set()
    for fid, vec in vecs.items():
        if fid == base_id and not include_self:
            continue
        digest = hashes.get(fid)
        if digest and digest in seen:
            continue
        if digest:
            seen.add(digest)
        scored.append((math.dist(base_vec, vec), fid))
    scored.sort()

    def row(distance, fid):
        info = catalogue.get(fid, {})
        return {"name": info.get("name"), "place": info.get("place"),
                "folder": info.get("folder"), "distance": round(distance, 4)}

    return {"database": resolved,
            "base": dict(row(0.0, base_id), distance=0.0),
            "searched": len(vecs),
            "results": [row(d, f) for d, f in scored[:max(1, int(limit))]],
            "note": ("Distance is Euclidean in Live's own 64-d embedding space — "
                     "smaller is more similar. Live analyses only the first ~2 s of "
                     "each file, so long evolving sounds are matched on their opening.")}
