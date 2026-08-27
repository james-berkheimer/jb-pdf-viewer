#!/usr/bin/env python3
"""Add, list, edit and remove libraries.

A library is a folder of PDFs plus a profile saying how to group them:

  auto      look at the filenames and choose (default)
  magazine  filenames encode a series and issue number
  folders   the containing folder is the group
  flat      one group for everything

Adding a library only records it. Run scripts/index_library.py afterwards to
scan it - or pass --index to do both.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, libraries
from app.indexer import discover, resolve_profile
from app.libraries import LibraryError


def _counts() -> dict[str, tuple[int, int]]:
    """(documents, pages) already indexed, per library."""
    try:
        with db.session(readonly=True) as conn:
            return {r["library"]: (r["n"], r["pages"] or 0) for r in conn.execute(
                "SELECT library, count(*) n, sum(pages) pages FROM docs GROUP BY library")}
    except Exception:
        return {}


def cmd_list(args) -> int:
    cfg = libraries.load()
    if not cfg.libraries:
        print("No libraries yet. Add one:")
        print('  scripts/library.py add "My Books" /path/to/pdfs')
        return 0

    counts = _counts()
    print(f"{'ID':22} {'NAME':26} {'PROFILE':9} {'INDEXED':>16}  ROOT")
    for lib in sorted(cfg.libraries, key=lambda l: (l.order, l.name)):
        docs, pages = counts.get(lib.id, (0, 0))
        flag = "" if lib.enabled else "  (disabled)"
        missing = "" if lib.path.exists() else "  [MISSING]"
        indexed = f"{docs} docs, {pages:,}p" if docs else "not indexed"
        print(f"{lib.id:22} {lib.name:26} {lib.profile:9} {indexed:>16}  "
              f"{lib.root}{flag}{missing}")
    print(f"\nConfig: {libraries.config_path()}")
    return 0


def cmd_add(args) -> int:
    root = Path(args.path).expanduser()
    lib = libraries.add(args.name, root, lib_id=args.id, profile=args.profile,
                        order=args.order)

    files = list(discover(lib.path))
    resolved = resolve_profile(lib, files)
    print(f"Added {lib.id!r}: {lib.name}")
    print(f"  root    : {lib.root}")
    print(f"  profile : {lib.profile}"
          + (f"  (resolves to {resolved})" if lib.profile == "auto" else ""))
    print(f"  found   : {len(files)} PDFs")
    if not files:
        print("  Nothing to index - check the path.")
        return 0

    if args.index:
        return _run_index(lib.id)
    print(f"\nNow run:  scripts/index_library.py --library {lib.id}")
    return 0


def cmd_remove(args) -> int:
    lib = libraries.remove(args.id)
    print(f"Removed {lib.id!r} ({lib.name}) from the config.")
    print("Its files are untouched. Run the indexer to drop it from the catalogue:")
    print("  scripts/index_library.py")
    return 0


def cmd_set(args) -> int:
    changes = {k: v for k, v in
               (("name", args.name), ("root", str(Path(args.path).expanduser().resolve())
                                      if args.path else None),
                ("profile", args.profile), ("order", args.order))
               if v is not None}
    if args.enable:
        changes["enabled"] = True
    if args.disable:
        changes["enabled"] = False
    if not changes:
        print("Nothing to change.")
        return 1
    lib = libraries.update(args.id, **changes)
    print(f"Updated {lib.id!r}: {lib}")
    if {"root", "profile"} & changes.keys():
        print("Root or profile changed - re-run the indexer with --force.")
    return 0


def _run_index(lib_id: str | None) -> int:
    from app.indexer import reindex
    print()
    stats = reindex(only=lib_id, progress=lambda d, t, i: print(
        f"\r  {d}/{t}  {i[:40]:40}", end="", flush=True))
    print(f"\n  indexed {stats['indexed']} documents, {stats['pages']:,} pages "
          f"in {stats['elapsed']:.1f}s")
    if stats["errors"]:
        print(f"  {len(stats['errors'])} errors")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list", help="show configured libraries").set_defaults(fn=cmd_list)

    a = sub.add_parser("add", help="add a library")
    a.add_argument("name", help='display name, e.g. "Pathfinder"')
    a.add_argument("path", help="folder to scan")
    a.add_argument("--id", help="url slug (default: derived from the name)")
    a.add_argument("--profile", default="auto", choices=libraries.PROFILES)
    a.add_argument("--order", type=int, help="sort position in the library switcher")
    a.add_argument("--index", action="store_true", help="scan it straight away")
    a.set_defaults(fn=cmd_add)

    r = sub.add_parser("remove", help="remove a library from the config")
    r.add_argument("id")
    r.set_defaults(fn=cmd_remove)

    s = sub.add_parser("set", help="change a library")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--path")
    s.add_argument("--profile", choices=libraries.PROFILES)
    s.add_argument("--order", type=int)
    s.add_argument("--enable", action="store_true")
    s.add_argument("--disable", action="store_true")
    s.set_defaults(fn=cmd_set)

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        return cmd_list(args)
    try:
        return args.fn(args)
    except LibraryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
