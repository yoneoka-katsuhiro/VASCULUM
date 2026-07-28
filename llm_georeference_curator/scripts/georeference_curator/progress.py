from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass


PROGRESS_BAR_WIDTH = 20
RECORD_STAGE_TOTAL = 3
RECORD_STAGE_NUMBER = {
    "Label reading": 1,
    "LLM georeference": 2,
    "Coordinate verification": 3,
}


def progress_bar(completed: int, total: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    if width < 1:
        return "[]"
    if total <= 0:
        filled = 0
    else:
        filled = round(width * min(max(completed, 0), total) / total)
    return f"[{'=' * filled}{'-' * (width - filled)}]"


@dataclass
class ActivityState:
    message: str
    stage: str
    index: int | None
    total: int | None
    catalog: str
    model: str
    started: float


class TerminalProgress:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.parallel = False
        self._lock = threading.Lock()
        self._activity_id = 0
        self._active: dict[int, ActivityState] = {}
        self._completed_records = 0
        self._total_records = 0
        self._worker_count = 1
        self._reporter_stop = threading.Event()
        self._reporter_thread: threading.Thread | None = None

    def set_parallel(self, enabled: bool) -> None:
        self.parallel = enabled

    def configure_run(self, *, total_records: int, worker_count: int) -> None:
        with self._lock:
            self._total_records = total_records
            self._worker_count = worker_count

    def update(self, message: str) -> None:
        if self.enabled:
            with self._lock:
                print(message, flush=True)

    def complete_record(self, completed: int, total: int, catalog: str, decision: str, verification: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._completed_records = completed
            active_count = len(self._active)
            print(
                f"{progress_bar(completed, total)} "
                f"{completed}/{total} records complete | "
                f"{catalog} | {decision} | {verification} | active={active_count}",
                file=sys.stderr,
                flush=True,
            )

    def finish_run(self) -> None:
        self._reporter_stop.set()
        thread = self._reporter_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def _activity_progress(self, activity: ActivityState, status: str = "active") -> str:
        stage_number = RECORD_STAGE_NUMBER.get(activity.stage, 0)
        stage_bar = progress_bar(stage_number, RECORD_STAGE_TOTAL)
        catalog = activity.catalog or "unknown-record"
        stage = activity.stage or activity.message
        elapsed = int(time.monotonic() - activity.started)
        model = f" | {activity.model}" if activity.model else ""
        return (
            f"{stage_bar} {stage_number}/{RECORD_STAGE_TOTAL} "
            f"{catalog} | {stage}{model} | {status} {elapsed}s"
        )

    def _ensure_parallel_reporter(self, stream) -> None:
        if self._reporter_thread and self._reporter_thread.is_alive():
            return
        self._reporter_stop.clear()

        def report() -> None:
            while not self._reporter_stop.wait(10.0):
                with self._lock:
                    if not self._active:
                        continue
                    active = list(self._active.values())
                    total = self._total_records or (active[0].total or 0)
                    print(
                        f"{progress_bar(self._completed_records, total)} "
                        f"{self._completed_records}/{total} records complete | "
                        f"workers={self._worker_count} | active={len(active)}",
                        file=stream,
                        flush=True,
                    )
                    for activity in sorted(
                        active,
                        key=lambda item: (
                            item.index if item.index is not None else sys.maxsize,
                            item.started,
                        ),
                    ):
                        print(
                            f"  {self._activity_progress(activity)}",
                            file=stream,
                            flush=True,
                        )

        self._reporter_thread = threading.Thread(target=report, daemon=True)
        self._reporter_thread.start()

    @contextmanager
    def activity(
        self,
        message: str,
        *,
        stage: str = "",
        index: int | None = None,
        total: int | None = None,
        catalog: str = "",
        model: str = "",
    ):
        if not self.enabled:
            yield
            return

        stop = threading.Event()
        started = time.monotonic()
        stream = sys.stderr
        dynamic = stream.isatty() and not self.parallel
        activity_id = 0
        activity_state = ActivityState(
            message=message,
            stage=stage,
            index=index,
            total=total,
            catalog=catalog,
            model=model,
            started=started,
        )
        if self.parallel:
            with self._lock:
                self._activity_id += 1
                activity_id = self._activity_id
                self._active[activity_id] = activity_state
                self._ensure_parallel_reporter(stream)
                print(
                    self._activity_progress(activity_state),
                    file=stream,
                    flush=True,
                )

        def animate() -> None:
            frames = "|/-\\"
            frame_index = 0
            while not stop.is_set():
                elapsed = int(time.monotonic() - started)
                text = self._activity_progress(
                    activity_state,
                    status=f"active {frames[frame_index % len(frames)]}",
                )
                if dynamic:
                    with self._lock:
                        stream.write(f"\r{text}")
                        stream.flush()
                elif elapsed == 0 or elapsed % 5 == 0:
                    with self._lock:
                        print(text, file=stream, flush=True)
                frame_index += 1
                stop.wait(1.0)

        thread = threading.Thread(target=animate, daemon=True)
        if not self.parallel:
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if not self.parallel:
                thread.join(timeout=2.0)
            elapsed = int(time.monotonic() - started)
            with self._lock:
                if self.parallel and activity_id in self._active:
                    activity = self._active.pop(activity_id)
                    print(
                        self._activity_progress(activity, status="done"),
                        file=stream,
                        flush=True,
                    )
                    return
                if dynamic:
                    stream.write("\r\033[K")
                print(
                    self._activity_progress(activity_state, status="done"),
                    file=stream,
                    flush=True,
                )
