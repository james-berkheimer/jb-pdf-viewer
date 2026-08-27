"""Full-text search across the collection.

Two indexes are queried: page bodies (OCR text) and TOC entries (real article
titles). TOC hits are ranked above body hits because on a magazine an article
title match is almost always what the reader meant.
"""
from __future__ import annotations

import re
import sqlite3

_TOKEN = re.compile(r'"[^"]*"|\S+')
_SAFE = re.compile(r"[^\w\s*-]", re.UNICODE)


def build_match(query: str) -> str:
    """Turn free-form user input into a valid FTS5 MATCH expression.

    Users type apostrophes, hyphens and stray punctuation that FTS5 treats as
    syntax; anything not explicitly quoted gets escaped into a phrase so a
    query can never raise a syntax error back at the reader.
    """
    parts: list[str] = []
    for raw in _TOKEN.findall(query or ""):
        if raw.startswith('"') and raw.endswith('"') and len(raw) > 1:
            inner = raw[1:-1].replace('"', "")
            if inner.strip():
                parts.append(f'"{inner.strip()}"')
            continue
        trailing_star = raw.endswith("*")
        cleaned = _SAFE.sub(" ", raw).replace("*", " ").strip()
        if not cleaned:
            continue
        parts.append(f'"{cleaned}"' + ("*" if trailing_star else ""))
    return " ".join(parts)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    library: str | None = None,
    group: str | None = None,
    doc_id: str | None = None,
    limit: int = 60,
    offset: int = 0,
) -> dict:
    match = build_match(query)
    if not match:
        return {"query": query, "total": 0, "hits": [], "truncated": False}

    filters, params = [], []
    if library:
        filters.append("d.library = ?")
        params.append(library)
    if group:
        filters.append("d.group_id = ?")
        params.append(group)
    if doc_id:
        filters.append("d.id = ?")
        params.append(doc_id)
    where = (" AND " + " AND ".join(filters)) if filters else ""

    # 1.0 weights body rank; TOC rank is offset so titles win ties.
    sql = f"""
    WITH body AS (
        SELECT f.doc_id, f.page AS page, 'page' AS kind,
               snippet(pages_fts, 2, '<mark>', '</mark>', '…', 18) AS excerpt,
               bm25(pages_fts, 0, 0, 1.0) AS rank
        FROM pages_fts f JOIN docs d ON d.id = f.doc_id
        WHERE pages_fts MATCH ?{where}
        ORDER BY rank LIMIT 3000
    ),
    titles AS (
        SELECT t.doc_id, t.page AS page, 'article' AS kind,
               snippet(toc_fts, 2, '<mark>', '</mark>', '…', 18) AS excerpt,
               bm25(toc_fts, 0, 0, 1.0) - 100.0 AS rank
        FROM toc_fts t JOIN docs d ON d.id = t.doc_id
        WHERE toc_fts MATCH ?{where}
        ORDER BY rank LIMIT 1000
    ),
    merged AS (SELECT * FROM body UNION ALL SELECT * FROM titles)
    SELECT m.doc_id, m.page, m.kind, m.excerpt, m.rank,
           d.title, d.label, d.library, d.group_id, d.group_name, d.pages
    FROM merged m JOIN docs d ON d.id = m.doc_id
    ORDER BY m.rank ASC, d.group_order, d.sort_a, m.page
    LIMIT ? OFFSET ?
    """
    rows = conn.execute(
        sql, [match, *params, match, *params, limit + 1, offset]
    ).fetchall()

    truncated = len(rows) > limit
    hits = [dict(r) for r in rows[:limit]]

    counts = conn.execute(
        f"""SELECT count(*) FROM (
              SELECT 1 FROM pages_fts f JOIN docs d ON d.id=f.doc_id
              WHERE pages_fts MATCH ?{where}
              UNION ALL
              SELECT 1 FROM toc_fts t JOIN docs d ON d.id=t.doc_id
              WHERE toc_fts MATCH ?{where})""",
        [match, *params, match, *params],
    ).fetchone()[0]

    # How many distinct issues the hits span - useful context on the results page.
    docs = len({h["doc_id"] for h in hits})
    return {"query": query, "match": match, "total": counts, "docs": docs,
            "hits": hits, "truncated": truncated}
