#!/usr/bin/env python3
"""Pre-render pages into the image cache so first opens feel instant.

Rendering every page of every issue would be ~25 GB, so by default this only
warms the opening pages of each issue - enough that browsing the library and
dipping into an issue never waits on a render.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.config import settings


def _warm(path: str, doc_id: str, pages: list[int], widths: list[int]) -> int:
    import io, os
    import pymupdf
    from PIL import Image
    from app.render import renderer

    done = 0
    doc = pymupdf.open(path)
    try:
        for p in pages:
            if not 1 <= p <= doc.page_count:
                continue
            for w in widths:
                target = renderer.cache_path(doc_id, p, w)
                if target.exists():
                    continue
                page = doc[p - 1]
                zoom = w / page.rect.width
                pm = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
                buf = io.BytesIO()
                img.save(buf, "WEBP", quality=settings.webp_quality,
                         method=settings.webp_method)
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_bytes(buf.getvalue())
                os.replace(tmp, target)
                done += 1
    finally:
        doc.close()
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=int, default=6,
                    help="how many opening pages per issue (default 6)")
    ap.add_argument("--width", type=int, action="append",
                    help="render width; repeatable (default 1400)")
    ap.add_argument("--series", help="limit to one series slug")
    ap.add_argument("--all-pages", action="store_true",
                    help="warm every page - large, check disk first")
    args = ap.parse_args()
    widths = args.width or [1400]

    conn = db.connect(readonly=True)
    sql = "SELECT id, path, pages FROM docs WHERE indexed_at IS NOT NULL"
    params: list = []
    if args.series:
        sql += " AND series = ?"
        params.append(args.series)
    rows = conn.execute(sql + " ORDER BY series_order, sort_a", params).fetchall()
    conn.close()

    if not rows:
        print("Nothing to warm - build the index first.")
        return 1

    est = sum(r["pages"] if args.all_pages else min(args.pages, r["pages"]) for r in rows)
    print(f"{len(rows)} issues, ~{est * len(widths)} renders "
          f"(~{est * len(widths) * 0.42 / 1024:.1f} GB at 1400px)")

    started = time.time()
    total = 0
    with ProcessPoolExecutor(max_workers=settings.render_workers) as pool:
        futs = {}
        for r in rows:
            pages = (list(range(1, r["pages"] + 1)) if args.all_pages
                     else list(range(1, min(args.pages, r["pages"]) + 1)))
            futs[pool.submit(_warm, r["path"], r["id"], pages, widths)] = r["id"]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                total += fut.result()
            except Exception as exc:
                print(f"\n  {futs[fut]}: {exc}")
            if i % 10 == 0 or i == len(futs):
                el = time.time() - started
                print(f"\r  {i}/{len(futs)} issues · {total} pages · {el:.0f}s", end="", flush=True)

    print(f"\nWarmed {total} page images in {time.time() - started:.0f}s")
    from app.render import renderer
    st = renderer.cache_stats()
    print(f"Cache now {st['bytes'] / 1024**3:.2f} GB across {st['files']} files "
          f"(limit {st['limit'] / 1024**3:.0f} GB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
