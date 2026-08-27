#!/usr/bin/env python3
"""Print a short summary of a running viewer, for the `pdfv` shell helper.

Reads /api/stats JSON on stdin. Kept as a file rather than inlined in the
alias so the quoting stays sane and it can be tested on its own.
"""
from __future__ import annotations

import json
import sys


def gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def main() -> int:
    try:
        d = json.load(sys.stdin)
    except Exception:
        print("  (API not answering yet)")
        return 1

    cache = d.get("cache", {})
    print(f"  {d.get('docs', 0):,} documents · {d.get('pages', 0):,} pages · "
          f"{gb(d.get('bytes', 0))} of PDFs")
    print(f"  {d.get('with_text', 0):,} searchable · {d.get('groups', 0)} groups")
    limit = cache.get("limit", 0)
    used = cache.get("bytes", 0)
    pct = f" ({used / limit * 100:.0f}%)" if limit else ""
    print(f"  render cache {gb(used)} / {gb(limit)}{pct} across "
          f"{cache.get('files', 0):,} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
