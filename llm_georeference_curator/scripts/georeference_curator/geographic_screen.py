from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import nearest_points

from .geocoding import haversine_km, parse_decimal


DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "geographic_screen.json"
BOUNDARY_TOLERANCE_KM = 25


def normalized(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(value)).casefold())


@lru_cache(maxsize=1)
def references():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    for item in data["countries"]:
        item["shape"] = shape(item["geometry"])
        item["aliases"] = {normalized(name) for name in item["names"]}
    aliases = {"CHN": ["中国", "中國", "People's Republic of China"], "TWN": ["台灣", "台湾", "Formosa", "Chinese Taipei"],
               "MMR": ["Burma", "Myanmar", "缅甸", "緬甸"], "JPN": ["Japan", "日本"]}
    for item in data["countries"]:
        item["aliases"].update(normalized(name) for name in aliases.get(item["code"], []))
    return data


def country_matches(row):
    keys = {normalized(row.get(key, "")) for key in ("country", "countryCode")} - {""}
    return [item for item in references()["countries"] if keys & item["aliases"]]


def distance_to_geometry(latitude, longitude, geometry):
    point = Point(longitude, latitude)
    if geometry.covers(point):
        return 0.0
    nearest = nearest_points(point, geometry)[1]
    return haversine_km(latitude, longitude, nearest.y, nearest.x)


def geographic_problem(row, latitude, longitude, *, placeholders=False):
    countries = country_matches(row)
    if countries and min(distance_to_geometry(latitude, longitude, item["shape"]) for item in countries) > BOUNDARY_TOLERANCE_KM:
        # Disputed boundaries and absent small islands are not adjudicated by a coarse map.
        occupants = [item for item in references()["countries"] if item["shape"].covers(Point(longitude, latitude))]
        if occupants:
            return "country_conflict: point is well outside the stated country in Natural Earth"
        marine = re.search(r"marine|sea|ocean|seagrass|coastal|island|islet|海|島|岛", str(row.get("habitat", "")) + " " + str(row.get("locality", "")), re.I)
        if not marine:
            return "ocean_or_country_conflict: point is far from the stated country's mapped land"
    state = normalized(row.get("stateProvince", ""))
    codes = {item["code"] for item in countries}
    states = [item for item in references()["admin1"] if item["country"] in codes and
              state and state in {normalized(name) for name in item["names"]}]
    if states:
        def near_bounds(item):
            west, south, east, north = item["bounds"]
            latitude_gap = max(south - latitude, latitude - north, 0) * 110.0
            longitude_gap = max(west - longitude, longitude - east, 0) * 110.0 * abs(math.cos(math.radians(latitude)))
            return math.hypot(latitude_gap, longitude_gap) <= BOUNDARY_TOLERANCE_KM
        if not any(near_bounds(item) for item in states):
            return "state_conflict: point is well outside the stated first-order administrative bounds"
    if placeholders:
        context = " ".join(str(row.get(key, "")) for key in ("georeferenceProtocol", "georeferenceRemarks", "geospatialIssues", "coordinateIssues", "issue"))
        if re.search(r"COUNTRY_COORDINATE_MISMATCH|COORDINATE_COUNTRY_MISMATCH|ZERO_COORDINATE|COUNTRY_CENTROID|CAPITAL_COORDINATE|INSTITUTION_COORDINATE|MUSEUM_COORDINATE|\b(?:country centroid|country capital|institution location|museum location)\b", context, re.I):
            return "source_reported_coordinate_placeholder_or_conflict"
        for item in countries:
            if haversine_km(latitude, longitude, *item["center"]) < 1:
                return "country_centroid_placeholder"
        for item in references()["capitals"]:
            if item["country"] in codes and haversine_km(latitude, longitude, item["latitude"], item["longitude"]) < 0.25:
                if normalized(item["name"]) not in normalized(row.get("locality", "")):
                    return "country_capital_placeholder"
        ilat, ilon = parse_decimal(row.get("institutionLatitude", "")), parse_decimal(row.get("institutionLongitude", ""))
        if ilat is not None and ilon is not None and haversine_km(latitude, longitude, ilat, ilon) < 0.25:
            return "institution_coordinate_placeholder"
    return ""
