"""SQLite storage: catalogue, per-page text (FTS5), TOC, reading progress."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

# Bump when the catalogue shape changes. A mismatch rebuilds the catalogue
# (cheap - a full reindex is ~85 s) but never touches reading progress.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id            TEXT PRIMARY KEY,
    library       TEXT NOT NULL,
    path          TEXT NOT NULL UNIQUE,
    rel_path      TEXT NOT NULL DEFAULT '',
    group_id      TEXT NOT NULL,
    group_name    TEXT NOT NULL,
    group_order   INTEGER NOT NULL DEFAULT 100,
    group_path    TEXT NOT NULL DEFAULT '',
    number        INTEGER,
    volume        INTEGER,
    title         TEXT NOT NULL,
    label         TEXT NOT NULL,
    pages         INTEGER NOT NULL DEFAULT 0,
    file_size     INTEGER NOT NULL DEFAULT 0,
    mtime         REAL NOT NULL DEFAULT 0,
    page_w        INTEGER NOT NULL DEFAULT 0,
    page_h        INTEGER NOT NULL DEFAULT 0,
    has_text      INTEGER NOT NULL DEFAULT 0,
    text_pages    INTEGER NOT NULL DEFAULT 0,
    toc_count     INTEGER NOT NULL DEFAULT 0,
    indexed_at    REAL,
    sort_a        INTEGER NOT NULL DEFAULT 0,
    sort_b        INTEGER NOT NULL DEFAULT 0,
    sort_text     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS docs_browse
    ON docs(library, group_order, group_name, sort_a, sort_b, sort_text);
CREATE INDEX IF NOT EXISTS docs_group ON docs(library, group_id);

CREATE TABLE IF NOT EXISTS toc (
    doc_id  TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    idx     INTEGER NOT NULL,
    level   INTEGER NOT NULL,
    title   TEXT NOT NULL,
    page    INTEGER NOT NULL,
    PRIMARY KEY (doc_id, idx)
);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    doc_id UNINDEXED,
    page   UNINDEXED,
    body,
    tokenize = 'porter unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS toc_fts USING fts5(
    doc_id UNINDEXED,
    page   UNINDEXED,
    title,
    tokenize = 'porter unicode61 remove_diacritics 2'
);

-- Progress is keyed by doc id but also records the file path, so a document
-- that is re-identified (renamed library, changed id scheme) can keep its
-- bookmark instead of silently losing the reader's place.
CREATE TABLE IF NOT EXISTS progress (
    doc_id     TEXT PRIMARY KEY,
    path       TEXT NOT NULL DEFAULT '',
    page       INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    finished   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS progress_path ON progress(path);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    db = path or settings.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{db}?mode=ro" if readonly else f"file:{db}"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def init(conn: sqlite3.Connection) -> None:
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='docs'").fetchone()

    # Startup must not take a write lock when there is nothing to do: an
    # indexer run holds one for minutes, and the web server would otherwise
    # block behind it (or time out) just to re-assert a schema that is
    # already current.
    if existing and _version(conn) == SCHEMA_VERSION:
        return

    if existing:
        _migrate(conn)

    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                 (str(SCHEMA_VERSION),))
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Rebuild the catalogue, carrying reading progress across by file path."""
    saved: list[tuple] = []
    cols = {r[1] for r in conn.execute("PRAGMA table_info(progress)")}
    if cols:
        if "path" in cols:
            saved = conn.execute(
                "SELECT doc_id, path, page, updated_at, finished FROM progress").fetchall()
        else:
            # Older progress rows have no path; recover it from the docs table
            # so bookmarks survive the id scheme changing.
            saved = conn.execute(
                """SELECT p.doc_id, COALESCE(d.path, ''), p.page, p.updated_at, p.finished
                   FROM progress p LEFT JOIN docs d ON d.id = p.doc_id""").fetchall()

    for stmt in ("DROP TABLE IF EXISTS toc",
                 "DROP TABLE IF EXISTS pages_fts",
                 "DROP TABLE IF EXISTS toc_fts",
                 "DROP TABLE IF EXISTS docs",
                 "DROP TABLE IF EXISTS progress"):
        conn.execute(stmt)
    conn.executescript(SCHEMA)

    if saved:
        conn.executemany(
            "INSERT OR REPLACE INTO progress (doc_id, path, page, updated_at, finished)"
            " VALUES (?,?,?,?,?)", saved)
    conn.commit()


def relink_progress(conn: sqlite3.Connection) -> int:
    """Re-point saved bookmarks at whatever id now owns that file."""
    rows = conn.execute(
        """SELECT p.doc_id AS old_id, d.id AS new_id
           FROM progress p JOIN docs d ON d.path = p.path
           WHERE p.path <> '' AND p.doc_id <> d.id""").fetchall()
    for row in rows:
        conn.execute("DELETE FROM progress WHERE doc_id = ?", (row["new_id"],))
        conn.execute("UPDATE progress SET doc_id = ? WHERE doc_id = ?",
                     (row["new_id"], row["old_id"]))
    # Drop bookmarks whose file is gone from every library.
    conn.execute("DELETE FROM progress WHERE doc_id NOT IN (SELECT id FROM docs)")
    conn.commit()
    return len(rows)


@contextmanager
def session(**kw) -> Iterator[sqlite3.Connection]:
    conn = connect(**kw)
    try:
        yield conn
    finally:
        conn.close()
