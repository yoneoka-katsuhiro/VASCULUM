from __future__ import annotations

import sys
import threading
from contextlib import AbstractContextManager


def progress(message: str) -> None:
    print(f"running... {message}", file=sys.stderr, flush=True)


def progress_bar(current: int, total: int, label: str, width: int = 30) -> None:
    total = max(total, 1)
    current = min(max(current, 0), total)
    filled = int(round(width * current / total))
    bar = "|" * filled + "." * (width - filled)
    progress(f"[{bar}] {current}/{total} {label}")


class Heartbeat(AbstractContextManager["Heartbeat"]):
    def __init__(
        self,
        message: str,
        interval_seconds: float = 10.0,
        announce_immediately: bool = True,
    ) -> None:
        self.message = message
        self.interval_seconds = interval_seconds
        self.announce_immediately = announce_immediately
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Heartbeat":
        if self.announce_immediately:
            progress(self.message)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            progress(self.message)
