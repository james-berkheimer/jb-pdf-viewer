"""One-at-a-time background library scan, with progress the UI can poll.

Indexing is long enough that the browser cannot wait on it, but there is never
a reason to run two scans at once, so a single slot with a status snapshot is
all this needs.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field


class ScanBusy(RuntimeError):
    """A scan is already running."""


@dataclass
class ScanStatus:
    state: str = "idle"          # idle | running | done | error
    library: str | None = None
    label: str = ""
    done: int = 0
    total: int = 0
    current: str = ""
    started: float = 0.0
    finished: float = 0.0
    error: str | None = None
    result: dict | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["elapsed"] = round(
            (self.finished or time.time()) - self.started, 1) if self.started else 0.0
        d["percent"] = round(self.done / self.total * 100, 1) if self.total else 0.0
        return d


class ScanRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = ScanStatus()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._status.state == "running"

    def status(self) -> dict:
        with self._lock:
            return self._status.as_dict()

    def start(self, *, library: str | None, label: str, force: bool = False) -> dict:
        with self._lock:
            if self._status.state == "running":
                raise ScanBusy(f"already scanning {self._status.label or 'the library'}")
            self._status = ScanStatus(state="running", library=library, label=label,
                                      started=time.time())
        self._thread = threading.Thread(
            target=self._run, args=(library, force), daemon=True,
            name="library-scan")
        self._thread.start()
        return self.status()

    def _run(self, library: str | None, force: bool) -> None:
        from .indexer import reindex

        def progress(done: int, total: int, doc_id: str) -> None:
            with self._lock:
                self._status.done = done
                self._status.total = total
                self._status.current = doc_id

        try:
            stats = reindex(force=force, only=library, progress=progress)
            summary = {k: stats[k] for k in
                       ("total", "indexed", "removed", "pages", "elapsed", "relinked")
                       if k in stats}
            summary["errors"] = len(stats.get("errors", []))
            with self._lock:
                self._status.state = "done"
                self._status.result = summary
                self._status.finished = time.time()
        except Exception as exc:
            with self._lock:
                self._status.state = "error"
                self._status.error = f"{type(exc).__name__}: {exc}"
                self._status.finished = time.time()


runner = ScanRunner()
