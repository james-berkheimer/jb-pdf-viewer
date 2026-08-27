"""Walk each configured library, extract text/TOC/covers into SQLite.

PDF work runs in a process pool; SQLite writes stay in the parent, which is
the only writer.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Iterator

from . import db, libraries
from .config import Settings, settings
from .libraries import Library
from .naming import ParsedName, looks_like_magazine, parse

log = logging.getLogger("indexer")

# A page with less than this many characters is treated as un-OCR'd artwork
# rather than real text, so an issue is not reported as searchable when all we
# have is a stray page number in the gutter.
MIN_PAGE_CHARS = 120

SKIP_DIRS = {".git", ".svn", "@eaDir", "#recycle", ".Trash", "__MACOSX"}

# SQLite allows a single writer, so a scan that held one transaction for its
# whole run would block the reader from saving a bookmark for minutes. Commit
# in batches instead.
COMMIT_EVERY = 250


def discover(root: Path) -> Iterator[Path]:
    if not root.exists():
        log.warning("library root missing: %s", root)
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or not name.lower().endswith(".pdf"):
                continue
            yield Path(dirpath) / name


def resolve_profile(lib: Library, paths: list[Path]) -> str:
    if lib.profile != "auto":
        return lib.profile
    return "magazine" if looks_like_magazine(paths) else "folders"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def candidate_id(lib: Library, parsed: ParsedName, rel: Path) -> str:
    """Readable id derived from the library and the file's place in it.

    Stable across reindexes as long as the file does not move, which is what
    lets reading progress survive.
    """
    if parsed.number is not None:
        stem = f"{parsed.group}-{parsed.volume}-{parsed.number}" \
            if parsed.volume is not None else f"{parsed.group}-{parsed.number}"
    else:
        stem = str(rel.with_suffix(""))
    base = _slugify(f"{lib.id}-{stem}") or lib.id
    return base[:96].rstrip("-")


def assign_ids(lib: Library, items: list[tuple[Path, Path, ParsedName]]) -> dict[Path, str]:
    """Give every file a unique id, disambiguating only where names collide."""
    taken: dict[str, list[tuple[Path, Path]]] = {}
    for path, rel, parsed in items:
        taken.setdefault(candidate_id(lib, parsed, rel), []).append((path, rel))

    ids: dict[Path, str] = {}
    for base, group in taken.items():
        if len(group) == 1:
            ids[group[0][0]] = base
            continue
        for path, rel in group:
            digest = hashlib.sha1(str(rel).encode("utf-8")).hexdigest()[:6]
            ids[path] = f"{base}-{digest}"
    return ids


def _extract(path_str: str, cover_path: str, cover_w: int, quality: int, method: int):
    """Worker: open one PDF and pull out everything the app needs."""
    import pymupdf
    from PIL import Image

    out = {"path": path_str, "error": None}
    try:
        doc = pymupdf.open(path_str)
        out["pages"] = doc.page_count
        rect = doc[0].rect if doc.page_count else None
        out["page_w"] = round(rect.width) if rect else 0
        out["page_h"] = round(rect.height) if rect else 0

        texts: list[tuple[int, str]] = []
        text_pages = 0
        for i in range(doc.page_count):
            try:
                body = doc[i].get_text("text").strip()
            except Exception:
                body = ""
            if len(body) >= MIN_PAGE_CHARS:
                text_pages += 1
                texts.append((i + 1, " ".join(body.split())))
        out["texts"] = texts
        out["text_pages"] = text_pages

        toc = []
        try:
            for idx, (level, title, page) in enumerate(doc.get_toc()):
                title = " ".join(str(title).split())
                if title and 1 <= page <= doc.page_count:
                    toc.append((idx, int(level), title, int(page)))
        except Exception:
            pass
        out["toc"] = toc

        cov = Path(cover_path)
        if doc.page_count and not cov.exists():
            page = doc[0]
            zoom = cover_w / page.rect.width
            pm = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            img = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
            cov.parent.mkdir(parents=True, exist_ok=True)
            tmp = cov.with_suffix(f".{os.getpid()}.tmp")
            img.save(tmp, "WEBP", quality=quality, method=method)
            os.replace(tmp, cov)
        doc.close()
    except Exception as exc:  # a broken PDF must not sink the whole run
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _needs_reindex(conn: sqlite3.Connection, doc_id: str, path: Path) -> bool:
    row = conn.execute(
        "SELECT file_size, mtime, indexed_at FROM docs WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None or row["indexed_at"] is None:
        return True
    try:
        st = path.stat()
    except OSError:
        return False
    return int(row["file_size"]) != st.st_size or abs(row["mtime"] - st.st_mtime) > 1


def reindex(
    cfg: Settings = settings,
    *,
    force: bool = False,
    limit: int | None = None,
    only: str | None = None,
    progress=None,
) -> dict:
    cfg.ensure_dirs()
    conn = db.connect()
    db.init(conn)

    lib_cfg = libraries.load()
    libs = lib_cfg.enabled()
    if only:
        libs = [l for l in libs if l.id == only]
        if not libs:
            raise libraries.LibraryError(f"no enabled library with id {only!r}")

    stats = {"libraries": [], "total": 0, "indexed": 0, "removed": 0,
             "errors": [], "pages": 0, "started": time.time()}
    todo: list[tuple[str, Path]] = []
    known: set[str] = set()

    for lib in libs:
        paths = list(discover(lib.path))
        profile = resolve_profile(lib, paths)
        items = []
        for path in paths:
            try:
                rel = path.resolve().relative_to(lib.path.resolve())
            except ValueError:
                rel = Path(path.name)
            items.append((path, rel, parse(path, profile, lib.path)))

        ids = assign_ids(lib, items)
        stats["libraries"].append(
            {"id": lib.id, "name": lib.name, "profile": profile, "files": len(items)})

        for n, (path, rel, parsed) in enumerate(items, 1):
            doc_id = ids[path]
            known.add(doc_id)
            try:
                st = path.stat()
            except OSError:
                continue
            if n % COMMIT_EVERY == 0:
                conn.commit()
            conn.execute(
                """INSERT INTO docs (id, library, path, rel_path, group_id, group_name,
                                     group_order, group_path, number, volume, title,
                                     label, file_size, mtime, sort_a, sort_b, sort_text)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     library=excluded.library, path=excluded.path,
                     rel_path=excluded.rel_path, group_id=excluded.group_id,
                     group_name=excluded.group_name, group_order=excluded.group_order,
                     group_path=excluded.group_path, number=excluded.number,
                     volume=excluded.volume, title=excluded.title, label=excluded.label,
                     file_size=excluded.file_size, mtime=excluded.mtime,
                     sort_a=excluded.sort_a, sort_b=excluded.sort_b,
                     sort_text=excluded.sort_text""",
                (doc_id, lib.id, str(path), str(rel), parsed.group, parsed.group_name,
                 parsed.group_order, parsed.group_path, parsed.number, parsed.volume,
                 parsed.title, parsed.label, st.st_size, st.st_mtime,
                 parsed.sort_a, parsed.sort_b, parsed.sort_text),
            )
            if force or _needs_reindex(conn, doc_id, path):
                todo.append((doc_id, path))
    conn.commit()
    stats["total"] = len(known)

    # Drop catalogue rows for files that vanished, but only inside the
    # libraries we actually scanned this run.
    scanned = {l.id for l in libs}
    for row in list(conn.execute("SELECT id, library FROM docs")):
        if row["library"] in scanned and row["id"] not in known:
            _purge(conn, row["id"])
            conn.execute("DELETE FROM docs WHERE id = ?", (row["id"],))
            stats["removed"] += 1
        elif row["library"] not in {l.id for l in lib_cfg.libraries}:
            _purge(conn, row["id"])
            conn.execute("DELETE FROM docs WHERE id = ?", (row["id"],))
            stats["removed"] += 1
    conn.commit()

    if limit:
        todo = todo[:limit]

    if todo:
        jobs = {}
        with ProcessPoolExecutor(max_workers=cfg.render_workers) as pool:
            for doc_id, path in todo:
                cover = cfg.cover_dir / f"{doc_id}.webp"
                if force and cover.exists():
                    cover.unlink()
                jobs[pool.submit(_extract, str(path), str(cover), cfg.cover_width,
                                 cfg.webp_quality, cfg.webp_method)] = doc_id

            for done, fut in enumerate(as_completed(jobs), 1):
                doc_id = jobs[fut]
                try:
                    res = fut.result()
                except Exception as exc:
                    stats["errors"].append((doc_id, f"worker crashed: {exc}"))
                    continue
                if res["error"]:
                    stats["errors"].append((doc_id, res["error"]))
                    continue
                _store(conn, doc_id, res)
                stats["indexed"] += 1
                stats["pages"] += res["pages"]
                if stats["indexed"] % 25 == 0:
                    conn.commit()
                if progress:
                    progress(done, len(jobs), doc_id)
            conn.commit()

    stats["relinked"] = db.relink_progress(conn)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_index', ?)",
                 (str(time.time()),))
    conn.commit()
    conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('optimize')")
    conn.execute("INSERT INTO toc_fts(toc_fts) VALUES('optimize')")
    conn.commit()
    conn.close()
    stats["elapsed"] = time.time() - stats["started"]
    return stats


def _purge(conn: sqlite3.Connection, doc_id: str) -> None:
    conn.execute("DELETE FROM pages_fts WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM toc_fts WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM toc WHERE doc_id = ?", (doc_id,))


def _store(conn: sqlite3.Connection, doc_id: str, res: dict) -> None:
    _purge(conn, doc_id)
    conn.executemany(
        "INSERT INTO pages_fts (doc_id, page, body) VALUES (?,?,?)",
        ((doc_id, pg, body) for pg, body in res["texts"]),
    )
    conn.executemany(
        "INSERT INTO toc (doc_id, idx, level, title, page) VALUES (?,?,?,?,?)",
        ((doc_id, i, lvl, t, pg) for i, lvl, t, pg in res["toc"]),
    )
    conn.executemany(
        "INSERT INTO toc_fts (doc_id, page, title) VALUES (?,?,?)",
        ((doc_id, pg, t) for _, _, t, pg in res["toc"]),
    )
    conn.execute(
        """UPDATE docs SET pages=?, page_w=?, page_h=?, has_text=?, text_pages=?,
                           toc_count=?, indexed_at=? WHERE id=?""",
        (res["pages"], res["page_w"], res["page_h"],
         1 if res["text_pages"] else 0, res["text_pages"],
         len(res["toc"]), time.time(), doc_id),
    )
