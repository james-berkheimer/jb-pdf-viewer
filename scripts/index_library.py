#!/usr/bin/env python3
"""Build or refresh the library index. Safe to re-run; only changed files rescan."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import libraries
from app.config import settings
from app.indexer import reindex


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="reindex every file")
    ap.add_argument("--library", help="only scan this library id")
    ap.add_argument("--limit", type=int, help="only process N files (for testing)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    last = [0.0]

    def show(done: int, total: int, doc_id: str) -> None:
        now = time.time()
        if now - last[0] < 0.2 and done != total:
            return
        last[0] = now
        pct = done / total * 100
        bar = "=" * int(pct / 2.5)
        print(f"\r  [{bar:<40}] {done}/{total} {pct:5.1f}%  {doc_id[:28]:28}",
              end="", flush=True)

    cfg = libraries.load()
    shown = [l for l in cfg.enabled() if not args.library or l.id == args.library]
    if not shown:
        print(f"No enabled library matches {args.library!r}.")
        print("Configured libraries:")
        for l in cfg.libraries:
            print(f"  {l.id}  ->  {l.root}")
        return 1
    print(f"Data: {settings.data_dir}")
    for l in shown:
        print(f"  {l.id:20} {l.profile:9} {l.root}")

    stats = reindex(force=args.force, limit=args.limit, only=args.library,
                    progress=None if args.quiet else show)
    print()
    for entry in stats["libraries"]:
        print(f"  {entry['id']:20} {entry['files']:5} files  "
              f"(profile: {entry['profile']})")
    print(f"  catalogued : {stats['total']} documents")
    print(f"  indexed    : {stats['indexed']} ({stats['pages']} pages)")
    if stats["removed"]:
        print(f"  removed    : {stats['removed']} missing files")
    if stats.get("relinked"):
        print(f"  bookmarks  : {stats['relinked']} re-linked to new ids")
    print(f"  elapsed    : {stats['elapsed']:.1f}s")
    if stats["errors"]:
        print(f"  ERRORS     : {len(stats['errors'])}")
        for doc_id, err in stats["errors"][:15]:
            print(f"      {doc_id}: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
