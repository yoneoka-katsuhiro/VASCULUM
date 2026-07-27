from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path


CACHE_FORMAT_VERSION = "llm-response-v1"


class LlmResponseCache:
    def __init__(self, directory: Path, *, enabled: bool = True) -> None:
        self.directory = directory
        self.enabled = enabled
        self._lock = threading.Lock()

    def key(
        self,
        *,
        purpose: str,
        provider: str,
        model: str,
        reasoning_effort: str,
        web_search_mode: str,
        prompt: str,
        image_paths,
    ) -> str:
        digest = hashlib.sha256()
        for value in (
            CACHE_FORMAT_VERSION,
            purpose,
            provider,
            model,
            reasoning_effort,
            web_search_mode,
            prompt,
        ):
            digest.update(str(value).encode("utf-8"))
            digest.update(b"\0")
        for path in image_paths or []:
            image_path = Path(path)
            digest.update(image_path.name.encode("utf-8"))
            digest.update(b"\0")
            if image_path.exists():
                with image_path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def get(self, key: str):
        if not self.enabled:
            return None
        path = self.directory / f"{key}.json"
        try:
            with self._lock:
                if not path.exists():
                    return None
                payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        response = payload.get("response")
        return response if isinstance(response, dict) else None

    def put(self, key: str, response) -> None:
        if not self.enabled or not isinstance(response, dict):
            return
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            destination = self.directory / f"{key}.json"
            temporary = self.directory / f".{key}.{uuid.uuid4().hex}.tmp"
            temporary.write_text(
                json.dumps(
                    {
                        "format": CACHE_FORMAT_VERSION,
                        "response": response,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
