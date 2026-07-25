from __future__ import annotations

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
    """Render live progress on stderr without creating persistent log files."""

    def __init__(self, sources: list[str], stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stderr
        self.live = bool(getattr(self.stream, "isatty", lambda: False)())
        self.rows = {source: ProgressRow() for source in sources}
        self.started = time.monotonic()
        self.current_task = "initializing"
        self.records_found = 0
        self.physical_specimens = 0
        self.duplicate_gatherings = 0
        self.errors: list[str] = []
        self.active_source: str | None = None
        self._rendered_lines = 0
        self._last_plain_state: dict[str, str] = {}

    @staticmethod
    def _bar(completed: int, total: int, width: int = 24) -> str:
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
    ) -> None:
        if records_found is not None:
            self.records_found = records_found
        if physical_specimens is not None:
            self.physical_specimens = physical_specimens
        if duplicate_gatherings is not None:
            self.duplicate_gatherings = duplicate_gatherings
        self.render()

    def _lines(self) -> list[str]:
        lines = [
            "SOURCE               STATUS       PROGRESS                    RECORDS   IMAGES  RETRY",
            "",
        ]
        for source, row in self.rows.items():
            progress = self._bar(row.completed, row.total)
            lines.append(
                f"{source[:20]:<20} {row.status[:12]:<12} {progress:<28} "
                f"{row.records:>7,} {row.images:>8,} {row.retries:>6,}"
            )
        source_error_count = len(
            {
                error.split(":", 1)[0]
                for error in self.errors
                if ":" in error
            }
        )
        lines.extend(
            [
                "",
                f"Current task : {self.current_task}",
                f"Elapsed      : {self._elapsed(time.monotonic() - self.started)}",
                f"Records found: {self.records_found:,}",
                f"Physical specimens after deduplication: {self.physical_specimens:,}",
                f"Duplicate gatherings grouped: {self.duplicate_gatherings:,}",
                f"Errors       : {source_error_count} source(s), {len(self.errors)} event(s)",
            ]
        )
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
