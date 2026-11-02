from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from .geocoding import haversine_km, parse_decimal, valid_lat_lon
from .inputs import read_table


def positive_number(value):
    number = parse_decimal(value)
    return number if number is not None and math.isfinite(number) and number > 0 else None


def canonical_crs(datum: str) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", str(datum or "").upper())
    return {
        "WGS84": "EPSG:4326", "EPSG4326": "EPSG:4326",
        "WGS1984": "EPSG:4326", "WORLDGEODETICSYSTEM1984": "EPSG:4326",
        "TWD67": "EPSG:3821", "TWD97": "EPSG:3824",
        "TOKYO": "EPSG:4301", "TOKYODATUM": "EPSG:4301",
    }.get(compact, f"EPSG:{compact[4:]}" if compact.startswith("EPSG") and compact[4:].isdigit() else "")


@dataclass(frozen=True)
class ConvertedCoordinate:
    latitude: float
    longitude: float
    datum: str
    operation: str
    accuracy_meters: float | None


def to_wgs84(latitude: float, longitude: float, datum: str) -> ConvertedCoordinate:
    crs_name = canonical_crs(datum)
    if not crs_name:
        raise ValueError("Coordinate reference system is missing or unrecognized; no WGS84 assumption made.")
    if crs_name == "EPSG:4326":
        if not valid_lat_lon(latitude, longitude):
            raise ValueError("Coordinates are outside valid geographic ranges.")
        return ConvertedCoordinate(latitude, longitude, "WGS84", "identity EPSG:4326", 0.0)
    try:
        from pyproj import CRS
        from pyproj.aoi import AreaOfInterest
        from pyproj.transformer import TransformerGroup
    except ImportError as exc:
        raise ValueError("pyproj is required for datum conversion; run curator/setup_mac.sh.") from exc
    source = CRS.from_user_input(crs_name)
    if not source.is_geographic:
        raise ValueError("Projected coordinates require explicitly parsed easting/northing; latitude/longitude were not reinterpreted.")
    if not valid_lat_lon(latitude, longitude):
        raise ValueError("Invalid source latitude/longitude.")
    area = source.area_of_use
    if area and not in_bounds(latitude, longitude, area.south, area.west, area.north, area.east):
        raise ValueError("Point is outside the source CRS area of use.")
    group = TransformerGroup(source, "EPSG:4326", always_xy=True, allow_ballpark=False,
                             area_of_interest=AreaOfInterest(longitude - 0.01, latitude - 0.01, longitude + 0.01, latitude + 0.01))
    if not group.best_available or not group.transformers:
        raise ValueError("The best datum transformation requires an unavailable grid; point retained for review.")
    transformer = group.transformers[0]
    lon, lat = transformer.transform(longitude, latitude, errcheck=True)
    if not valid_lat_lon(lat, lon):
        raise ValueError("Datum conversion returned invalid coordinates.")
    accuracy = transformer.accuracy if transformer.accuracy >= 0 else None
    return ConvertedCoordinate(lat, lon, "WGS84", transformer.description, accuracy)


def in_bounds(latitude, longitude, south, west, north, east):
    longitude_inside = west <= longitude <= east if west <= east else longitude >= west or longitude <= east
    return south <= latitude <= north and longitude_inside


def rounding_radius(latitude_text, longitude_text):
    def half_step(value):
        return float(Decimal(10) ** Decimal(str(value)).as_tuple().exponent) * 0.5
    latitude = parse_decimal(latitude_text)
    if latitude is None:
        return None
    try:
        latitude_step = half_step(latitude_text)
        north = 111700 * latitude_step
        east = 111320 * abs(math.cos(math.radians(max(0, abs(latitude) - latitude_step)))) * half_step(longitude_text)
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return max(1, math.ceil(math.hypot(north, east)))


def source_domains(url_text):
    hosts = set()
    for value in str(url_text or "").split("|"):
        parsed = urlparse(value.strip())
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username:
            host = parsed.hostname.lower().removeprefix("www.")
            # Related subdomains are not counted as independent corroboration.
            hosts.add(".".join(host.split(".")[-2:]))
    return hosts


def read_reference_regions(path):
    if not path:
        return []
    rows, _ = read_table(path)
    regions = []
    for row in rows:
        bounds = [parse_decimal(row.get(key, "")) for key in ("south", "west", "north", "east")]
        if any(value is None for value in bounds):
            raise ValueError("Reference region requires south/west/north/east coordinates.")
        south, west, north, east = bounds
        if not (-90 <= south <= north <= 90 and -180 <= west <= 180 and -180 <= east <= 180):
            raise ValueError("Invalid reference region bounds.")
        if not row.get("country") or not row.get("source"):
            raise ValueError("Reference region requires country and source provenance.")
        regions.append(dict(row, bounds=bounds))
    return regions


def region_conflict(row, latitude, longitude, regions):
    country = str(row.get("country", "")).strip().casefold()
    code = str(row.get("countryCode", "")).strip().casefold()
    state = str(row.get("stateProvince", "")).strip().casefold()
    matches = [region for region in regions if str(region["country"]).casefold() in {country, code}]
    state_matches = [region for region in matches if region.get("stateProvince", "").casefold() == state and state]
    matches = state_matches or [region for region in matches if not region.get("stateProvince")]
    if matches and not any(in_bounds(latitude, longitude, *region["bounds"]) for region in matches):
        return "Point lies outside supplied country/state reference bounds: " + " | ".join(region["source"] for region in matches)
    return ""


def documented_original(row):
    text = " ".join(str(row.get(key, "")) for key in ("georeferenceProtocol", "georeferenceSources", "georeferenceRemarks"))
    issues = " ".join(str(row.get(key, "")) for key in ("issue", "geospatialIssues", "coordinateIssues"))
    if issues.strip() or re.search(r"\b(?:not|without|no)\s+(?:a\s+)?(?:GPS|GNSS)\b|\b(?:centroid|approximate|uncertain|placeholder)\b", text, re.I):
        return False
    verified = str(row.get("georeferenceVerificationStatus", "")).strip().casefold() in {"verified", "validated", "verified by human", "verified by curator"}
    return bool(verified or re.search(r"\bGPS\b|\bGNSS\b", text, re.I))


def extent_uncertainty(latitude, longitude, geometry_text):
    try:
        geometry = json.loads(geometry_text) if isinstance(geometry_text, str) else geometry_text
        points = geometry.get("coordinates", [])
        if geometry.get("type") == "bbox":
            south, west, north, east = [float(value) for value in points]
            if not (-90 <= south <= north <= 90 and -180 <= west <= east <= 180):
                return None
            if not in_bounds(latitude, longitude, south, west, north, east):
                return None
            # Meridian-plus-parallel path is a conservative bound for the full box.
            radius = 111700 * max(latitude - south, north - latitude) + 111320 * max(longitude - west, east - longitude)
            return max(1, math.ceil(radius))
        # Unspecified vertices do not establish coverage of a line or polygon.
        return None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
