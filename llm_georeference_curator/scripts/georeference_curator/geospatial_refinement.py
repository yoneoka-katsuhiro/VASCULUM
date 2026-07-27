from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .elevation import parse_elevation_meters, round_elevation
from .geocoding import haversine_km
from .habitat import HabitatPreference
from .models import CoordinateCandidate, LabelRead


DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
DEFAULT_ELEVATION_ENDPOINT = "https://api.open-meteo.com/v1/elevation"
OPEN_METEO_DOCUMENTATION = "https://open-meteo.com/en/docs/elevation-api"
OPENSTREETMAP_COPYRIGHT = "https://www.openstreetmap.org/copyright"
COPERNICUS_DEM_DOI = "https://doi.org/10.5270/ESA-c5d3d65"
MAX_REFINEMENT_RADIUS_METERS = 5000
MAX_ELEVATION_POINTS = 100
DEM_RESOLUTION_METERS = 90


@dataclass(frozen=True)
class RoutePoint:
    latitude: float
    longitude: float
    way_id: int
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EnvironmentalFeature:
    osm_type: str
    osm_id: int
    tags: Dict[str, str] = field(default_factory=dict)
    geometry: Tuple[Tuple[float, float], ...] = ()
    classes: Tuple[str, ...] = ()


@dataclass
class GeospatialContext:
    routes: List[RoutePoint] = field(default_factory=list)
    environment: List[EnvironmentalFeature] = field(default_factory=list)


@dataclass
class GeospatialRefinementSettings:
    enabled: bool = True
    use_routes: bool = True
    overpass_endpoint: str = field(
        default_factory=lambda: os.environ.get(
            "OVERPASS_API_ENDPOINT", DEFAULT_OVERPASS_ENDPOINT
        )
    )
    elevation_endpoint: str = field(
        default_factory=lambda: os.environ.get(
            "OPEN_METEO_ELEVATION_ENDPOINT", DEFAULT_ELEVATION_ENDPOINT
        )
    )
    timeout_seconds: int = 600
    deadline_monotonic: float = 0.0
    user_agent: str = "VASCULUM-llm-georeference-curator/0.1"


def remaining_request_timeout(settings: GeospatialRefinementSettings) -> float:
    timeout = float(max(1, settings.timeout_seconds))
    if settings.deadline_monotonic:
        remaining = settings.deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Coordinate verification exceeded its stage time limit.")
        timeout = min(timeout, remaining)
    return max(0.1, timeout)


@dataclass
class GeospatialRefinementCache:
    contexts: Dict[Tuple[float, float, int], GeospatialContext] = field(
        default_factory=dict
    )
    elevations: Dict[Tuple[float, float], float] = field(default_factory=dict)


@dataclass
class RefinementOutcome:
    attempted: bool = False
    candidate: Optional[CoordinateCandidate] = None
    rejected_anchor: bool = False
    warning: str = ""


ContextFetcher = Callable[
    [float, float, int, GeospatialRefinementSettings], GeospatialContext
]
ElevationFetcher = Callable[
    [Sequence[RoutePoint], GeospatialRefinementSettings], List[Optional[float]]
]


def number_or(value, fallback: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def select_refinement_anchor(
    candidates: Sequence[CoordinateCandidate],
    validate_environment_only: bool = False,
) -> Optional[CoordinateCandidate]:
    eligible = []
    for candidate in candidates:
        if not candidate.candidate_source.startswith("llm:"):
            continue
        if not candidate.source_urls:
            continue
        route_evidence = has_route_evidence(candidate)
        if candidate.candidate_type == "place_centroid" and route_evidence:
            type_priority = 3
        elif candidate.candidate_type == "refined_georeference" and route_evidence:
            type_priority = 2
        elif validate_environment_only and candidate.candidate_type == "refined_georeference":
            type_priority = 1
        elif validate_environment_only and candidate.candidate_type == "place_centroid":
            type_priority = 0
        else:
            continue
        try:
            latitude = float(candidate.candidate_latitude)
            longitude = float(candidate.candidate_longitude)
        except (TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        eligible.append(
            (
                type_priority,
                number_or(candidate.score, 0.0),
                -number_or(candidate.candidate_uncertainty_meters, float("inf")),
                candidate,
            )
        )
    if not eligible:
        return None
    return max(eligible, key=lambda item: item[:3])[-1]


def has_route_evidence(candidate: CoordinateCandidate) -> bool:
    return bool(
        re.search(
            r"\b(?:trail|path|road|route|track|railway|rail)\b|登山|步道|林道|古道",
            (
                f"{candidate.modern_place_name} {candidate.historical_place_name} "
                f"{candidate.evidence_layers} {candidate.evidence}"
            ),
            flags=re.I,
        )
    )


def append_audit_value(existing: str, value: str) -> str:
    values = [item.strip() for item in str(existing or "").split("|") if item.strip()]
    values.append(value)
    return " | ".join(dict.fromkeys(values))


def refinement_radius_meters(candidate: CoordinateCandidate) -> int:
    uncertainty = number_or(candidate.candidate_uncertainty_meters, 2000.0)
    if not math.isfinite(uncertainty) or uncertainty <= 0:
        uncertainty = 2000.0
    return int(round(min(MAX_REFINEMENT_RADIUS_METERS, max(500.0, uncertainty))))


def fetch_osm_geospatial_context(
    latitude: float,
    longitude: float,
    radius_meters: int,
    settings: GeospatialRefinementSettings,
) -> GeospatialContext:
    query = f"""
[out:json][timeout:30];
(
  way(around:{radius_meters},{latitude:.7f},{longitude:.7f})
    [\"highway\"~\"^(path|track|footway|bridleway|steps|service|unclassified)$\"];
  way(around:{radius_meters},{latitude:.7f},{longitude:.7f})
    [\"railway\"~\"^(narrow_gauge|abandoned|disused)$\"];
)->.routes;
(
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})
    [\"landuse\"~\"^(forest|residential|commercial|industrial|retail|farmland|farmyard|meadow|orchard|vineyard|plant_nursery|quarry|salt_pond|reservoir|basin)$\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})
    [\"natural\"~\"^(wood|scrub|heath|grassland|wetland|water|beach|sand|desert|dune|bare_rock|scree|cliff|coastline|spring|hot_spring|geyser|cave_entrance|volcano|glacier|tundra)$\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})
    [\"waterway\"~\"^(river|stream|canal|drain|ditch|waterfall)$\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})[\"water\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})
    [\"place\"~\"^(city|town|village|suburb|neighbourhood|quarter|hamlet)$\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})[\"wetland\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})[\"estuary\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})[\"tidal\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})[\"geological\"];
  nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})
    [\"surface\"~\"^(limestone|serpentine|rock|bare_rock|sand)$\"];
)->.environment;
.routes out tags geom;
.environment out tags geom;
""".strip()
    body = urlencode({"data": query}).encode("utf-8")
    request = Request(
        settings.overpass_endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": settings.user_agent,
        },
    )
    with urlopen(request, timeout=remaining_request_timeout(settings)) as response:
        payload = json.loads(response.read().decode("utf-8"))

    points = []
    environment = []
    for element in payload.get("elements", []):
        osm_type = str(element.get("type", ""))
        osm_id = int(element.get("id", 0))
        tags = {
            str(key): str(value)
            for key, value in (element.get("tags") or {}).items()
        }
        geometry = parse_element_geometry(element)
        if osm_type == "way" and (
            tags.get("highway")
            in {"path", "track", "footway", "bridleway", "steps", "service", "unclassified"}
            or tags.get("railway") in {"narrow_gauge", "abandoned", "disused"}
        ):
            for point_latitude, point_longitude in geometry:
                if (
                    haversine_km(
                        latitude,
                        longitude,
                        point_latitude,
                        point_longitude,
                    )
                    * 1000
                    <= radius_meters
                ):
                    points.append(
                        RoutePoint(
                            latitude=point_latitude,
                            longitude=point_longitude,
                            way_id=osm_id,
                            tags=tags,
                        )
                    )
        classes = classify_environment_tags(tags)
        if classes and geometry:
            environment.append(
                EnvironmentalFeature(
                    osm_type=osm_type,
                    osm_id=osm_id,
                    tags=tags,
                    geometry=tuple(geometry),
                    classes=classes,
                )
            )
    return GeospatialContext(
        routes=deduplicate_route_points(points),
        environment=deduplicate_environment_features(environment),
    )


def parse_element_geometry(element) -> List[Tuple[float, float]]:
    coordinates = []
    if element.get("lat") is not None and element.get("lon") is not None:
        try:
            coordinates.append((float(element["lat"]), float(element["lon"])))
        except (TypeError, ValueError):
            pass
    for coordinate in element.get("geometry") or []:
        if not isinstance(coordinate, dict):
            continue
        try:
            coordinates.append((float(coordinate["lat"]), float(coordinate["lon"])))
        except (KeyError, TypeError, ValueError):
            continue
    center = element.get("center") or {}
    if not coordinates and center.get("lat") is not None and center.get("lon") is not None:
        try:
            coordinates.append((float(center["lat"]), float(center["lon"])))
        except (TypeError, ValueError):
            pass
    return coordinates


def classify_environment_tags(tags: Dict[str, str]) -> Tuple[str, ...]:
    classes = []
    landuse = tags.get("landuse", "").casefold()
    natural = tags.get("natural", "").casefold()
    waterway = tags.get("waterway", "").casefold()
    water = tags.get("water", "").casefold()
    place = tags.get("place", "").casefold()
    wetland = tags.get("wetland", "").casefold()
    estuary = tags.get("estuary", "").casefold()
    tidal = tags.get("tidal", "").casefold()
    geological = " ".join(
        (
            tags.get("geological", ""),
            tags.get("rock", ""),
            tags.get("surface", ""),
            tags.get("description", ""),
        )
    ).casefold()

    if landuse in {"residential", "commercial", "industrial", "retail"} or place in {
        "city", "town", "village", "suburb", "neighbourhood", "quarter", "hamlet"
    }:
        classes.append("built_up")
    if landuse in {"forest", "plantation"} or natural == "wood":
        classes.append("forest")
    if natural == "scrub":
        classes.append("shrubland")
    if natural == "heath":
        classes.extend(("heath", "shrubland"))
    if natural == "grassland" or landuse == "meadow":
        classes.append("grassland")
    if landuse in {"farmland", "farmyard", "orchard", "vineyard", "plant_nursery"}:
        classes.append("agriculture")
    if natural == "wetland" or wetland:
        classes.append("wetland")
        if wetland in {"bog", "fen", "peat_bog"}:
            classes.append("peatland")
        if wetland == "mangrove":
            classes.append("mangrove")
    if waterway:
        classes.extend(("river", "water"))
        if waterway == "waterfall":
            classes.append("waterfall")
    if natural == "spring":
        classes.extend(("spring", "water"))
    if natural in {"hot_spring", "geyser"}:
        classes.extend(("geothermal", "spring", "water"))
    if natural == "water" or water or landuse in {"reservoir", "basin", "salt_pond"}:
        classes.append("water")
        if water in {"lake", "pond", "reservoir", "basin"} or landuse in {"reservoir", "basin"}:
            classes.append("standing_water")
        if water in {"river", "stream"}:
            classes.append("river")
    if natural == "coastline":
        classes.extend(("coast", "marine"))
    if estuary not in {"", "no"}:
        classes.extend(("estuary", "coast", "water"))
    if tidal not in {"", "no"}:
        classes.extend(("coast", "marine"))
    if natural == "beach":
        classes.extend(("beach", "coast", "sand"))
    if natural == "sand" or tags.get("surface") == "sand":
        classes.append("sand")
    if natural in {"desert", "dune"}:
        classes.extend(("desert", "sand"))
    if natural == "glacier":
        classes.append("snow_ice")
    if natural == "tundra":
        classes.extend(("moss_lichen", "shrubland", "grassland"))
    if natural in {"bare_rock", "scree", "cliff"} or landuse == "quarry":
        classes.append("bare_rock")
    if natural == "scree":
        classes.append("scree")
    if natural == "cliff":
        classes.append("cliff")
    if natural == "cave_entrance":
        classes.append("cave")
    if natural == "volcano" or "volcan" in geological or "lava" in geological:
        classes.append("volcanic")
    if any(value in geological for value in ("limestone", "calcareous")):
        classes.append("limestone")
    if "karst" in geological:
        classes.append("karst")
    if any(value in geological for value in ("ultramafic", "serpentine", "serpentinite")):
        classes.append("ultramafic")
    if "gypsum" in geological:
        classes.append("gypsum")
    if landuse == "salt_pond" or any(value in geological for value in ("saline", "salt")):
        classes.append("saline")
    return tuple(dict.fromkeys(classes))


def deduplicate_environment_features(
    features: Sequence[EnvironmentalFeature],
) -> List[EnvironmentalFeature]:
    unique = {}
    for feature in features:
        unique[(feature.osm_type, feature.osm_id)] = feature
    return list(unique.values())


def fetch_osm_route_points(
    latitude: float,
    longitude: float,
    radius_meters: int,
    settings: GeospatialRefinementSettings,
) -> List[RoutePoint]:
    return fetch_osm_geospatial_context(
        latitude, longitude, radius_meters, settings
    ).routes


def deduplicate_route_points(points: Sequence[RoutePoint]) -> List[RoutePoint]:
    unique = {}
    for point in points:
        key = (round(point.latitude, 7), round(point.longitude, 7))
        existing = unique.get(key)
        if existing is None or route_name_score(point.tags) > route_name_score(
            existing.tags
        ):
            unique[key] = point
    return list(unique.values())


def choose_elevation_sample(
    points: Sequence[RoutePoint],
    anchor_latitude: float,
    anchor_longitude: float,
    maximum: int = MAX_ELEVATION_POINTS,
) -> List[RoutePoint]:
    ordered = sorted(
        deduplicate_route_points(points),
        key=lambda point: haversine_km(
            anchor_latitude,
            anchor_longitude,
            point.latitude,
            point.longitude,
        ),
    )
    if len(ordered) <= maximum:
        return ordered

    nearest_count = min(60, maximum)
    selected = list(ordered[:nearest_count])
    remaining_slots = maximum - len(selected)
    tail = ordered[nearest_count:]
    if remaining_slots > 0 and tail:
        for index in range(remaining_slots):
            position = round(index * (len(tail) - 1) / max(1, remaining_slots - 1))
            selected.append(tail[position])
    return deduplicate_route_points(selected)[:maximum]


def fetch_open_meteo_elevations(
    points: Sequence[RoutePoint],
    settings: GeospatialRefinementSettings,
) -> List[Optional[float]]:
    if not points:
        return []
    query = urlencode(
        {
            "latitude": ",".join(f"{point.latitude:.7f}" for point in points),
            "longitude": ",".join(f"{point.longitude:.7f}" for point in points),
        }
    )
    request = Request(
        f"{settings.elevation_endpoint}?{query}",
        headers={"Accept": "application/json", "User-Agent": settings.user_agent},
    )
    with urlopen(request, timeout=remaining_request_timeout(settings)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw = payload.get("elevation")
    if not isinstance(raw, list) or len(raw) != len(points):
        raise ValueError("Elevation API returned an unexpected response shape.")
    values = []
    for value in raw:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            values.append(None)
    return values


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for generic in (
        "forest bathing trail",
        "hiking trail",
        "mountain trail",
        "trail",
        "road",
        "track",
        "登山步道",
        "森林浴步道",
        "步道",
        "登山道",
        "林道",
        "古道",
    ):
        text = text.replace(generic, " ")
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def route_names(tags: Dict[str, str]) -> List[str]:
    return [
        value
        for key, value in tags.items()
        if key in {"name", "old_name", "alt_name"} or key.startswith("name:")
        if value
    ]


def name_similarity(route_tags: Dict[str, str], locality_context: str) -> float:
    context = normalize_name(locality_context)
    if not context:
        return 0.0
    best = 0.0
    context_tokens = {token for token in context.split() if len(token) >= 3}
    context_cjk = set(re.findall(r"[\u3400-\u9fff]{2}", context))
    for raw_name in route_names(route_tags):
        name = normalize_name(raw_name)
        if not name:
            continue
        if len(name) >= 2 and (name in context or context in name):
            best = max(best, 1.0)
        tokens = {token for token in name.split() if len(token) >= 3}
        if tokens and context_tokens:
            best = max(best, len(tokens & context_tokens) / len(tokens | context_tokens))
        name_cjk = set(re.findall(r"[\u3400-\u9fff]{2}", name))
        if name_cjk and context_cjk:
            best = max(best, len(name_cjk & context_cjk) / len(name_cjk | context_cjk))
    return min(1.0, best)


def route_name_score(tags: Dict[str, str]) -> int:
    return 1 if route_names(tags) else 0


def route_class_score(tags: Dict[str, str]) -> float:
    highway = tags.get("highway", "")
    railway = tags.get("railway", "")
    if highway in {"path", "footway", "track", "bridleway"}:
        return 1.0
    if railway in {"narrow_gauge", "abandoned", "disused"}:
        return 0.85
    if highway in {"steps", "service", "unclassified"}:
        return 0.70
    return 0.50


def point_in_polygon(
    latitude: float,
    longitude: float,
    geometry: Sequence[Tuple[float, float]],
) -> bool:
    if len(geometry) < 4 or geometry[0] != geometry[-1]:
        return False
    inside = False
    previous_latitude, previous_longitude = geometry[-1]
    for current_latitude, current_longitude in geometry:
        crosses = (current_latitude > latitude) != (previous_latitude > latitude)
        if crosses:
            crossing_longitude = (
                (previous_longitude - current_longitude)
                * (latitude - current_latitude)
                / (previous_latitude - current_latitude)
                + current_longitude
            )
            if longitude < crossing_longitude:
                inside = not inside
        previous_latitude, previous_longitude = current_latitude, current_longitude
    return inside


def point_to_segment_meters(
    latitude: float,
    longitude: float,
    start: Tuple[float, float],
    end: Tuple[float, float],
) -> float:
    latitude_scale = 110540.0
    longitude_scale = 111320.0 * math.cos(math.radians(latitude))
    start_x = (start[1] - longitude) * longitude_scale
    start_y = (start[0] - latitude) * latitude_scale
    end_x = (end[1] - longitude) * longitude_scale
    end_y = (end[0] - latitude) * latitude_scale
    segment_x = end_x - start_x
    segment_y = end_y - start_y
    denominator = segment_x * segment_x + segment_y * segment_y
    if denominator <= 0:
        return math.hypot(start_x, start_y)
    position = max(
        0.0,
        min(1.0, -(start_x * segment_x + start_y * segment_y) / denominator),
    )
    return math.hypot(
        start_x + position * segment_x,
        start_y + position * segment_y,
    )


def distance_to_environment_feature(
    latitude: float,
    longitude: float,
    feature: EnvironmentalFeature,
) -> float:
    geometry = feature.geometry
    if not geometry:
        return float("inf")
    if point_in_polygon(latitude, longitude, geometry):
        return 0.0
    if len(geometry) == 1:
        return (
            haversine_km(
                latitude,
                longitude,
                geometry[0][0],
                geometry[0][1],
            )
            * 1000.0
        )
    return min(
        point_to_segment_meters(latitude, longitude, start, end)
        for start, end in zip(geometry, geometry[1:])
    )


def nearest_environment_distance(
    latitude: float,
    longitude: float,
    classes: Sequence[str],
    features: Sequence[EnvironmentalFeature],
) -> Tuple[float, str]:
    wanted = set(classes)
    nearest_distance = float("inf")
    nearest_class = ""
    for feature in features:
        matching = wanted.intersection(feature.classes)
        if not matching:
            continue
        distance = distance_to_environment_feature(latitude, longitude, feature)
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_class = sorted(matching)[0]
    return nearest_distance, nearest_class


@dataclass(frozen=True)
class HabitatEvaluation:
    score: float = 0.5
    hard_conflict: bool = False
    note: str = "habitat prior not supplied"


def evaluate_habitat_compatibility(
    preference: HabitatPreference,
    point: RoutePoint,
    elevation: float,
    features: Sequence[EnvironmentalFeature],
) -> HabitatEvaluation:
    if not preference.enabled:
        return HabitatEvaluation()
    if not preference.canonical:
        return HabitatEvaluation(
            note=(
                "custom habitat prior retained for LLM research; no controlled "
                "post-processing rule was available"
            )
        )

    expected_distance, expected_class = nearest_environment_distance(
        point.latitude,
        point.longitude,
        preference.expected_classes,
        features,
    )
    avoided_distance, avoided_class = nearest_environment_distance(
        point.latitude,
        point.longitude,
        preference.avoided_classes,
        features,
    )
    built_up_distance, _built_up_class = nearest_environment_distance(
        point.latitude,
        point.longitude,
        ("built_up",),
        features,
    )

    if math.isfinite(expected_distance):
        expected_score = math.exp(-expected_distance / 800.0)
        expected_note = f"nearest {expected_class} map feature {expected_distance:.0f} m"
    else:
        expected_score = 0.5
        expected_note = "expected habitat class not mapped nearby (unknown)"

    if math.isfinite(avoided_distance):
        avoided_score = 1.0 - math.exp(-avoided_distance / 300.0)
        avoided_note = f"nearest conflicting {avoided_class} feature {avoided_distance:.0f} m"
    else:
        avoided_score = 1.0
        avoided_note = "no mapped conflicting class nearby"

    elevation_score = 1.0
    elevation_notes = []
    floor = preference.elevation_floor_meters
    ceiling = preference.elevation_ceiling_meters
    if floor and elevation < floor:
        elevation_score *= math.exp(-(floor - elevation) / 300.0)
        elevation_notes.append(f"below soft habitat floor {floor} m")
    if ceiling and elevation > ceiling:
        elevation_score *= math.exp(-(elevation - ceiling) / 100.0)
        elevation_notes.append(f"above soft habitat ceiling {ceiling} m")

    hard_conflict = False
    if ceiling and elevation > ceiling + max(100, ceiling):
        hard_conflict = True
    expected_is_close = math.isfinite(expected_distance) and expected_distance <= 250
    if (
        preference.wilderness
        and built_up_distance <= 150
        and not expected_is_close
    ):
        hard_conflict = True
    if (
        floor
        and elevation < max(0, floor - 500)
        and built_up_distance <= 500
    ):
        hard_conflict = True

    score = 0.55 * expected_score + 0.25 * elevation_score + 0.20 * avoided_score
    note_parts = [
        f"habitat={preference.display}",
        expected_note,
        avoided_note,
    ]
    note_parts.extend(elevation_notes)
    if hard_conflict:
        note_parts.append("hard ecological contradiction")
    return HabitatEvaluation(
        score=max(0.0, min(1.0, score)),
        hard_conflict=hard_conflict,
        note="; ".join(note_parts),
    )


def target_elevation(
    anchor: CoordinateCandidate, label: LabelRead
) -> Optional[float]:
    direct = number_or(anchor.candidate_elevation_meters, float("nan"))
    if math.isfinite(direct):
        return direct
    minimum, maximum = parse_elevation_meters(
        label.elevation_text or label.label_transcription
    )
    if minimum is None or maximum is None:
        return None
    return (minimum + maximum) / 2.0


def score_route_points(
    anchor: CoordinateCandidate,
    label: LabelRead,
    points: Sequence[RoutePoint],
    elevations: Sequence[Optional[float]],
    radius_meters: int,
    habitat_preference: HabitatPreference,
    environment: Sequence[EnvironmentalFeature],
) -> Optional[Tuple[RoutePoint, float, float, int, str, str]]:
    if len(points) != len(elevations) or not points:
        return None
    anchor_latitude = float(anchor.candidate_latitude)
    anchor_longitude = float(anchor.candidate_longitude)
    context = " ".join(
        (
            anchor.modern_place_name,
            anchor.historical_place_name,
            label.locality_text,
            label.label_transcription,
        )
    )
    elevation_target = target_elevation(anchor, label)
    scored = []
    for point, elevation in zip(points, elevations):
        if elevation is None:
            continue
        distance_meters = haversine_km(
            anchor_latitude,
            anchor_longitude,
            point.latitude,
            point.longitude,
        ) * 1000.0
        distance_score = math.exp(
            -distance_meters / max(250.0, radius_meters / 2.0)
        )
        name_score = name_similarity(point.tags, context)
        class_score = route_class_score(point.tags)
        habitat = evaluate_habitat_compatibility(
            habitat_preference,
            point,
            elevation,
            environment,
        )
        if habitat.hard_conflict:
            continue
        if elevation_target is not None:
            tolerance = max(120.0, min(300.0, abs(elevation_target) * 0.08))
            elevation_delta = abs(elevation - elevation_target)
            if elevation_delta > max(500.0, tolerance * 2.5):
                continue
            elevation_score = math.exp(-elevation_delta / tolerance)
            if habitat_preference.canonical:
                point_score = (
                    0.38 * elevation_score
                    + 0.18 * distance_score
                    + 0.10 * name_score
                    + 0.08 * class_score
                    + 0.26 * habitat.score
                )
            else:
                point_score = (
                    0.52 * elevation_score
                    + 0.25 * distance_score
                    + 0.13 * name_score
                    + 0.10 * class_score
                )
        else:
            if name_score < 0.35 and (
                not habitat_preference.canonical or habitat.score < 0.65
            ):
                continue
            elevation_delta = float("nan")
            if habitat_preference.canonical:
                point_score = (
                    0.45 * distance_score
                    + 0.20 * name_score
                    + 0.10 * class_score
                    + 0.25 * habitat.score
                )
            else:
                point_score = (
                    0.55 * distance_score + 0.35 * name_score + 0.10 * class_score
                )
        scored.append(
            (
                point_score,
                -distance_meters,
                point,
                elevation,
                elevation_delta,
                habitat.note,
            )
        )
    if not scored:
        return None

    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    (
        best_score,
        _negative_distance,
        selected,
        selected_elevation,
        delta,
        habitat_note,
    ) = scored[0]
    contenders = [item for item in scored if item[0] >= best_score - 0.05]
    spread = max(
        (
            haversine_km(
                selected.latitude,
                selected.longitude,
                item[2].latitude,
                item[2].longitude,
            )
            * 1000.0
            for item in contenders
        ),
        default=0.0,
    )
    anchor_uncertainty = number_or(
        anchor.candidate_uncertainty_meters, float(radius_meters)
    )
    selected_name_score = name_similarity(selected.tags, context)
    minimum_uncertainty = 250.0 if selected_name_score >= 0.35 else 500.0
    uncertainty = int(
        round(
            max(
                DEM_RESOLUTION_METERS,
                min(
                    anchor_uncertainty,
                    max(minimum_uncertainty, spread / 2.0),
                ),
            )
        )
    )
    if math.isfinite(delta):
        elevation_note = (
            f"DEM elevation {selected_elevation:.0f} m differs from the "
            f"target by {delta:.0f} m"
        )
    else:
        elevation_note = f"DEM elevation {selected_elevation:.0f} m"
    return (
        selected,
        selected_elevation,
        best_score,
        uncertainty,
        elevation_note,
        habitat_note,
    )


def build_refined_candidate(
    anchor: CoordinateCandidate,
    selected: RoutePoint,
    elevation: float,
    point_score: float,
    uncertainty_meters: int,
    elevation_note: str,
    habitat_note: str,
) -> CoordinateCandidate:
    route_name = next(iter(route_names(selected.tags)), "")
    route_kind = selected.tags.get("highway") or selected.tags.get("railway") or "route"
    anchor_score = number_or(anchor.score, 0.0)
    combined_score = min(0.96, 0.55 * anchor_score + 0.45 * point_score)
    source_urls = [url.strip() for url in anchor.source_urls.split("|") if url.strip()]
    source_urls.extend(
        (
            f"https://www.openstreetmap.org/way/{selected.way_id}",
            OPENSTREETMAP_COPYRIGHT,
            OPEN_METEO_DOCUMENTATION,
            COPERNICUS_DEM_DOI,
        )
    )
    evidence_layers = [
        value.strip()
        for value in anchor.evidence_layers.split("|")
        if value.strip()
    ]
    evidence_layers.extend(
        ("openstreetmap_route_geometry", "copernicus_dem_90m")
    )
    if habitat_note != "habitat prior not supplied":
        evidence_layers.extend(("habitat_prior", "openstreetmap_environment_context"))
    evidence = (
        f"Started from the source-backed LLM locality anchor and selected an actual "
        f"OpenStreetMap {route_kind} vertex"
        f"{f' on {route_name}' if route_name else ''}; {elevation_note}; "
        f"{habitat_note}."
    )
    rounded_elevation = round_elevation(elevation, 10)
    return replace(
        anchor,
        candidate_latitude=f"{selected.latitude:.6f}",
        candidate_longitude=f"{selected.longitude:.6f}",
        candidate_geodetic_datum="WGS84",
        candidate_uncertainty_meters=str(uncertainty_meters),
        candidate_elevation_meters=str(rounded_elevation),
        candidate_type="route_dem_refinement",
        modern_place_name=route_name or anchor.modern_place_name,
        source_urls=" | ".join(dict.fromkeys(source_urls)),
        evidence_layers=" | ".join(dict.fromkeys(evidence_layers)),
        evidence=evidence,
        score=f"{combined_score:.2f}",
        selected="",
        decision="",
        verification_status="",
        candidate_source="geospatial_refinement:osm_open_meteo",
        remarks=(
            "Six-decimal route vertex is a reproducible point estimate, not a "
            "claim of sub-meter accuracy; use coordinateUncertaintyInMeters."
        ),
    )


def refine_llm_candidates(
    candidates: Sequence[CoordinateCandidate],
    label: LabelRead,
    settings: GeospatialRefinementSettings,
    habitat_preference: Optional[HabitatPreference] = None,
    cache: Optional[GeospatialRefinementCache] = None,
    context_fetcher: ContextFetcher = fetch_osm_geospatial_context,
    elevation_fetcher: ElevationFetcher = fetch_open_meteo_elevations,
) -> RefinementOutcome:
    if not settings.enabled:
        return RefinementOutcome()
    habitat_preference = habitat_preference or HabitatPreference()
    anchor = select_refinement_anchor(
        candidates,
        validate_environment_only=bool(habitat_preference.canonical),
    )
    if anchor is None:
        return RefinementOutcome()
    outcome = RefinementOutcome(attempted=True)
    cache = cache or GeospatialRefinementCache()
    try:
        anchor_latitude = float(anchor.candidate_latitude)
        anchor_longitude = float(anchor.candidate_longitude)
        radius_meters = refinement_radius_meters(anchor)
        context_key = (
            round(anchor_latitude, 4),
            round(anchor_longitude, 4),
            radius_meters,
        )
        if context_key not in cache.contexts:
            cache.contexts[context_key] = context_fetcher(
                anchor_latitude,
                anchor_longitude,
                radius_meters,
                settings,
            )
        context = cache.contexts[context_key]
        if habitat_preference.canonical:
            anchor_point = RoutePoint(
                latitude=anchor_latitude,
                longitude=anchor_longitude,
                way_id=0,
                tags={},
            )
            anchor_elevation_key = (
                round(anchor_latitude, 7),
                round(anchor_longitude, 7),
            )
            if anchor_elevation_key in cache.elevations:
                anchor_elevation = cache.elevations[anchor_elevation_key]
            else:
                anchor_elevations = elevation_fetcher([anchor_point], settings)
                if not anchor_elevations or anchor_elevations[0] is None:
                    raise ValueError("DEM elevation was unavailable for the LLM anchor.")
                anchor_elevation = float(anchor_elevations[0])
                cache.elevations[anchor_elevation_key] = anchor_elevation
            anchor_habitat = evaluate_habitat_compatibility(
                habitat_preference,
                anchor_point,
                anchor_elevation,
                context.environment,
            )
            if anchor_habitat.hard_conflict:
                anchor.candidate_type = "ecological_conflict"
                anchor.score = "0.00"
                anchor.evidence_layers = append_audit_value(
                    anchor.evidence_layers, "habitat_conflict"
                )
                anchor.evidence_layers = append_audit_value(
                    anchor.evidence_layers, "openstreetmap_environment_context"
                )
                anchor.evidence = (
                    f"{anchor.evidence}; rejected by habitat validation: "
                    f"{anchor_habitat.note}."
                ).strip("; ")
                anchor.remarks = (
                    f"{anchor.remarks} Candidate retained for audit but made "
                    "ineligible because of a hard ecological contradiction."
                ).strip()
                outcome.rejected_anchor = True
                outcome.warning = (
                    "LLM candidate rejected by habitat validation: "
                    f"{anchor_habitat.note}."
                )
                return outcome

        if not settings.use_routes or not has_route_evidence(anchor):
            return outcome
        sampled = choose_elevation_sample(
            context.routes, anchor_latitude, anchor_longitude
        )
        if not sampled:
            outcome.warning = "No mapped trail/road geometry was found near the LLM anchor."
            return outcome

        elevations: List[Optional[float]] = [None] * len(sampled)
        missing_points = []
        missing_indexes = []
        for index, point in enumerate(sampled):
            elevation_key = (round(point.latitude, 7), round(point.longitude, 7))
            if elevation_key in cache.elevations:
                elevations[index] = cache.elevations[elevation_key]
            else:
                missing_points.append(point)
                missing_indexes.append(index)
        if missing_points:
            fetched = elevation_fetcher(missing_points, settings)
            if len(fetched) != len(missing_points):
                raise ValueError("Elevation result count did not match route points.")
            for index, point, elevation in zip(
                missing_indexes, missing_points, fetched
            ):
                elevations[index] = elevation
                if elevation is not None:
                    cache.elevations[
                        (round(point.latitude, 7), round(point.longitude, 7))
                    ] = elevation

        scored = score_route_points(
            anchor,
            label,
            sampled,
            elevations,
            radius_meters,
            habitat_preference,
            context.environment,
        )
        if scored is None:
            outcome.warning = (
                "Mapped routes were found, but none agreed sufficiently with the "
                "locality, elevation, and habitat evidence."
            )
            return outcome
        outcome.candidate = build_refined_candidate(anchor, *scored)
        return outcome
    except Exception as exc:
        outcome.warning = f"Geospatial refinement failed: {exc}"
        return outcome
