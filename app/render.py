"""Rasterise PDF pages to WebP, with a size-capped disk cache.

Rendering is CPU-bound and PyMuPDF is not thread-safe across a shared
document, so each render opens its own handle inside a worker thread. WebP
encoding dominates (~110ms/page at 1400px) and releases the GIL in Pillow,
so a thread pool is enough; no process pool needed on the hot path.
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from .config import Settings, settings

log = logging.getLogger("render")

# Only sweep the cache occasionally; stat-ing tens of thousands of files on
# every miss would cost more than the renders we are trying to save.
SWEEP_EVERY = 400


@dataclass
class RenderResult:
    data: bytes
    path: Path
    cached: bool


class PageRenderer:
    def __init__(self, cfg: Settings = settings) -> None:
        self.cfg = cfg
        cfg.ensure_dirs()
        self._pool = ThreadPoolExecutor(
            max_workers=cfg.render_workers, thread_name_prefix="render"
        )
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._since_sweep = 0
        self._sweep_lock = threading.Lock()

    # ---------- cache paths ----------

    def cache_path(self, doc_id: str, page: int, width: int) -> Path:
        # Shard by doc id so no single directory holds 50k entries.
        return self.cfg.cache_dir / doc_id / f"{page:05d}@{width}.webp"

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    # ---------- rendering ----------

    def _render_sync(self, pdf_path: Path, doc_id: str, page: int, width: int) -> RenderResult:
        target = self.cache_path(doc_id, page, width)
        if target.exists():
            try:
                os.utime(target, None)  # keep LRU ordering honest
            except OSError:
                pass
            return RenderResult(target.read_bytes(), target, True)

        # One render per (doc, page, width) even under concurrent requests.
        with self._lock_for(f"{doc_id}/{page}@{width}"):
            if target.exists():
                return RenderResult(target.read_bytes(), target, True)

            doc = pymupdf.open(pdf_path)
            try:
                if not 1 <= page <= doc.page_count:
                    raise IndexError(f"page {page} out of range 1..{doc.page_count}")
                pg = doc[page - 1]
                zoom = width / pg.rect.width
                pm = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
            finally:
                doc.close()

            buf = io.BytesIO()
            img.save(buf, "WEBP", quality=self.cfg.webp_quality,
                     method=self.cfg.webp_method)
            data = buf.getvalue()

            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)

        self._maybe_sweep()
        return RenderResult(data, target, False)

    async def render(self, pdf_path: Path, doc_id: str, page: int, width: int) -> RenderResult:
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pool, self._render_sync, pdf_path, doc_id, page, width
        )

    def warm(self, pdf_path: Path, doc_id: str, pages: list[int], width: int) -> None:
        """Fire-and-forget prefetch of upcoming pages."""
        for p in pages:
            if p < 1 or self.cache_path(doc_id, p, width).exists():
                continue
            self._pool.submit(self._safe_render, pdf_path, doc_id, p, width)

    def _safe_render(self, pdf_path: Path, doc_id: str, page: int, width: int) -> None:
        try:
            self._render_sync(pdf_path, doc_id, page, width)
        except Exception as exc:
            log.debug("prefetch failed %s p%s: %s", doc_id, page, exc)

    # ---------- cache maintenance ----------

    def _maybe_sweep(self) -> None:
        with self._sweep_lock:
            self._since_sweep += 1
            if self._since_sweep < SWEEP_EVERY:
                return
            self._since_sweep = 0
        try:
            self.sweep()
        except Exception as exc:
            log.warning("cache sweep failed: %s", exc)

    def cache_stats(self) -> dict:
        total = count = 0
        for f in self.cfg.cache_dir.rglob("*.webp"):
            try:
                total += f.stat().st_size
                count += 1
            except OSError:
                pass
        return {"bytes": total, "files": count, "limit": self.cfg.cache_max_bytes}

    def sweep(self) -> int:
        """Evict least-recently-used pages until the cache fits its budget."""
        entries = []
        total = 0
        for f in self.cfg.cache_dir.rglob("*.webp"):
            try:
                st = f.stat()
            except OSError:
                continue
            entries.append((st.st_atime, st.st_size, f))
            total += st.st_size

        limit = self.cfg.cache_max_bytes
        if total <= limit:
            return 0

        # Trim to 90% of the cap so we are not sweeping again immediately.
        goal = int(limit * 0.9)
        freed = 0
        for _atime, size, f in sorted(entries):
            if total - freed <= goal:
                break
            try:
                f.unlink()
                freed += size
            except OSError:
                pass
        log.info("cache sweep freed %.1f MB", freed / 1024**2)
        return freed

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


renderer = PageRenderer()
