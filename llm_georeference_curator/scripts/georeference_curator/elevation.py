from __future__ import annotations

import re


def parse_elevation_meters(text: str):
    if not text:
        return None, None
    normalized = text.replace(",", "")
    range_match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*(?:m|meters?)?\s*[-–~]\s*(-?\d+(?:\.\d+)?)\s*(?:m|meters?)",
        normalized,
        flags=re.I,
    )
    if range_match:
        return round(float(range_match.group(1))), round(float(range_match.group(2)))
    match = re.search(r"(?:alt\.?|elev\.?|elevation)?\s*(-?\d+(?:\.\d+)?)\s*(?:m|meters?)", normalized, flags=re.I)
    if match:
        value = round(float(match.group(1)))
        return value, value
    return None, None
