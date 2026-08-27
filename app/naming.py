"""Turn a file path into a browsable group + title.

Different collections are organised differently, so this offers profiles
rather than one hard-coded scheme:

  magazine  filenames encode a series and an issue number
  folders   the containing folder is the group; the filename is the title
  flat      one group for the whole library
  auto      sample the library and pick between magazine and folders

Only `magazine` knows anything about any particular collection, and even that
is pattern matching rather than a fixed list of titles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- magazine

# Recognised magazine lines: display name and where the group sorts.
_SERIES_META = {
    "dragon": ("Dragon", 10),
    "dungeon": ("Dungeon", 20),
    "dragon-annual": ("Dragon Annual", 30),
    "best-of-dragon": ("Best of Dragon", 40),
    "strategic-review": ("The Strategic Review", 50),
}

_CLEAN = re.compile(r"^\s*(accessory|d&d\s*\d?e)\s*[-–]?\s*", re.I)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^strategic\s+review\s*#?\s*(?P<vol>\d+)\.(?P<num>\d+)$", re.I),
     "strategic-review"),
    (re.compile(r"^best\s+of\s+dragon(?:\s+magazine)?\s+volume\s*(?P<num>\d+)$", re.I),
     "best-of-dragon"),
    (re.compile(r"^dragon\s+magazine\s+annual\s*(?P<num>\d{4})$", re.I),
     "dragon-annual"),
    (re.compile(r"^dragon(?:\s+magazine)?\s*#?\s*(?P<num>\d+)$", re.I), "dragon"),
    (re.compile(r"^dungeon(?:\s+magazine)?\s*#?\s*(?P<num>\d+)$", re.I), "dungeon"),
]

# A generic "<words> #12" / "<words> 12" fallback, so an unknown magazine run
# still groups by its own name instead of collapsing into one pile.
_GENERIC_ISSUE = re.compile(
    r"^(?P<name>.+?)[\s_-]*(?:#|no\.?|issue|vol\.?)?\s*(?P<num>\d{1,4})$", re.I)


@dataclass(frozen=True)
class ParsedName:
    group: str           # slug, unique within a library
    group_name: str      # display name for the shelf heading
    group_order: int
    number: int | None
    volume: int | None
    title: str
    label: str           # short caption under a cover tile
    sort_a: int
    sort_b: int
    sort_text: str
    group_path: str = ""  # breadcrumb for nested folders, "" when meaningless


def _natural(text: str) -> str:
    """Sort key that keeps numbers in numeric order inside a string."""
    return re.sub(r"\d+", lambda m: m.group().zfill(8), text.lower())


def _pretty(text: str) -> str:
    text = re.sub(r"[_]+", " ", text).strip()
    return re.sub(r"\s{2,}", " ", text)


def _magazine(path: Path) -> ParsedName | None:
    stem = _CLEAN.sub("", path.stem).strip()

    for pattern, series in _PATTERNS:
        m = pattern.match(stem)
        if not m:
            continue
        num = int(m.group("num"))
        vol = int(m.group("vol")) if "vol" in m.groupdict() and m.group("vol") else None
        name, order = _SERIES_META[series]

        if series == "strategic-review":
            title, label = f"{name} Vol {vol}, #{num}", f"Vol {vol}, #{num}"
            return ParsedName(series, name, order, num, vol, title, label,
                              vol or 0, num, _natural(title))
        if series == "best-of-dragon":
            title, label = f"Best of Dragon, Volume {num}", f"Vol {num}"
        elif series == "dragon-annual":
            title, label = f"Dragon Magazine Annual {num}", str(num)
        else:
            title, label = f"{name} #{num}", f"#{num}"
        return ParsedName(series, name, order, num, None, title, label,
                          num, 0, _natural(title))

    # Unknown run with a trailing number: group by the leading words.
    m = _GENERIC_ISSUE.match(stem)
    if m and m.group("name").strip(" -_#"):
        raw = _pretty(m.group("name")).strip(" -_#")
        num = int(m.group("num"))
        slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-") or "issues"
        name = raw.title() if raw.islower() else raw
        return ParsedName(slug, name, 60, num, None,
                          f"{name} #{num}", f"#{num}", num, 0,
                          _natural(f"{name} {num:06d}"))
    return None


def looks_like_magazine(paths: list[Path], threshold: float = 0.6) -> bool:
    """Does enough of this library encode an issue number to use the magazine profile?"""
    sample = paths[:400]
    if not sample:
        return False
    hits = sum(1 for p in sample if _magazine(p) is not None)
    return hits / len(sample) >= threshold


# ---------------------------------------------------------------- generic

def _folders(path: Path, root: Path) -> ParsedName:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(path.name)

    parent = rel.parent
    if str(parent) in (".", ""):
        group_slug, group_name, order, crumb = "_root", "Loose files", 999, ""
    else:
        group_slug = re.sub(r"[^a-z0-9]+", "-", str(parent).lower()).strip("-")
        group_name = _pretty(parent.name).title() if parent.name.islower() \
            else _pretty(parent.name)
        order = 100
        # Folder names alone are ambiguous once the tree is deep ("Ravenloft"
        # appears under several editions), so keep the enclosing folders as a
        # breadcrumb the library page can show beside the heading.
        crumb = " / ".join(
            _pretty(part).title() if part.islower() else _pretty(part)
            for part in parent.parts[:-1]
        )

    title = _pretty(path.stem)
    return ParsedName(group_slug or "_root", group_name, order, None, None,
                      title, title, 0, 0, _natural(title), crumb)


def _flat(path: Path) -> ParsedName:
    title = _pretty(path.stem)
    return ParsedName("all", "All files", 100, None, None, title, title,
                      0, 0, _natural(title))


def parse(path: Path, profile: str = "folders", root: Path | None = None) -> ParsedName:
    """Resolve one file. `profile` should already be concrete, not 'auto'."""
    if profile == "magazine":
        return _magazine(path) or _folders(path, root or path.parent)
    if profile == "flat":
        return _flat(path)
    return _folders(path, root or path.parent)
