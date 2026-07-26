from __future__ import annotations

import re


def safe_token(value: object, fallback: str = "unknown") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("_")
    return text[:140] or fallback
