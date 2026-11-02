from __future__ import annotations

import hashlib
import json
import os
import platform
import threading
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def implementation_signature(directory):
    files = {path.name: file_hash(path) for path in sorted(Path(directory).glob("*.py"))}
    try:
        import pyproj
        crs_runtime = {"pyproj": pyproj.__version__, "PROJ": pyproj.proj_version_str}
    except ImportError:
        crs_runtime = {"pyproj": "unavailable"}
    try:
        import shapely
        geometry_runtime = {"shapely": shapely.__version__, "GEOS": shapely.geos_version_string}
    except ImportError:
        geometry_runtime = {"shapely": "unavailable"}
    return {"python": platform.python_version(), "files": files, "crsRuntime": crs_runtime, "geometryRuntime": geometry_runtime}


class RecordJournal:
    def __init__(self, directory, configuration, *, enabled=True):
        self.enabled = enabled
        self.configuration_hash = fingerprint(configuration)
        self.path = Path(directory) / f"{self.configuration_hash}.jsonl"
        self._lock = threading.Lock()
        self._records = {}
        self._separator = False
        if enabled and self.path.exists():
            data = self.path.read_bytes()
            self._separator = bool(data and not data.endswith(b"\n"))
            for line in data.splitlines():
                try:
                    entry = json.loads(line)
                    self._records[entry["key"]] = entry["outcome"]
                except (ValueError, KeyError, TypeError):
                    continue

    def get(self, key):
        if not self.enabled:
            return None
        with self._lock:
            return self._records.get(key)

    def put(self, key, outcome):
        if not self.enabled:
            return
        record = json.dumps({"key": key, "outcome": outcome}, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                if self._separator:
                    handle.write("\n")
                    self._separator = False
                handle.write(record + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._records[key] = outcome
