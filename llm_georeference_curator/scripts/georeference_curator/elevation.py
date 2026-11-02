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


def parse_elevation_meters(text: str, *, require_context: bool = False):
    if not text:
        return None, None
    normalized = text.replace(",", "")
    if require_context:
        number = r"-?\d+(?:\.\d+)?"
        measurement = rf"{number}\s*(?:{UNIT_PATTERN})?(?:\s*[-–~]\s*{number})?\s*{UNIT_PATTERN}"
        context = re.search(
            rf"(?:\b(?:alt(?:itude|itudo)?|elev(?:ation|atio)?)\.?|海抜|海拔|標高)\s*[:=]?\s*(?:ca\.?\s*)?({measurement})"
            rf"|({measurement})\s*(?:a\.?\s*s\.?\s*l\.?|above\s+sea\s+level)\b",
            normalized, flags=re.I,
        )
        if not context:
            return None, None
        normalized = context.group(1) or context.group(2)
    range_match = re.search(
        rf"(-?\d+(?:\.\d+)?)\s*({UNIT_PATTERN})?\s*[-–~]\s*"
        rf"(-?\d+(?:\.\d+)?)\s*({UNIT_PATTERN})",
        normalized,
        flags=re.I,
    )
    if range_match:
        first_unit = range_match.group(2) or range_match.group(4)
        second_unit = range_match.group(4)
        return tuple(sorted((
            label_meters(range_match.group(1), first_unit),
            label_meters(range_match.group(3), second_unit),
        )))
    match = re.search(
        rf"(?:alt\.?|elev\.?|elevation)?\s*(-?\d+(?:\.\d+)?)\s*({UNIT_PATTERN})",
        normalized,
        flags=re.I,
    )
    if match:
        value = label_meters(match.group(1), match.group(2))
        return value, value
    return None, None


def specimen_elevation(label):
    if label.elevation_text:
        return parse_elevation_meters(label.elevation_text)
    # A distance from a village or river in the full label is not an elevation.
    return parse_elevation_meters(label.label_transcription, require_context=True)
