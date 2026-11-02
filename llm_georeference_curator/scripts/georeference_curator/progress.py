"""Terminal progress: one line per finished specimen plus a single live status line.

Everything here goes to stderr so stdout stays free for results. The live status
line is drawn only on a terminal; redirected output gets the per-record lines
alone. Stage-level messages appear only with --verbose.
"""
from __future__ import annotations

import shutil
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass


PROGRESS_BAR_WIDTH = 20
STATUS_REFRESH_SECONDS = 1.0


def progress_bar(completed: int, total: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    if width < 1:
        return "[]"
    if total <= 0:
        filled = 0
    else:
        filled = round(width * min(max(completed, 0), total) / total)
    return f"[{'=' * filled}{'-' * (width - filled)}]"


def format_radius(meters) -> str:
    try:
        value = float(meters)
    except (TypeError, ValueError):
        return "-"
    return f"{value:.0f} m" if value < 1000 else f"{value / 1000:.1f} km"


def format_tokens(tokens: int) -> str:
    if tokens < 1000:
        return str(tokens)
    if tokens < 1_000_000:
        return f"{round(tokens / 1000)}k"
    return f"{tokens / 1_000_000:.1f}M"


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


@dataclass
class RecordLine:
    completed: int
    total: int
    catalog: str
    result: str
    confidence: str = ""
    radius_meters: str = ""
    basis: str = ""
    locality: str = ""
    seconds: float = 0.0
    tokens: int = 0


RECORD_HEADER = f"{'#':>5}  {'specimen':<14}{'result':<10}{'conf':<6}{'radius':>9}  {'basis':<18}{'time':>7}{'tok':>7}  locality"


def format_record_line(line: RecordLine) -> str:
    confidence = {"medium": "med"}.get(line.confidence, line.confidence) if line.result == "accepted" else "-"
    radius = format_radius(line.radius_meters) if line.result == "accepted" else "-"
    locality = line.locality if len(line.locality) <= 40 else line.locality[:39] + "…"
    return (f"{line.completed:>5}  {line.catalog[:13]:<14}{line.result:<10}{confidence:<6}{radius:>9}  "
            f"{line.basis[:17]:<18}{format_duration(line.seconds):>7}{format_tokens(line.tokens):>7}  {locality}")


@dataclass
class ActivityState:
    stage: str
    catalog: str
    model: str
    started: float


class TerminalProgress:
    def __init__(self, enabled: bool = True, verbose: bool = False) -> None:
        self.enabled = enabled
        self.verbose = verbose
        self.parallel = False
        self._lock = threading.Lock()
        self._activity_id = 0
        self._active: dict[int, ActivityState] = {}
        self._completed = 0
        self._total = 0
        self._failed = 0
        self._tokens = 0
        self._started = time.monotonic()
        self._header_printed = False
        self._status_shown = False
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None

    @property
    def _live(self) -> bool:
        return self.enabled and not self.verbose and sys.stderr.isatty()

    def set_parallel(self, enabled: bool) -> None:
        self.parallel = enabled

    def configure_run(self, *, total_records: int, worker_count: int) -> None:
        with self._lock:
            self._total = total_records
            self._started = time.monotonic()

    def update(self, message: str) -> None:
        """A run-level message such as the header or a warning."""
        if self.enabled:
            self._write_line(message)

    def detail(self, message: str) -> None:
        """Diagnostic detail shown only with --verbose; the log keeps the rest."""
        if self.enabled and self.verbose:
            self._write_line(message)

    def complete_record(self, line: RecordLine) -> None:
        with self._lock:
            self._completed = line.completed
            self._total = line.total or self._total
            self._tokens += line.tokens
            self._failed += line.result == "failed"
        if not self.enabled:
            return
        if not self._header_printed:
            self._header_printed = True
            self._write_line(RECORD_HEADER)
        self._write_line(format_record_line(line))

    def finish_run(self) -> None:
        self._stop.set()
        if self._ticker and self._ticker.is_alive():
            self._ticker.join(timeout=2.0)
        with self._lock:
            self._clear_status()

    @contextmanager
    def activity(self, message: str, *, stage: str = "", index: int | None = None, total: int | None = None,
                 catalog: str = "", model: str = ""):
        if not self.enabled:
            yield
            return
        state = ActivityState(stage=stage or message, catalog=catalog, model=model, started=time.monotonic())
        with self._lock:
            self._activity_id += 1
            activity_id = self._activity_id
            self._active[activity_id] = state
        self._ensure_ticker()
        succeeded = False
        try:
            yield
            succeeded = True
        finally:
            with self._lock:
                self._active.pop(activity_id, None)
            if self.verbose:
                elapsed = int(time.monotonic() - state.started)
                model_text = f" | {state.model}" if state.model else ""
                self._write_line(f"  {state.catalog or '-'} | {state.stage}{model_text} | {'done' if succeeded else 'failed'} {elapsed}s")

    def status_text(self) -> str:
        elapsed = time.monotonic() - self._started
        parts = [f"{progress_bar(self._completed, self._total)} {self._completed}/{self._total}",
                 f"{format_duration(elapsed)} elapsed"]
        if 0 < self._completed < self._total:
            parts.append(f"~{format_duration(elapsed / self._completed * (self._total - self._completed))} left")
        parts.append(f"{format_tokens(self._tokens)} tok")
        parts.append(f"failed {self._failed}")
        active = sorted(self._active.values(), key=lambda item: item.started)
        names = [f"{item.catalog} {item.stage.lower()}" if item.catalog else item.stage.lower() for item in active[:2]]
        if names:
            parts.append("now: " + ", ".join(names))
        # A wrapped status line cannot be erased with \r, so keep it within one terminal row.
        return " · ".join(parts)[:shutil.get_terminal_size().columns - 1]

    def _ensure_ticker(self) -> None:
        if not self._live or (self._ticker and self._ticker.is_alive()):
            return
        self._stop.clear()

        def tick() -> None:
            while not self._stop.wait(STATUS_REFRESH_SECONDS):
                with self._lock:
                    self._draw_status()

        self._ticker = threading.Thread(target=tick, daemon=True)
        self._ticker.start()

    def _write_line(self, text: str) -> None:
        with self._lock:
            self._clear_status()
            print(text, file=sys.stderr, flush=True)
            if self._live and self._ticker and self._ticker.is_alive():
                self._draw_status()

    def _draw_status(self) -> None:
        if not self._live:
            return
        sys.stderr.write("\r\033[K" + self.status_text())
        sys.stderr.flush()
        self._status_shown = True

    def _clear_status(self) -> None:
        if self._status_shown:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
            self._status_shown = False
