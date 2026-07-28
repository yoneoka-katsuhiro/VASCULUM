from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass
from typing import TextIO


@dataclass
class ProgressRow:
    status: str = "pending"
    completed: int = 0
    total: int = 0
    records: int = 0
    images: int = 0
    retries: int = 0


class TerminalProgress:
    """Render one width-safe live row per selected source on stderr."""

    def __init__(
        self,
        sources: list[str],
        stream: TextIO | None = None,
        terminal_width: int | None = None,
    ) -> None:
        self.stream = stream or sys.stderr
        self.live = bool(getattr(self.stream, "isatty", lambda: False)())
        self.rows = {source: ProgressRow() for source in sources}
        self.started = time.monotonic()
        self.current_task = "initializing"
        self.records_found = 0
        self.physical_specimens = 0
        self.duplicate_gatherings = 0
        self.images_downloaded = 0
        self.unreferenced_images = 0
        self.errors: list[str] = []
        self.active_source: str | None = None
        self._terminal_width = terminal_width
        self._rendered_lines = 0
        self._last_plain_state: dict[str, str] = {}

    @staticmethod
    def _bar(completed: int, total: int, width: int = 12) -> str:
        if total <= 0:
            return "[" + "." * width + "]"
        filled = min(width, round(width * completed / total))
        return "[" + "#" * filled + "-" * (width - filled) + "]"

    @staticmethod
    def _elapsed(seconds: float) -> str:
        value = max(0, int(seconds))
        hours, remainder = divmod(value, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @staticmethod
    def _fit(text: str, width: int) -> str:
        if width <= 0:
            return ""
        if len(text) <= width:
            return text
        if width == 1:
            return text[:1]
        return text[: width - 1] + "~"

    @staticmethod
    def _status_label(status: str) -> str:
        return {
            "pending": "wait",
            "processing": "search",
            "images": "images",
            "complete": "done",
            "partial": "partial",
            "failed": "failed",
            "validated": "ready",
        }.get(status, status)

    def _width(self) -> int:
        columns = (
            self._terminal_width
            if self._terminal_width is not None
            else shutil.get_terminal_size(fallback=(80, 24)).columns
        )
        # Avoid writing in the last terminal column, which can trigger wrapping.
        return max(1, columns - 1)

    def set_task(self, text: str) -> None:
        self.current_task = text
        self.render()

    def update_source(
        self,
        source: str,
        *,
        status: str | None = None,
        completed: int | None = None,
        total: int | None = None,
        records: int | None = None,
        images: int | None = None,
    ) -> None:
        row = self.rows[source]
        if status is not None:
            row.status = status
            if status in {"processing", "images"}:
                self.active_source = source
        if completed is not None:
            row.completed = completed
        if total is not None:
            row.total = total
        if records is not None:
            row.records = records
        if images is not None:
            row.images = images
        self.render()

    def increment_retry(self, source: str | None = None) -> None:
        source = source or self.active_source
        if source and source in self.rows:
            self.rows[source].retries += 1
            self.render()

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.render()

    def set_totals(
        self,
        *,
        records_found: int | None = None,
        physical_specimens: int | None = None,
        duplicate_gatherings: int | None = None,
        images_downloaded: int | None = None,
        unreferenced_images: int | None = None,
    ) -> None:
        if records_found is not None:
            self.records_found = records_found
        if physical_specimens is not None:
            self.physical_specimens = physical_specimens
        if duplicate_gatherings is not None:
            self.duplicate_gatherings = duplicate_gatherings
        if images_downloaded is not None:
            self.images_downloaded = images_downloaded
        if unreferenced_images is not None:
            self.unreferenced_images = unreferenced_images
        self.render()

    def _source_counts(self) -> tuple[int, int]:
        finished = sum(
            row.status in {"complete", "partial", "failed"}
            for row in self.rows.values()
        )
        active = sum(
            row.status in {"processing", "images"} for row in self.rows.values()
        )
        return finished, active

    def _source_lines(self, width: int) -> list[str]:
        lines: list[str] = []
        if width >= 58:
            lines.append("SOURCE         STATUS   PROGRESS        RECORDS IMAGES")
            for source, row in self.rows.items():
                status = self._status_label(row.status)
                lines.append(
                    f"{source[:14]:<14} {status[:8]:<8} "
                    f"{self._bar(row.completed, row.total, 10):<12} "
                    f"{row.records:>7,} {row.images:>6,}"
                )
        elif width >= 40:
            lines.append("SOURCE         STATUS   DONE    RECORDS")
            for source, row in self.rows.items():
                status = self._status_label(row.status)
                done = f"{row.completed}/{row.total or '?'}"
                lines.append(
                    f"{source[:14]:<14} {status[:8]:<8} "
                    f"{done:>7} {row.records:>7,}"
                )
        else:
            source_width = max(1, width - 18)
            lines.append(
                f"{'SOURCE'[:source_width]:<{source_width}} STATUS   DONE"
            )
            for source, row in self.rows.items():
                status = self._status_label(row.status)
                done = f"{row.completed}/{row.total or '?'}"
                lines.append(
                    f"{source[:source_width]:<{source_width}} "
                    f"{status[:8]:<8} {done:>7}"
                )
        return [self._fit(line, width) for line in lines]

    def _lines(self) -> list[str]:
        width = self._width()
        finished, active = self._source_counts()
        source_summary = (
            f"Sources: {len(self.rows)} selected | {finished} done | {active} active"
        )
        total_summary = (
            f"Totals: {self.records_found:,} found | "
            f"{self.physical_specimens:,} records | "
            f"{self.images_downloaded:,} images | "
            f"{len(self.errors):,} errors"
        )
        if self.unreferenced_images > 0:
            total_summary += f" | {self.unreferenced_images:,} unreferenced"
        lines = [
            self._fit(source_summary, width),
            "",
            *self._source_lines(width),
            "",
            self._fit(f"Task: {self.current_task}", width),
            self._fit(
                f"Elapsed: {self._elapsed(time.monotonic() - self.started)}",
                width,
            ),
            self._fit(total_summary, width),
        ]
        return lines

    def _render_plain(self) -> None:
        for source, row in self.rows.items():
            state = (
                f"{row.status}|{row.completed}|{row.total}|"
                f"{row.records}|{row.images}|{row.retries}"
            )
            if self._last_plain_state.get(source) == state:
                continue
            self._last_plain_state[source] = state
            if row.status != "pending":
                print(
                    f"[{source}] {row.status}: "
                    f"{row.completed}/{row.total or '?'}, "
                    f"{row.records} records, {row.images} images, "
                    f"{row.retries} retries",
                    file=self.stream,
                    flush=True,
                )

    def render(self) -> None:
        if not self.live:
            self._render_plain()
            return
        lines = self._lines()
        if self._rendered_lines:
            self.stream.write(f"\x1b[{self._rendered_lines}F")
        self.stream.write("\n".join("\x1b[2K" + line for line in lines) + "\n")
        self.stream.flush()
        self._rendered_lines = len(lines)

    def finish(self) -> None:
        self.render()
        if self.live:
            self.stream.write("\n")
            self.stream.flush()
