from __future__ import annotations

import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


DEFAULT_CODEX_WORKER_CAP = 4
DOCUMENTED_CODEX_THREAD_CAP_FALLBACK = 8
MAX_REASONABLE_WORKERS = 16

RATE_LIMIT_PATTERNS = (
    "429",
    "too many requests",
    "rate limit",
    "ratelimit",
    "usage limit",
    "quota",
    "exceeded retry limit",
)


@dataclass
class RateLimitBackoff:
    retries: int = 2
    base_seconds: float = 30.0
    max_seconds: float = 300.0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _pause_until: float = 0.0
    _failures: int = 0

    def wait(self) -> None:
        while True:
            with self._lock:
                delay = self._pause_until - time.monotonic()
            if delay <= 0:
                return
            time.sleep(min(delay, 5.0))

    def note_success(self) -> None:
        with self._lock:
            self._failures = max(0, self._failures - 1)

    def note_rate_limit(self) -> float:
        with self._lock:
            self._failures += 1
            delay = min(self.max_seconds, self.base_seconds * (2 ** (self._failures - 1)))
            delay += random.uniform(0.0, min(10.0, delay * 0.25))
            self._pause_until = max(self._pause_until, time.monotonic() + delay)
            return delay


def is_rate_limit_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(pattern in text for pattern in RATE_LIMIT_PATTERNS)


def positive_int(value: str) -> Optional[int]:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def env_worker_cap() -> Optional[int]:
    for name in ("VASCULUM_LLM_WORKERS", "VASCULUM_MAX_WORKERS", "LLM_WORKERS"):
        value = positive_int(os.environ.get(name, ""))
        if value:
            return value
    return None


def read_codex_thread_cap(project_dir: Path) -> Optional[int]:
    if tomllib is None:
        return None
    candidates = [
        project_dir.parent / ".codex" / "config.toml",
        Path.home() / ".codex" / "config.toml",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        agents = data.get("agents") if isinstance(data, dict) else None
        if not isinstance(agents, dict):
            continue
        for key in ("max_concurrent_threads_per_session", "max_threads"):
            value = positive_int(str(agents.get(key, "")))
            if value:
                return value
    return None


def resolve_worker_count(
    requested: str,
    *,
    project_dir: Path,
    provider: str,
    model_label: str,
    web_search_mode: str,
    records: int,
) -> int:
    if records <= 1:
        return 1
    value = (requested or "auto").strip().lower()
    explicit = positive_int(value)
    if explicit:
        return min(explicit, records, MAX_REASONABLE_WORKERS)
    if value not in {"auto", "max"}:
        raise ValueError("--workers must be a positive integer, auto, or max")

    configured_cap = env_worker_cap()
    if configured_cap:
        cap = configured_cap
    elif provider == "codex-cli":
        cap = read_codex_thread_cap(project_dir) or (
            DOCUMENTED_CODEX_THREAD_CAP_FALLBACK if value == "max" else DEFAULT_CODEX_WORKER_CAP
        )
    else:
        cap = DOCUMENTED_CODEX_THREAD_CAP_FALLBACK if value == "max" else DEFAULT_CODEX_WORKER_CAP

    if provider == "codex-cli" and value == "auto":
        heavy_model = bool(re.search(r"sol|5\.6", model_label, flags=re.I))
        heavy_search = web_search_mode == "live"
        if heavy_model and heavy_search:
            cap = min(cap, 2)
        elif heavy_model or heavy_search:
            cap = min(cap, 3)

    return max(1, min(cap, records, MAX_REASONABLE_WORKERS))
