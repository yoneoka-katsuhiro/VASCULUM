from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .inputs import delimiter_for, first_present


@dataclass
class RawCoordinate:
    latitude: float
    longitude: float
    source_text: str
    source: str
    datum: str = ""
    uncertainty_meters: int = 0
    precision_kind: str = ""


@dataclass
class GazetteerEntry:
    place_name: str
    latitude: str
    longitude: str
    country: str = ""
    state_province: str = ""
    aliases: str = ""
    historical_place_name: str = ""
    uncertainty_meters: str = ""
    elevation_meters: str = ""
    language: str = ""
    source: str = "local_gazetteer"


@dataclass
class GazetteerMatch:
    entry: GazetteerEntry
    score: float
    evidence: str


def parse_decimal(value: str):
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def valid_lat_lon(latitude: float, longitude: float) -> bool:
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def is_precise_label_coordinate(raw: RawCoordinate) -> bool:
    datum = re.sub(r"[^A-Z0-9]", "", (raw.datum or "WGS84").upper())
    if datum not in {"", "WGS84", "EPSG4326"}:
        return False
    if raw.precision_kind == "dms_seconds":
        return True
    decimal_match = re.fullmatch(r"decimal_(\d+)dp", raw.precision_kind or "")
    if decimal_match:
        return int(decimal_match.group(1)) >= 4
    return bool(
        raw.precision_kind == "dms_minutes"
        and 0 < raw.uncertainty_meters <= 500
    )


def decimal_places(value: str) -> int:
    text = str(value).strip()
    match = re.search(r"\.(\d+)", text)
    return len(match.group(1)) if match else 0


def uncertainty_from_precision(latitude_text: str, longitude_text: str) -> int:
    places = min(decimal_places(latitude_text), decimal_places(longitude_text))
    if places <= 0:
        return 111000
    return max(1, int(round(111000 / (10 ** places))))


def uncertainty_from_dms_components(lat_minutes, lat_seconds, lon_minutes, lon_seconds) -> int:
    components = ((lat_minutes, lat_seconds), (lon_minutes, lon_seconds))
    uncertainties = []
    for minutes, seconds in components:
        if seconds is not None:
            uncertainties.append(max(1, int(round(30 / (10 ** decimal_places(seconds))))))
        elif minutes is not None:
            uncertainties.append(max(20, int(round(2000 / (10 ** decimal_places(minutes))))))
        else:
            uncertainties.append(111000)
    return max(uncertainties)


def detect_datum(text: str) -> str:
    upper = text.upper()
    if "TWD67" in upper or "TWD 67" in upper:
        return "TWD67"
    if "TWD97" in upper or "TWD 97" in upper:
        return "TWD97"
    if "WGS84" in upper or "WGS 84" in upper:
        return "WGS84"
    if "TOKYO" in upper and "DATUM" in upper:
        return "Tokyo"
    return ""


def extract_decimal_coordinates(text: str):
    results = []
    if not text:
        return results

    lat_lon_patterns = [
        r"(?:lat(?:itude)?\.?|N)\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\D{0,30}(?:lon(?:gitude)?\.?|long\.?|lng\.?|E)\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)",
        r"(?:lon(?:gitude)?\.?|long\.?|lng\.?|E)\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\D{0,30}(?:lat(?:itude)?\.?|N)\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)",
    ]
    for index, pattern in enumerate(lat_lon_patterns):
        for match in re.finditer(pattern, text, flags=re.I):
            if index == 0:
                lat, lon = float(match.group(1)), float(match.group(2))
            else:
                lon, lat = float(match.group(1)), float(match.group(2))
            if valid_lat_lon(lat, lon):
                source_text = match.group(0)
                latitude_text = match.group(1) if index == 0 else match.group(2)
                longitude_text = match.group(2) if index == 0 else match.group(1)
                places = min(decimal_places(latitude_text), decimal_places(longitude_text))
                results.append(
                    RawCoordinate(
                        lat,
                        lon,
                        source_text,
                        "decimal",
                        detect_datum(source_text),
                        uncertainty_from_precision(latitude_text, longitude_text),
                        f"decimal_{places}dp",
                    )
                )

    decimal_suffix_pattern = re.compile(
        r"(\d{1,2}(?:\.\d+)?)\s*°?\s*([NS])"
        r".{0,40}?"
        r"(\d{1,3}(?:\.\d+)?)\s*°?\s*([EW])",
        flags=re.I | re.S,
    )
    for match in decimal_suffix_pattern.finditer(text):
        latitude_text, latitude_hemisphere = match.group(1), match.group(2)
        longitude_text, longitude_hemisphere = match.group(3), match.group(4)
        latitude = float(latitude_text) * (-1 if latitude_hemisphere.upper() == "S" else 1)
        longitude = float(longitude_text) * (-1 if longitude_hemisphere.upper() == "W" else 1)
        if valid_lat_lon(latitude, longitude):
            places = min(decimal_places(latitude_text), decimal_places(longitude_text))
            results.append(
                RawCoordinate(
                    latitude,
                    longitude,
                    match.group(0),
                    "decimal",
                    detect_datum(match.group(0)),
                    uncertainty_from_precision(latitude_text, longitude_text),
                    f"decimal_{places}dp",
                )
            )

    dms_pattern = re.compile(
        r"([NS])[ \t]*(\d{1,2})"
        r"(?:[ \t]*[°º][ \t]*(\d{1,2}(?:\.\d+)?)"
        r"(?:[ \t]*[′'](?:[ \t]*(\d{1,2}(?:\.\d+)?)[ \t]*[″\"]?)?)?)?"
        r".{0,40}?"
        r"([EW])[ \t]*(\d{1,3})"
        r"(?:[ \t]*[°º][ \t]*(\d{1,2}(?:\.\d+)?)"
        r"(?:[ \t]*[′'](?:[ \t]*(\d{1,2}(?:\.\d+)?)[ \t]*[″\"]?)?)?)?",
        flags=re.I | re.S,
    )
    for match in dms_pattern.finditer(text):
        source_text = match.group(0)
        lat = dms_to_decimal(match.group(2), match.group(3), match.group(4), match.group(1))
        lon = dms_to_decimal(match.group(6), match.group(7), match.group(8), match.group(5))
        if valid_lat_lon(lat, lon):
            context_start = max(0, match.start() - 20)
            context_end = min(len(text), match.end() + 20)
            context = text[context_start:context_end]
            uncertainty = uncertainty_from_dms_components(
                match.group(3), match.group(4), match.group(7), match.group(8)
            )
            precision_kind = (
                "dms_seconds"
                if match.group(4) is not None and match.group(8) is not None
                else "dms_minutes"
                if match.group(3) is not None and match.group(7) is not None
                else "dms_degrees"
            )
            results.append(
                RawCoordinate(
                    lat,
                    lon,
                    source_text,
                    "dms",
                    detect_datum(context),
                    uncertainty,
                    precision_kind,
                )
            )

    dms_suffix_pattern = re.compile(
        r"(\d{1,2})[ \t]*[°º]"
        r"(?:[ \t]*(\d{1,2}(?:\.\d+)?)[ \t]*[′']"
        r"(?:[ \t]*(\d{1,2}(?:\.\d+)?)[ \t]*[″\"]?)?)?"
        r"[ \t]*([NS])"
        r".{0,40}?"
        r"(\d{1,3})[ \t]*[°º]"
        r"(?:[ \t]*(\d{1,2}(?:\.\d+)?)[ \t]*[′']"
        r"(?:[ \t]*(\d{1,2}(?:\.\d+)?)[ \t]*[″\"]?)?)?"
        r"[ \t]*([EW])",
        flags=re.I | re.S,
    )
    for match in dms_suffix_pattern.finditer(text):
        source_text = match.group(0)
        lat = dms_to_decimal(match.group(1), match.group(2), match.group(3), match.group(4))
        lon = dms_to_decimal(match.group(5), match.group(6), match.group(7), match.group(8))
        if valid_lat_lon(lat, lon):
            context_start = max(0, match.start() - 20)
            context_end = min(len(text), match.end() + 20)
            context = text[context_start:context_end]
            uncertainty = uncertainty_from_dms_components(
                match.group(2), match.group(3), match.group(6), match.group(7)
            )
            precision_kind = (
                "dms_seconds"
                if match.group(3) is not None and match.group(7) is not None
                else "dms_minutes"
                if match.group(2) is not None and match.group(6) is not None
                else "dms_degrees"
            )
            results.append(
                RawCoordinate(
                    lat,
                    lon,
                    source_text,
                    "dms",
                    detect_datum(context),
                    uncertainty,
                    precision_kind,
                )
            )

    unique = []
    seen = set()
    for raw in results:
        key = (round(raw.latitude, 8), round(raw.longitude, 8), raw.source)
        if key not in seen:
            seen.add(key)
            unique.append(raw)
    return unique


def dms_to_decimal(degrees, minutes, seconds, hemisphere) -> float:
    value = float(degrees)
    if minutes:
        value += float(minutes) / 60.0
    if seconds:
        value += float(seconds) / 3600.0
    if hemisphere.upper() in {"S", "W"}:
        value *= -1
    return value


def read_gazetteer(path):
    if not path:
        return []
    import csv

    delimiter = delimiter_for(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        entries = []
        for row in reader:
            latitude = first_present(row, "decimalLatitude", "latitude", "lat")
            longitude = first_present(row, "decimalLongitude", "longitude", "lon", "lng")
            place_name = first_present(row, "placeName", "name", "locality")
            if not place_name or parse_decimal(latitude) is None or parse_decimal(longitude) is None:
                continue
            entries.append(
                GazetteerEntry(
                    place_name=place_name,
                    latitude=latitude,
                    longitude=longitude,
                    country=first_present(row, "country"),
                    state_province=first_present(row, "stateProvince", "state", "province"),
                    aliases=first_present(row, "aliases", "alias"),
                    historical_place_name=first_present(row, "historicalPlaceName", "historicalName"),
                    uncertainty_meters=first_present(row, "uncertaintyMeters", "coordinateUncertaintyInMeters"),
                    elevation_meters=first_present(row, "elevationMeters", "elevation"),
                    language=first_present(row, "language"),
                    source=first_present(row, "source") or "local_gazetteer",
                )
            )
        return entries


def match_gazetteer(row, label_text: str, entries):
    if not entries:
        return []
    haystack = " ".join(
        [
            row.get("country", ""),
            row.get("stateProvince", ""),
            row.get("county", ""),
            row.get("municipality", ""),
            row.get("locality", ""),
            row.get("verbatimLocality", ""),
            label_text or "",
        ]
    ).lower()
    country = row.get("country", "").lower()
    state = row.get("stateProvince", "").lower()
    matches = []
    for entry in entries:
        if entry.country and country and entry.country.lower() not in country and country not in entry.country.lower():
            continue
        if entry.state_province and state and entry.state_province.lower() not in state and state not in entry.state_province.lower():
            continue
        names = [entry.place_name, entry.historical_place_name]
        names.extend([piece.strip() for piece in entry.aliases.split("|")])
        score = 0.0
        evidence_names = []
        for name in names:
            if not name:
                continue
            if name.lower() in haystack:
                evidence_names.append(name)
                score = max(score, min(0.95, 0.55 + len(name) / 40.0))
        if evidence_names:
            matches.append(
                GazetteerMatch(
                    entry=entry,
                    score=score,
                    evidence="Gazetteer name matched locality text: " + ", ".join(evidence_names),
                )
            )
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches[:5]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
