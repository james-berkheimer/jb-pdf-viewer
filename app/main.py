"""FastAPI application: library, reader, rendering and search endpoints."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pymupdf
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, jobs, libraries, search as search_mod
from .config import settings
from .render import renderer

log = logging.getLogger("pdfv")
WEB = Path(__file__).resolve().parent.parent / "web"

# Rendered pages are immutable for a given (doc, page, width): the only way
# the bytes change is a source PDF edit, which changes the ETag via mtime.
IMMUTABLE = "public, max-age=31536000, immutable"

# The app shell is not. Without an explicit Cache-Control a browser falls back
# to heuristic freshness and will happily serve a stale page or script for
# hours without ever asking us, which looks exactly like "the fix didn't work".
# "no-cache" means revalidate before use, not "do not store" - paired with an
# ETag that is a cheap 304.
REVALIDATE = "no-cache, must-revalidate"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    conn = db.connect()
    db.init(conn)
    app.state.db = conn
    log.info("library db ready at %s", settings.db_path)
    yield
    renderer.shutdown()
    conn.close()


app = FastAPI(title="jb-pdf-viewer", lifespan=lifespan, docs_url="/api/docs")


def get_db(request: Request):
    return request.app.state.db


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _doc_or_404(conn, doc_id: str):
    row = conn.execute("SELECT * FROM docs WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"unknown document: {doc_id}")
    return row


def _pdf_path(row) -> Path:
    p = Path(row["path"])
    if not p.exists():
        raise HTTPException(410, "source PDF is no longer on disk")
    return p


async def _render(row, doc_id: str, page: int, width: int):
    """Render a page, turning renderer failures into honest HTTP errors.

    A handful of files in a large library are empty or damaged; one of them
    must return 404 for that page rather than a 500 for the whole request.
    """
    try:
        return await renderer.render(_pdf_path(row), doc_id, page, width)
    except IndexError as exc:
        raise HTTPException(404, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("render failed %s p%s: %s", doc_id, page, exc)
        raise HTTPException(502, f"could not render page {page}") from exc


def _etag(*parts) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]
    return f'W/"{h}"'


def _conditional(request: Request, etag: str) -> Response | None:
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag,
                                                  "Cache-Control": IMMUTABLE})
    return None


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------

_ASSET_REF = re.compile(r'(?P<attr>href|src)="(?P<url>/static/[^"?]+)"')


def _asset_version(url: str) -> str:
    """Short fingerprint of a static file, from its size and mtime."""
    target = WEB / "static" / url.removeprefix("/static/")
    try:
        st = target.stat()
    except OSError:
        return "0"
    return hashlib.sha1(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:10]


def _stamp_assets(html: str) -> str:
    """Rewrite /static/... references to carry a content version.

    A stale cache entry for `reader.js` can never satisfy `reader.js?v=<new>`,
    so an edit reaches the browser without anyone clearing anything. This is
    what `Cache-Control` alone cannot guarantee, because a copy cached under
    the old headers may still be considered fresh.
    """
    def stamp(m):
        url = m["url"]
        version = _module_version() if url.endswith(".js") else _asset_version(url)
        return f'{m["attr"]}="{url}?v={version}"'

    return _ASSET_REF.sub(stamp, html)


def _module_version() -> str:
    """One version covering every JS module, since they import each other.

    `reader.js` importing `./util.js` is resolved by the browser, not by us,
    so stamping only the entry point would leave the imported modules stale.
    """
    parts = []
    for f in sorted((WEB / "static" / "js").glob("*.js")):
        st = f.stat()
        parts.append(f"{f.name}:{st.st_size}:{st.st_mtime_ns}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def _shell(name: str, request: Request) -> Response:
    """Serve an app-shell page, always revalidated, with versioned assets."""
    path = WEB / name
    html = _stamp_assets(path.read_text(encoding="utf-8"))
    body = html.encode("utf-8")
    etag = _etag("shell", name, path.stat().st_mtime, len(body), hash(html))
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304,
                        headers={"ETag": etag, "Cache-Control": REVALIDATE})
    return Response(body, media_type="text/html; charset=utf-8",
                    headers={"ETag": etag, "Cache-Control": REVALIDATE})


@app.get("/", response_class=HTMLResponse)
async def page_library(request: Request):
    return _shell("index.html", request)


@app.get("/read/{doc_id}", response_class=HTMLResponse)
async def page_reader(doc_id: str, request: Request):
    return _shell("reader.html", request)


@app.get("/search", response_class=HTMLResponse)
async def page_search(request: Request):
    return _shell("search.html", request)


# --------------------------------------------------------------------------
# library API
# --------------------------------------------------------------------------

@app.get("/api/libraries")
async def api_libraries(conn=Depends(get_db)):
    """Configured libraries, with what the catalogue currently holds for each."""
    counts = {r["library"]: dict(r) for r in conn.execute(
        """SELECT library, count(*) docs, COALESCE(sum(pages), 0) pages,
                  COALESCE(sum(file_size), 0) bytes,
                  COALESCE(sum(has_text), 0) with_text,
                  count(DISTINCT group_id) groups
           FROM docs GROUP BY library""")}
    try:
        cfg = libraries.load()
    except libraries.LibraryError as exc:
        raise HTTPException(500, str(exc))

    out = []
    for lib in cfg.enabled():
        stat = counts.get(lib.id, {})
        out.append({
            "id": lib.id, "name": lib.name, "profile": lib.profile,
            "root": lib.root, "enabled": lib.enabled, "order": lib.order,
            "exists": lib.path.exists(),
            "docs": stat.get("docs", 0), "pages": stat.get("pages", 0),
            "bytes": stat.get("bytes", 0), "groups": stat.get("groups", 0),
            "with_text": stat.get("with_text", 0),
            "indexed": bool(stat.get("docs")),
        })
    return {"libraries": out, "admin": settings.admin_enabled}


@app.get("/api/library")
async def api_library(conn=Depends(get_db), library: str | None = None,
                      group: str | None = None, q: str | None = None):
    where, params = [], []
    if library:
        where.append("d.library = ?")
        params.append(library)
    if group:
        where.append("d.group_id = ?")
        params.append(group)
    if q:
        where.append("(d.title LIKE ? OR d.label LIKE ? OR d.rel_path LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = conn.execute(
        f"""SELECT d.id, d.title, d.label, d.library, d.group_id, d.group_name,
                   d.group_order, d.group_path, d.number, d.volume, d.pages,
                   d.file_size, d.has_text, d.text_pages, d.toc_count,
                   d.page_w, d.page_h,
                   p.page AS progress_page, p.finished, p.updated_at
            FROM docs d LEFT JOIN progress p ON p.doc_id = d.id
            {clause}
            ORDER BY d.group_order, d.group_name, d.sort_a, d.sort_b, d.sort_text""",
        params,
    ).fetchall()

    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(
            r["group_id"],
            {"slug": r["group_id"], "name": r["group_name"],
             "path": r["group_path"], "order": r["group_order"],
             "library": r["library"], "issues": []},
        )
        g["issues"].append(dict(r))

    ordered = sorted(groups.values(), key=lambda g: (g["order"], g["name"].lower()))
    return {"groups": ordered, "total": len(rows)}


@app.get("/api/continue")
async def api_continue(conn=Depends(get_db), limit: int = 12):
    rows = conn.execute(
        """SELECT d.id, d.title, d.label, d.library, d.group_name, d.pages,
                  p.page AS progress_page, p.updated_at, p.finished
           FROM progress p JOIN docs d ON d.id = p.doc_id
           WHERE p.finished = 0 AND p.page > 1
           ORDER BY p.updated_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/doc/{doc_id}")
async def api_doc(doc_id: str, conn=Depends(get_db)):
    row = _doc_or_404(conn, doc_id)
    toc = [dict(r) for r in conn.execute(
        "SELECT idx, level, title, page FROM toc WHERE doc_id = ? ORDER BY idx",
        (doc_id,))]
    prog = conn.execute(
        "SELECT page, updated_at, finished FROM progress WHERE doc_id = ?",
        (doc_id,)).fetchone()

    nav = conn.execute(
        """SELECT id, title, label,
                  LAG(id)  OVER w AS prev_id, LAG(title)  OVER w AS prev_title,
                  LEAD(id) OVER w AS next_id, LEAD(title) OVER w AS next_title
           FROM docs
           WHERE library  = (SELECT library  FROM docs WHERE id = ?)
             AND group_id = (SELECT group_id FROM docs WHERE id = ?)
           WINDOW w AS (ORDER BY sort_a, sort_b, sort_text)""",
        (doc_id, doc_id)).fetchall()
    neighbours = next((dict(r) for r in nav if r["id"] == doc_id), {})

    d = dict(row)
    d.pop("path", None)  # never leak server filesystem layout to the client
    return {
        **d,
        "toc": toc,
        "progress": dict(prog) if prog else None,
        "prev": ({"id": neighbours.get("prev_id"), "title": neighbours.get("prev_title")}
                 if neighbours.get("prev_id") else None),
        "next": ({"id": neighbours.get("next_id"), "title": neighbours.get("next_title")}
                 if neighbours.get("next_id") else None),
        "widths": list(settings.render_widths),
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

@app.get("/api/doc/{doc_id}/page/{page}")
async def api_page(doc_id: str, page: int, request: Request,
                   conn=Depends(get_db),
                   w: int = Query(1400, ge=200, le=4000),
                   prefetch: int = Query(3, ge=0, le=8)):
    row = _doc_or_404(conn, doc_id)
    if not row["pages"]:
        raise HTTPException(404, "this document has no pages")
    if not 1 <= page <= row["pages"]:
        raise HTTPException(404, f"page {page} out of range 1..{row['pages']}")

    width = settings.snap_width(w)
    etag = _etag(doc_id, page, width, row["mtime"], settings.webp_quality)
    if (early := _conditional(request, etag)) is not None:
        return early

    pdf = _pdf_path(row)
    result = await _render(row, doc_id, page, width)

    if prefetch:
        upcoming = [p for p in range(page + 1, page + 1 + prefetch) if p <= row["pages"]]
        if page > 1:
            upcoming.append(page - 1)
        renderer.warm(pdf, doc_id, upcoming, width)

    return Response(
        result.data,
        media_type="image/webp",
        headers={"ETag": etag, "Cache-Control": IMMUTABLE,
                 "X-Cache": "hit" if result.cached else "miss"},
    )


@app.get("/api/doc/{doc_id}/thumb/{page}")
async def api_thumb(doc_id: str, page: int, request: Request, conn=Depends(get_db)):
    row = _doc_or_404(conn, doc_id)
    if not 1 <= page <= row["pages"]:
        raise HTTPException(404, "page out of range")
    etag = _etag("thumb", doc_id, page, row["mtime"])
    if (early := _conditional(request, etag)) is not None:
        return early
    result = await _render(row, doc_id, page, settings.thumb_width)
    return Response(result.data, media_type="image/webp",
                    headers={"ETag": etag, "Cache-Control": IMMUTABLE})


@app.get("/api/doc/{doc_id}/cover")
async def api_cover(doc_id: str, request: Request, conn=Depends(get_db)):
    row = _doc_or_404(conn, doc_id)
    etag = _etag("cover", doc_id, row["mtime"])
    if (early := _conditional(request, etag)) is not None:
        return early
    cover = settings.cover_dir / f"{doc_id}.webp"
    if cover.exists():
        return FileResponse(cover, media_type="image/webp",
                            headers={"ETag": etag, "Cache-Control": IMMUTABLE})
    if not row["pages"]:
        raise HTTPException(404, "this document has no pages")
    result = await _render(row, doc_id, 1, settings.cover_width)
    return Response(result.data, media_type="image/webp",
                    headers={"ETag": etag, "Cache-Control": IMMUTABLE})


@app.get("/api/doc/{doc_id}/text/{page}")
async def api_text(doc_id: str, page: int, conn=Depends(get_db)):
    """Word boxes in PDF points, for the selectable text layer over the image."""
    row = _doc_or_404(conn, doc_id)
    if not 1 <= page <= row["pages"]:
        raise HTTPException(404, "page out of range")

    def extract():
        doc = pymupdf.open(_pdf_path(row))
        try:
            pg = doc[page - 1]
            rect = pg.rect
            words = [
                {"x": round(x0, 2), "y": round(y0, 2),
                 "w": round(x1 - x0, 2), "h": round(y1 - y0, 2), "t": text}
                for x0, y0, x1, y1, text, *_ in pg.get_text("words")
                if text.strip()
            ]
            return {"page": page, "width": rect.width, "height": rect.height,
                    "words": words}
        finally:
            doc.close()

    import asyncio
    data = await asyncio.get_running_loop().run_in_executor(None, extract)
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/doc/{doc_id}/file")
async def api_file(doc_id: str, conn=Depends(get_db)):
    row = _doc_or_404(conn, doc_id)
    return FileResponse(_pdf_path(row), media_type="application/pdf",
                        filename=f"{row['title']}.pdf")


# --------------------------------------------------------------------------
# search & progress
# --------------------------------------------------------------------------

@app.get("/api/search")
async def api_search(q: str = Query("", max_length=300), conn=Depends(get_db),
                     library: str | None = None, group: str | None = None,
                     doc_id: str | None = None,
                     limit: int = Query(60, ge=1, le=200),
                     offset: int = Query(0, ge=0)):
    started = time.perf_counter()
    try:
        result = search_mod.search(conn, q, library=library, group=group,
                                   doc_id=doc_id, limit=limit, offset=offset)
    except Exception as exc:
        log.warning("search failed for %r: %s", q, exc)
        raise HTTPException(400, f"could not run that search: {exc}")
    result["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


@app.put("/api/progress/{doc_id}")
async def api_set_progress(doc_id: str, payload: dict, conn=Depends(get_db)):
    row = _doc_or_404(conn, doc_id)
    page = max(1, min(int(payload.get("page", 1)), row["pages"]))
    finished = 1 if payload.get("finished") or page >= row["pages"] else 0
    conn.execute(
        """INSERT INTO progress (doc_id, path, page, updated_at, finished)
           VALUES (?,?,?,?,?)
           ON CONFLICT(doc_id) DO UPDATE SET
             path=excluded.path, page=excluded.page,
             updated_at=excluded.updated_at, finished=excluded.finished""",
        (doc_id, row["path"], page, time.time(), finished),
    )
    conn.commit()
    return {"doc_id": doc_id, "page": page, "finished": bool(finished)}


@app.delete("/api/progress/{doc_id}")
async def api_clear_progress(doc_id: str, conn=Depends(get_db)):
    conn.execute("DELETE FROM progress WHERE doc_id = ?", (doc_id,))
    conn.commit()
    return {"doc_id": doc_id, "cleared": True}


@app.get("/api/stats")
async def api_stats(conn=Depends(get_db), library: str | None = None):
    clause, params = ("WHERE library = ?", [library]) if library else ("", [])
    row = conn.execute(
        f"""SELECT count(*) docs, COALESCE(sum(pages), 0) pages,
                   COALESCE(sum(file_size), 0) bytes,
                   COALESCE(sum(has_text), 0) with_text,
                   COALESCE(sum(toc_count > 0), 0) with_toc,
                   count(DISTINCT group_id) groups
            FROM docs {clause}""", params
    ).fetchone()
    return {**dict(row), "library": library, "cache": renderer.cache_stats()}


# --------------------------------------------------------------------------
# admin: manage libraries and trigger scans
# --------------------------------------------------------------------------

def _require_admin() -> None:
    if not settings.admin_enabled:
        raise HTTPException(
            403, "the admin API is disabled (set PDFV_ADMIN=1 to enable it)")


def _lib_error(exc: libraries.LibraryError):
    return HTTPException(400, str(exc))


@app.get("/api/admin/libraries")
async def api_admin_libraries():
    """Every configured library, including disabled ones."""
    _require_admin()
    cfg = libraries.load()
    return {"libraries": [
        {"id": l.id, "name": l.name, "root": l.root, "profile": l.profile,
         "enabled": l.enabled, "order": l.order, "exists": l.path.exists()}
        for l in sorted(cfg.libraries, key=lambda l: (l.order, l.name))
    ], "profiles": list(libraries.PROFILES)}


@app.post("/api/admin/libraries", status_code=201)
async def api_add_library(payload: dict = Body(...)):
    _require_admin()
    name = str(payload.get("name", "")).strip()
    root = str(payload.get("root", "")).strip()
    if not name or not root:
        raise HTTPException(400, "name and root are both required")
    try:
        lib = libraries.add(name, root,
                            lib_id=(payload.get("id") or "").strip() or None,
                            profile=payload.get("profile", "auto"))
    except libraries.LibraryError as exc:
        raise _lib_error(exc)

    from .indexer import discover, resolve_profile
    files = list(discover(lib.path))
    return {"id": lib.id, "name": lib.name, "root": lib.root,
            "profile": lib.profile, "resolved_profile": resolve_profile(lib, files),
            "files": len(files)}


@app.patch("/api/admin/libraries/{lib_id}")
async def api_update_library(lib_id: str, payload: dict = Body(...)):
    _require_admin()
    allowed = {k: payload[k] for k in ("name", "profile", "enabled", "order")
               if k in payload}
    if "root" in payload and payload["root"]:
        root = Path(str(payload["root"])).expanduser()
        if not root.is_dir():
            raise HTTPException(400, f"{root} is not a folder")
        allowed["root"] = str(root.resolve())
    try:
        lib = libraries.update(lib_id, **allowed)
    except libraries.LibraryError as exc:
        raise _lib_error(exc)
    return {"id": lib.id, "name": lib.name, "root": lib.root,
            "profile": lib.profile, "enabled": lib.enabled}


@app.delete("/api/admin/libraries/{lib_id}")
async def api_delete_library(lib_id: str, conn=Depends(get_db),
                             purge: bool = Query(True)):
    """Forget a library. Never touches the files on disk."""
    _require_admin()
    try:
        lib = libraries.remove(lib_id)
    except libraries.LibraryError as exc:
        raise _lib_error(exc)

    removed = 0
    if purge:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM docs WHERE library = ?", (lib_id,))]
        for doc_id in ids:
            conn.execute("DELETE FROM pages_fts WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM toc_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM docs WHERE library = ?", (lib_id,))
        conn.execute(
            "DELETE FROM progress WHERE doc_id NOT IN (SELECT id FROM docs)")
        conn.commit()
        removed = len(ids)
    return {"id": lib.id, "name": lib.name, "removed": removed}


@app.post("/api/admin/scan")
async def api_scan(payload: dict = Body(default={})):
    _require_admin()
    lib_id = (payload.get("library") or "").strip() or None
    force = bool(payload.get("force"))
    label = "all libraries"
    if lib_id:
        lib = libraries.load().get(lib_id)
        if not lib:
            raise HTTPException(404, f"no library with id {lib_id!r}")
        label = lib.name
    try:
        return jobs.runner.start(library=lib_id, label=label, force=force)
    except jobs.ScanBusy as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/admin/scan")
async def api_scan_status():
    _require_admin()
    return jobs.runner.status()


# Browsing sits above a media server, where a folder can hold millions of
# files that are not PDFs. Every limit here has to bound the *walk*, not just
# the number of matches, or counting never returns.
_COUNT_MAX_PDFS = 4000
_COUNT_MAX_DIRS = 3000
_COUNT_SECONDS = 1.5


def _count_pdfs(root: Path) -> tuple[int, bool]:
    """Recursive PDF count under a hard time and breadth budget.

    Returns (count, capped); capped means "at least this many" rather than an
    exact total.
    """
    deadline = time.monotonic() + _COUNT_SECONDS
    pdfs = dirs = 0
    for _dir, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        pdfs += sum(1 for f in filenames if f.lower().endswith(".pdf"))
        dirs += 1
        if (pdfs >= _COUNT_MAX_PDFS or dirs >= _COUNT_MAX_DIRS
                or time.monotonic() > deadline):
            return pdfs, True
    return pdfs, False


@app.get("/api/admin/browse")
def api_browse(path: str | None = None):
    """List folders on the server so a library root can be picked, not typed."""
    _require_admin()
    roots = settings.browse_roots

    if not path:
        starts = roots or [Path.home(), Path("/mnt"), Path("/media"), Path("/")]
        seen, entries = set(), []
        for p in starts:
            rp = p.expanduser()
            if rp.is_dir() and str(rp) not in seen:
                seen.add(str(rp))
                entries.append({"name": str(rp), "path": str(rp), "dirs": 0})
        return {"path": None, "parent": None, "entries": entries, "pdfs": 0}

    here = Path(path).expanduser()
    try:
        here = here.resolve(strict=True)
    except OSError:
        raise HTTPException(404, f"{path} does not exist")
    if not here.is_dir():
        raise HTTPException(400, f"{here} is not a folder")
    if roots and not any(here == r or r in here.parents for r in roots):
        raise HTTPException(403, "that folder is outside the browsable roots")

    entries = []
    try:
        for child in sorted(here.iterdir(), key=lambda c: c.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            entries.append({"name": child.name, "path": str(child)})
    except PermissionError:
        raise HTTPException(403, f"cannot read {here}")

    pdfs, capped = _count_pdfs(here)
    parent = str(here.parent) if here.parent != here else None
    return {"path": str(here), "parent": parent, "entries": entries,
            "pdfs": pdfs, "capped": capped}


_JS_IMPORT = re.compile(
    r'(?P<kw>\bfrom\s*|\bimport\s*\()(?P<q>[\'"])(?P<spec>\.{1,2}/[^\'"]+?\.js)(?P=q)')


@app.get("/static/js/{name}.js")
def api_module(name: str, request: Request):
    """Serve a JS module with its relative imports version-stamped.

    The browser resolves `import ... from './util.js'` itself, so a version on
    the entry point alone would still let a cached `util.js` through. Rewriting
    the specifiers keeps the whole module graph on one version.
    """
    target = (WEB / "static" / "js" / f"{name}.js").resolve()
    js_dir = (WEB / "static" / "js").resolve()
    if js_dir not in target.parents or not target.is_file():
        raise HTTPException(404, "no such module")

    version = _module_version()
    source = _JS_IMPORT.sub(
        lambda m: f'{m["kw"]}{m["q"]}{m["spec"]}?v={version}{m["q"]}',
        target.read_text(encoding="utf-8"))
    body = source.encode("utf-8")
    etag = f'W/"{version}-{name}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304,
                        headers={"ETag": etag, "Cache-Control": REVALIDATE})
    return Response(body, media_type="text/javascript; charset=utf-8",
                    headers={"ETag": etag, "Cache-Control": REVALIDATE})


class RevalidatingStatic(StaticFiles):
    """StaticFiles that always revalidates.

    Starlette sends ETag and Last-Modified but no Cache-Control, which leaves
    the browser free to guess a freshness lifetime and serve stale CSS or JS
    after a deploy. Revalidation costs one 304.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = REVALIDATE
        return response


app.mount("/static", RevalidatingStatic(directory=WEB / "static"), name="static")
