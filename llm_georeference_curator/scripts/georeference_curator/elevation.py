from __future__ import annotations

import re
import math


UNIT_PATTERN = r"(?:m(?:eters?|etres?)?\.?|ft\.?|feet|foot)(?![A-Za-z])"


def label_meters(value, unit):
    converted = to_meters(value, unit)
    if unit.lower().rstrip(".") in {"ft", "feet", "foot"}:
        return round(converted)
    return int(converted) if converted.is_integer() else converted


def to_meters(value: str, unit: str) -> float:
    numeric = float(value)
    normalized_unit = unit.lower().rstrip(".")
    if normalized_unit in {"ft", "feet", "foot"}:
        return numeric * 0.3048
    return numeric


def round_elevation(value: float, granularity: int = 10) -> int:
    scaled = float(value) / granularity
    rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
    return int(rounded * granularity)


def parse_elevation_meters(text: str):
    if not text:
        return None, None
    normalized = text.replace(",", "")
    range_match = re.search(
        rf"(-?\d+(?:\.\d+)?)\s*({UNIT_PATTERN})?\s*[-–~]\s*"
        rf"(-?\d+(?:\.\d+)?)\s*({UNIT_PATTERN})",
        normalized,
        flags=re.I,
    )
    if range_match:
        first_unit = range_match.group(2) or range_match.group(4)
        second_unit = range_match.group(4)
        return (
            label_meters(range_match.group(1), first_unit),
            label_meters(range_match.group(3), second_unit),
        )
    match = re.search(
        rf"(?:alt\.?|elev\.?|elevation)?\s*(-?\d+(?:\.\d+)?)\s*({UNIT_PATTERN})",
        normalized,
        flags=re.I,
    )
    if match:
        value = label_meters(match.group(1), match.group(2))
        return value, value
    return None, None
