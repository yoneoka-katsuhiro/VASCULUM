from __future__ import annotations

import json
import logging
import platform
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path


class RunLogger:
    """Write compact JSON events that are easy to inspect or share for diagnosis."""

    def __init__(self, output_dir: Path, version: str) -> None:
        log_dir = output_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        self.path = log_dir / f"run_{stamp}.log"
        self._logger = logging.getLogger(f"specimen_collector.{stamp}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = logging.FileHandler(self.path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s%(msecs)03d %(levelname)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S.",
            )
        )
        self._logger.addHandler(handler)
        self._handler = handler
        self.event(
            "INFO",
            "runtime",
            version=version,
            python=sys.version.split()[0],
            platform=platform.platform(),
        )

    @staticmethod
    def _clean(value: object) -> object:
        if isinstance(value, str):
            return re.sub(
                (
                    r"([?&](?:api_key|apikey|key|token|access_token|"
                    r"password|client_secret)=)[^&\s)]+"
                ),
                r"\1REDACTED",
                value,
                flags=re.IGNORECASE,
            )
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [RunLogger._clean(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): RunLogger._clean(item)
                for key, item in value.items()
            }
        return str(value)

    def event(self, level: str, event: str, **fields: object) -> None:
        payload = {"event": event}
        payload.update(
            {key: self._clean(value) for key, value in fields.items()}
        )
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        self._logger.log(
            numeric_level,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )

    def exception(self, event: str, exc: BaseException, **fields: object) -> None:
        fields["error_type"] = type(exc).__name__
        fields["error"] = str(exc)
        fields["traceback"] = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        self.event("ERROR", event, **fields)

    def close(self) -> None:
        self._handler.flush()
        self._handler.close()
        self._logger.removeHandler(self._handler)
