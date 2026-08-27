"""User-defined libraries.

A library is just a name, a folder to scan, and a profile that says how to turn
the files inside it into browsable groups. Libraries live in a JSON file so
they can be added by hand or with `scripts/library.py`; nothing about any
particular collection is baked into the code.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import settings

# How filenames become groups and titles. See naming.py for the implementations.
PROFILES = ("auto", "magazine", "folders", "flat")

CONFIG_NAME = "libraries.json"


class LibraryError(ValueError):
    """Raised for problems a user can fix - bad path, duplicate id, etc."""


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "library"


@dataclass
class Library:
    id: str
    name: str
    root: str
    profile: str = "auto"
    enabled: bool = True
    order: int = 100

    @property
    def path(self) -> Path:
        return Path(self.root).expanduser()

    def validate(self) -> None:
        if not self.id or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.id):
            raise LibraryError(
                f"library id {self.id!r} must be lowercase letters, digits and dashes")
        if self.profile not in PROFILES:
            raise LibraryError(
                f"unknown profile {self.profile!r} (choose from {', '.join(PROFILES)})")
        if not self.name.strip():
            raise LibraryError("library name cannot be empty")


@dataclass
class LibraryConfig:
    libraries: list[Library] = field(default_factory=list)

    def get(self, lib_id: str) -> Library | None:
        return next((l for l in self.libraries if l.id == lib_id), None)

    def enabled(self) -> list[Library]:
        return [l for l in sorted(self.libraries, key=lambda l: (l.order, l.name))
                if l.enabled]


def config_path() -> Path:
    return settings.data_dir / CONFIG_NAME


def _seed_from_env() -> LibraryConfig:
    """First run: adopt whatever PDFV_LIBRARY points at so nothing breaks."""
    libs = []
    for i, root in enumerate(settings.library_roots):
        libs.append(Library(
            id=slugify(root.name) or f"library-{i + 1}",
            name=root.name.replace("_", " ").replace("-", " ").title() or "Library",
            root=str(root),
            profile="auto",
            order=10 * (i + 1),
        ))
    return LibraryConfig(libs)


def load() -> LibraryConfig:
    path = config_path()
    if not path.exists():
        cfg = _seed_from_env()
        save(cfg)
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryError(f"could not read {path}: {exc}") from exc

    libs = []
    for entry in raw.get("libraries", []):
        known = {k: entry[k] for k in ("id", "name", "root", "profile", "enabled", "order")
                 if k in entry}
        known.setdefault("id", slugify(known.get("name", "library")))
        lib = Library(**known)
        lib.validate()
        libs.append(lib)
    return LibraryConfig(libs)


def save(cfg: LibraryConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": "Libraries scanned by jb-pdf-viewer. "
                    "Edit here or use scripts/library.py, then re-run the indexer.",
        "libraries": [asdict(l) for l in cfg.libraries],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def add(name: str, root: str | Path, *, lib_id: str | None = None,
        profile: str = "auto", order: int | None = None) -> Library:
    cfg = load()
    path = Path(root).expanduser()
    if not path.exists():
        raise LibraryError(f"{path} does not exist")
    if not path.is_dir():
        raise LibraryError(f"{path} is not a folder")

    lib_id = lib_id or slugify(name)
    if cfg.get(lib_id):
        raise LibraryError(f"a library with id {lib_id!r} already exists")
    resolved = str(path.resolve())
    if any(l.root == resolved for l in cfg.libraries):
        raise LibraryError(f"{resolved} is already a library")

    lib = Library(
        id=lib_id, name=name, root=resolved, profile=profile,
        order=order if order is not None else (max((l.order for l in cfg.libraries),
                                                   default=0) + 10),
    )
    lib.validate()
    cfg.libraries.append(lib)
    save(cfg)
    return lib


def remove(lib_id: str) -> Library:
    cfg = load()
    lib = cfg.get(lib_id)
    if not lib:
        raise LibraryError(f"no library with id {lib_id!r}")
    cfg.libraries = [l for l in cfg.libraries if l.id != lib_id]
    save(cfg)
    return lib


def update(lib_id: str, **changes) -> Library:
    cfg = load()
    lib = cfg.get(lib_id)
    if not lib:
        raise LibraryError(f"no library with id {lib_id!r}")
    for key, value in changes.items():
        if value is None or not hasattr(lib, key):
            continue
        setattr(lib, key, value)
    lib.validate()
    save(cfg)
    return lib
