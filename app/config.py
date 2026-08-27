"""Runtime settings. Everything is overridable by environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LIBRARY = "/mnt/media/books/roleplaying/dungeons_and_dragons/magazines"


def _env_paths(name: str, default: str) -> list[Path]:
    raw = os.environ.get(name, default)
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    library_roots: list[Path] = field(
        default_factory=lambda: _env_paths("PDFV_LIBRARY", DEFAULT_LIBRARY)
    )
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("PDFV_DATA", Path(__file__).resolve().parent.parent / "data")
        )
    )

    # Render cache
    cache_max_bytes: int = field(
        default_factory=lambda: _env_int("PDFV_CACHE_GB", 20) * 1024**3
    )
    # Widths the API will serve. Requests snap to one of these so the cache
    # does not fragment across arbitrary viewport widths. The largest exists
    # for the magnifier, which needs real detail rather than an upscale of
    # whatever the page is currently drawn at.
    render_widths: tuple[int, ...] = (900, 1400, 1800, 2400, 3200)
    thumb_width: int = 220
    cover_width: int = 480
    webp_quality: int = 80
    webp_method: int = 2  # 2 is ~2x faster than 4 for <2% size cost on scans

    render_workers: int = field(
        default_factory=lambda: _env_int("PDFV_WORKERS", max(2, (os.cpu_count() or 4) - 1))
    )

    # The admin API lets the web UI add libraries and browse server folders.
    # That is reasonable on a private LAN and a bad idea on the open internet,
    # so it can be switched off without touching the reader.
    admin_enabled: bool = field(
        default_factory=lambda: os.environ.get("PDFV_ADMIN", "1").strip().lower()
        not in ("0", "false", "no", "off")
    )
    # Optional whitelist for the folder browser; empty means anywhere readable.
    browse_roots: list[Path] = field(
        default_factory=lambda: [Path(p) for p in
                                 os.environ.get("PDFV_BROWSE_ROOTS", "").split(os.pathsep)
                                 if p.strip()]
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def cover_dir(self) -> Path:
        return self.data_dir / "covers"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.cache_dir, self.cover_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def magnify_width(self) -> int:
        """Width used to source the magnifier."""
        return self.render_widths[-1]

    def snap_width(self, w: int | None) -> int:
        """Clamp an arbitrary requested width to a cacheable rendering width."""
        if not w:
            return self.render_widths[1]
        # Round up to the next available width so a page is never upscaled;
        # only fall back to the largest when the request exceeds it.
        return next((x for x in self.render_widths if x >= w), self.render_widths[-1])


settings = Settings()
