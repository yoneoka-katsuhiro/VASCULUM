from __future__ import annotations

import math
import re
import unicodedata

from .models import EvidenceRecord, SearchArea, clean_text


COUNTRY_ALIASES = {
    "argentina": "AR",
    "australia": "AU",
    "brazil": "BR",
    "canada": "CA",
    "china": "CN",
    "chinese peoples republic": "CN",
    "people s republic of china": "CN",
    "peoples republic of china": "CN",
    "france": "FR",
    "germany": "DE",
    "india": "IN",
    "indonesia": "ID",
    "japan": "JP",
    "korea": "KR",
    "republic of korea": "KR",
    "south korea": "KR",
    "malaysia": "MY",
    "mexico": "MX",
    "new zealand": "NZ",
    "philippines": "PH",
    "singapore": "SG",
    "south africa": "ZA",
    "taiwan": "TW",
    "thailand": "TH",
    "uk": "GB",
    "united kingdom": "GB",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "vietnam": "VN",
}


def normalized_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value)).casefold()
    text = "".join(char if char.isalnum() else " " for char in text)
    return re.sub(r"\s+", " ", text).strip()


def country_code_for(value: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if re.fullmatch(r"[A-Za-z]{2}", text):
        return text.upper()
    return COUNTRY_ALIASES.get(normalized_text(text), text)


def canonical_country_code(value: str, explicit_code: str = "") -> str:
    code = clean_text(explicit_code)
    if re.fullmatch(r"[A-Za-z]{2}", code):
        return code.upper()
    normalized = country_code_for(value)
    if re.fullmatch(r"[A-Z]{2}", normalized):
        return normalized
    return ""


def destination_point(
    latitude: float,
    longitude: float,
    bearing_degrees: float,
    distance_km: float,
) -> tuple[float, float]:
    radius_km = 6371.0088
    angular_distance = distance_km / radius_km
    bearing = math.radians(bearing_degrees)
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    lon_degrees = (math.degrees(lon2) + 540) % 360 - 180
    return math.degrees(lat2), lon_degrees


def circle_wkt(latitude: float, longitude: float, radius_km: float, segments: int = 72) -> str:
    points = []
    for index in range(segments):
        lat, lon = destination_point(latitude, longitude, -index * 360 / segments, radius_km)
        points.append((lon, lat))
    points.append(points[0])
    coordinates = ", ".join(f"{lon:.6f} {lat:.6f}" for lon, lat in points)
    return f"POLYGON(({coordinates}))"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def record_matches_area(record: EvidenceRecord, area: SearchArea) -> bool:
    if area.country:
        expected_code = canonical_country_code(area.country) or country_code_for(area.country)
        record_country = clean_text(record.country)
        record_code = canonical_country_code(record_country, record_country) or canonical_country_code(record.raw_country)
        if len(expected_code) == 2 and re.fullmatch(r"[A-Z]{2}", expected_code):
            if record_code != expected_code and normalized_text(record_country) != normalized_text(area.country):
                return False
        elif normalized_text(expected_code) not in normalized_text(record_country):
            return False

    place_text = " ".join(
        (
            record.state_province,
            record.locality,
            record.country,
        )
    )
    if area.state and normalized_text(area.state) not in normalized_text(place_text):
        return False
    if area.prefecture and normalized_text(area.prefecture) not in normalized_text(place_text):
        return False

    if area.latitude is not None and area.longitude is not None and area.radius_km is not None:
        if not record.decimal_latitude or not record.decimal_longitude:
            return False
        try:
            lat = float(record.decimal_latitude)
            lon = float(record.decimal_longitude)
        except ValueError:
            return False
        if distance_km(area.latitude, area.longitude, lat, lon) > area.radius_km:
            return False
    return True
