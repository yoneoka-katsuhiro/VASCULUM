from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from .elevation import parse_elevation_meters, round_elevation, specimen_elevation
from .geocoding import haversine_km, is_precise_label_coordinate, parse_decimal, valid_lat_lon
from .geographic_screen import geographic_problem
from .models import CoordinateCandidate, CuratedResult
from .policy import (canonical_crs, documented_original, extent_uncertainty, positive_number,
                     region_conflict, rounding_radius, source_domains, to_wgs84,
                     UNSTATED_POSITION_ALLOWANCE_METERS)


INFERRED_ELEVATION_GRANULARITY_METERS = 10
DEFAULT_LOCALITY_RADIUS_METERS = 50000
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class SelectionOptions:
    review_distance_km: float = 5.0
    georeferenced_by: str = "VASCULUM llm_georeference_curator"
    georeferenced_date: str = ""
    protocol: str = ""
    habitat_prior: str = ""
    max_uncertainty_meters: float | None = None
    label_coordinate_uncertainty_meters: float | None = None
    reference_regions: list = field(default_factory=list)


def original_coordinate_status(row, reference_regions=None):
    lat, lon = parse_decimal(row.get("decimalLatitude", "")), parse_decimal(row.get("decimalLongitude", ""))
    if lat is None or lon is None:
        return "missing"
    if lat == 0 and lon == 0:
        return "zero_placeholder"
    if not valid_lat_lon(lat, lon):
        if valid_lat_lon(lon, lat) and not geographic_problem(row, lon, lat):
            return "swapped"
        return "invalid"
    try:
        point = to_wgs84(lat, lon, row.get("geodeticDatum", ""))
    except (ValueError, RuntimeError):
        return "conversion_failed"
    if region_conflict(row, point.latitude, point.longitude, reference_regions or []):
        return "region_conflict"
    problem = geographic_problem(row, point.latitude, point.longitude, placeholders=not documented_original(row))
    return problem or "trusted"


def candidate_base(label):
    return {name: getattr(label, name) for name in (
        "catalog_number", "occurrence_id", "image_path", "image_width", "image_height", "image_file_size_bytes",
        "image_quality_status", "image_quality_remarks", "label_source", "label_status",
        "label_transcription", "locality_text", "event_date_text", "collector_text", "elevation_text",
    )} | {"detected_languages": " | ".join(label.detected_languages), "image_paths": " | ".join(label.image_paths)}


def convert_candidate(candidate):
    if candidate.coordinate_operation and candidate.candidate_geodetic_datum == "WGS84":
        return True
    candidate.source_latitude = candidate.source_latitude or candidate.candidate_latitude
    candidate.source_longitude = candidate.source_longitude or candidate.candidate_longitude
    candidate.source_datum = candidate.source_datum or candidate.candidate_geodetic_datum
    try:
        point = to_wgs84(float(candidate.candidate_latitude), float(candidate.candidate_longitude), candidate.candidate_geodetic_datum)
        candidate.candidate_latitude, candidate.candidate_longitude = f"{point.latitude:.6f}", f"{point.longitude:.6f}"
        candidate.candidate_geodetic_datum = "WGS84"
        candidate.coordinate_operation = point.operation
        components = json.loads(candidate.uncertainty_components or "{}")
        allowance = point.accuracy_meters if point.accuracy_meters is not None else 5000
        components["datumOrTransformationAllowanceMeters"] = allowance
        candidate.candidate_uncertainty_meters = str(math.ceil((positive_number(candidate.candidate_uncertainty_meters) or 0) + allowance))
        candidate.uncertainty_components = json.dumps(components, ensure_ascii=False)
        if point.assumption:
            candidate.remarks = "; ".join(filter(None, (candidate.remarks, point.assumption, f"datum allowance={allowance:g} m (policy assumption)")))
            candidate.confidence = "low"
        elif point.accuracy_meters is None:
            candidate.remarks += "; transformation accuracy not stated; 5000 m policy allowance"
            candidate.confidence = "low"
        return True
    except (ValueError, RuntimeError, TypeError) as exc:
        candidate.curation_reasons = str(exc)
        return False


def original_candidate(row, label, status):
    lat, lon = parse_decimal(row.get("decimalLatitude", "")), parse_decimal(row.get("decimalLongitude", ""))
    if lat is None or lon is None or status not in {"trusted", "swapped"}:
        return None
    if status == "swapped":
        lat, lon = lon, lat
    reported = positive_number(row.get("coordinateUncertaintyInMeters", ""))
    precision_latitude = row.get("decimalLongitude") if status == "swapped" else row.get("decimalLatitude")
    precision_longitude = row.get("decimalLatitude") if status == "swapped" else row.get("decimalLongitude")
    minimum = rounding_radius(precision_latitude, precision_longitude) or 1
    radius = max(reported or UNSTATED_POSITION_ALLOWANCE_METERS, minimum)
    known_datum = bool(canonical_crs(row.get("geodeticDatum", "")))
    confidence = "high" if reported and known_datum and documented_original(row) else "medium" if reported and known_datum else "low"
    candidate = CoordinateCandidate(**candidate_base(label), candidate_latitude=str(lat), candidate_longitude=str(lon),
        candidate_geodetic_datum=row.get("geodeticDatum", ""), candidate_uncertainty_meters=str(math.ceil(radius)),
        reported_uncertainty_meters=str(reported or ""), uncertainty_basis="reported_original" if reported else "policy_original_allowance",
        confidence=confidence, support_status="documented_original" if documented_original(row) else "plausible_original",
        candidate_type="original_coordinate", candidate_source="original_dwc", evidence_layers="original_dwc",
        evidence="Existing coordinate retained after plausibility checks" + ("; latitude/longitude swap corrected" if status == "swapped" else ""),
        uncertainty_components=json.dumps({"reportedRadiusMeters": reported, "roundingMinimumMeters": minimum,
            "unstatedPositionAllowanceMeters": 0 if reported else UNSTATED_POSITION_ALLOWANCE_METERS}),
        remarks="" if reported else "positional error not stated; 1000 m policy allowance (not measured accuracy)")
    convert_candidate(candidate)
    if status != "swapped" and (not row.get("geodeticDatum") or canonical_crs(row.get("geodeticDatum", "")) == "EPSG:4326"):
        candidate.candidate_latitude, candidate.candidate_longitude = str(row["decimalLatitude"]), str(row["decimalLongitude"])
    return candidate


def label_coordinate_candidates(raw_coordinates, label, options=None):
    options = options or SelectionOptions()
    supplied = positive_number(label.coordinate_uncertainty_meters) or options.label_coordinate_uncertainty_meters
    measurement = supplied or UNSTATED_POSITION_ALLOWANCE_METERS
    candidates = []
    for raw in raw_coordinates:
        minimum = raw.uncertainty_meters or rounding_radius(str(raw.latitude), str(raw.longitude)) or 1
        candidate = CoordinateCandidate(**candidate_base(label), candidate_latitude=str(raw.latitude), candidate_longitude=str(raw.longitude),
            candidate_geodetic_datum=raw.datum, candidate_uncertainty_meters=str(math.ceil(minimum + measurement)),
            uncertainty_basis="documented_label" if supplied else "policy_label_allowance",
            uncertainty_components=json.dumps({"coordinateRoundingMinimumMeters": minimum, "measurementAllowanceMeters": measurement,
                "allowanceSource": "sidecar" if label.coordinate_uncertainty_meters else "user_policy" if supplied else "default_policy_not_measured"}),
            confidence="high" if supplied and raw.datum else "low",
            candidate_type="verbatim_coordinate", candidate_source=f"label:{raw.source}",
            support_status="explicit_label" if is_precise_label_coordinate(raw) else "coarse_label",
            evidence_layers="specimen_label", evidence=f"Printed coordinate: {raw.source_text}; precision={raw.precision_kind}",
            remarks="" if supplied else "label measurement error not stated; 1000 m policy allowance")
        convert_candidate(candidate)
        candidates.append(candidate)
    return candidates


def gazetteer_coordinate_candidates(matches, label):
    candidates = []
    for match in matches:
        entry = match.entry
        geometry = ""
        if entry.bounds:
            try:
                bounds = json.loads(entry.bounds)
                if len(bounds) == 4:
                    geometry = json.dumps({"type": "bbox", "coordinates": bounds})
            except (ValueError, TypeError):
                pass
        candidate = CoordinateCandidate(**candidate_base(label), candidate_latitude=str(entry.latitude), candidate_longitude=str(entry.longitude),
            candidate_geodetic_datum=entry.geodetic_datum, candidate_uncertainty_meters=str(positive_number(entry.uncertainty_meters) or ""),
            uncertainty_basis="reference_extent" if geometry else "reference_reported", confidence="high" if entry.verified else "low",
            candidate_elevation_meters=entry.elevation_meters, support_status="verified_reference" if entry.verified else "gazetteer_name_match",
            supporting_geometry=geometry, candidate_type="place_centroid", modern_place_name=entry.place_name,
            historical_place_name=entry.historical_place_name, match_language=entry.language,
            source_urls=entry.source if source_domains(entry.source) else "", evidence_layers="gazetteer", evidence=match.evidence,
            candidate_source=f"gazetteer:{entry.source}")
        candidates.append(candidate)
    return candidates


def classify_candidate(candidate, row, options):
    reasons = []
    lat, lon = parse_decimal(candidate.candidate_latitude), parse_decimal(candidate.candidate_longitude)
    if lat is None or lon is None or not valid_lat_lon(lat, lon):
        return "failed", ["Invalid or missing coordinate in provider/reference response"]
    if candidate.supporting_geometry and candidate.candidate_type not in {"original_coordinate", "verbatim_coordinate"}:
        try:
            geometry = json.loads(candidate.supporting_geometry)
            south, west, north, east = map(float, geometry["coordinates"])
            if geometry.get("type") == "bbox" and -90 <= south <= north <= 90 and -180 <= west <= east <= 180:
                if not (south <= lat <= north and west <= lon <= east):
                    lat, lon = (south + north) / 2, (west + east) / 2
                    candidate.candidate_latitude, candidate.candidate_longitude = f"{lat:.6f}", f"{lon:.6f}"
                    candidate.coordinate_operation = ""
                    candidate.confidence = "low"
                    reasons.append("Supplied point lay outside its named extent; extent center used")
        except (ValueError, TypeError, KeyError):
            candidate.confidence = "low"
    radius = positive_number(candidate.candidate_uncertainty_meters)
    extent = extent_uncertainty(lat, lon, candidate.supporting_geometry) if candidate.supporting_geometry else None
    if extent:
        radius = max(radius or 0, extent)
        reasons.append("Radius covers the stated locality extent")
    if not radius:
        radius = DEFAULT_LOCALITY_RADIUS_METERS
        candidate.confidence = "low"
        candidate.uncertainty_basis = "policy_locality_allowance"
        reasons.append("Locality radius not supplied; conservative 50000 m policy allowance, not measured accuracy")
    candidate.candidate_uncertainty_meters = str(math.ceil(max(radius, rounding_radius(candidate.candidate_latitude, candidate.candidate_longitude) or 1)))
    if not convert_candidate(candidate):
        return "failed", [candidate.curation_reasons]
    lat, lon = float(candidate.candidate_latitude), float(candidate.candidate_longitude)
    conflict = region_conflict(row, lat, lon, options.reference_regions) or geographic_problem(row, lat, lon)
    if conflict:
        return "excluded", [conflict]
    if candidate.confidence not in CONFIDENCE_RANK:
        candidate.confidence = "low"
    if candidate.support_status in {"llm_estimate", "gazetteer_name_match", "route_proposal", "coarse_label"}:
        reasons.append("Best point-radius estimate; confidence is not a calibrated probability")
    if candidate.source_checks:
        checks = json.loads(candidate.source_checks)
        if checks and all(item.get("status") == "unavailable" for item in checks):
            candidate.confidence = "low"
            reasons.append("Cited source retrieval unavailable; geographic estimate retained")
    reasons.extend(filter(None, (candidate.evidence, candidate.remarks)))
    return "accepted", reasons or ["Plausible coordinate with an explicit uncertainty radius"]


def best_candidate(candidates):
    priorities = {"documented_original": 7, "plausible_original": 7, "explicit_label": 6,
                  "verified_reference": 5, "mapped_locality": 4, "llm_estimate": 3, "gazetteer_name_match": 3,
                  "coarse_label": 2, "route_proposal": 1}
    return max(candidates, key=lambda item: (priorities.get(item.support_status, 0), CONFIDENCE_RANK.get(item.confidence, 0)))


def select_result(row, label, raw_label_coordinates, llm_candidates, gazetteer_matches, insufficient_locality, options):
    status = original_coordinate_status(row, options.reference_regions)
    original = original_candidate(row, label, status)
    candidates = ([original] if original else []) + label_coordinate_candidates(raw_label_coordinates, label, options)
    candidates.extend(gazetteer_coordinate_candidates(gazetteer_matches, label))
    candidates.extend(llm_candidates)
    for candidate in candidates:
        candidate.occurrence_id = label.occurrence_id
        candidate.curation_status, reasons = classify_candidate(candidate, row, options)
        candidate.curation_reasons = "; ".join(reasons)
    usable = [candidate for candidate in candidates if candidate.curation_status == "accepted"]
    selected = best_candidate(usable) if usable else None
    notes = []
    reason = ""
    if selected:
        curation_status = "accepted"
        for alternative in usable:
            if alternative is selected:
                continue
            distance = haversine_km(float(selected.candidate_latitude), float(selected.candidate_longitude), float(alternative.candidate_latitude), float(alternative.candidate_longitude)) * 1000
            radius = positive_number(selected.candidate_uncertainty_meters)
            if distance > max(options.review_distance_km * 1000, radius + positive_number(alternative.candidate_uncertainty_meters)):
                selected.confidence = "low"
                selected.candidate_uncertainty_meters = str(math.ceil(max(radius, distance + positive_number(alternative.candidate_uncertainty_meters))))
                notes.append("Conflicting locality evidence retained in the uncertainty radius")
        if options.max_uncertainty_meters is not None and float(selected.candidate_uncertainty_meters) > options.max_uncertainty_meters:
            curation_status, reason = "excluded", "User-requested maximum uncertainty exceeded"
    elif insufficient_locality:
        curation_status, reason = "excluded", insufficient_locality
    elif any(item.curation_status == "excluded" for item in candidates) or status not in {"missing", "trusted", "swapped", "conversion_failed"}:
        curation_status, reason = "excluded", "Coordinates contradict country/state or are obvious placeholders and could not be repaired from the label"
    else:
        curation_status, reason = "failed", "Required point-radius response missing or invalid; retry geographic research"
    decision = ("keep_original" if selected is original and status != "swapped" else "correct_existing" if status != "missing" else "infer_missing") if selected else "failed" if curation_status == "failed" else "exclude_coordinate_conflict" if not insufficient_locality else "exclude_insufficient_locality"
    if selected and curation_status == "excluded":
        decision = "exclude_uncertainty_limit"
    curated = dict(row)
    for key in ("decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters", "geodeticDatum"):
        curated[key] = ""
    curated.update(curationStatus=curation_status, confidence=selected.confidence if selected else "low", georeferenceVerificationStatus="unverified")
    if selected:
        curated.update(decimalLatitude=selected.candidate_latitude, decimalLongitude=selected.candidate_longitude,
                       coordinateUncertaintyInMeters=selected.candidate_uncertainty_meters, geodeticDatum="WGS84",
                       georeferencedBy=options.georeferenced_by, georeferencedDate=options.georeferenced_date, georeferenceProtocol=options.protocol)
        if selected is original:
            curated["georeferenceSources"] = row.get("georeferenceSources") or row.get("vasculumCoordinateSource") or row.get("references") or "original DwC"
            for key in ("georeferencedBy", "georeferencedDate"):
                curated[key] = row.get(key) or curated[key]
            curated["georeferenceProtocol"] = "; ".join(filter(None, (options.protocol, row.get("georeferenceProtocol", ""))))
        elif selected.candidate_type == "verbatim_coordinate":
            curated["georeferenceSources"] = " | ".join(f"specimen photograph: {path}" for path in label.image_paths) or "specimen label transcription"
        else:
            curated["georeferenceSources"] = selected.source_urls or selected.candidate_source
    minimum, maximum = specimen_elevation(label)
    elevation_note = "elevation_source=specimen_label" if minimum is not None else ""
    if minimum is None:
        minimum, maximum = parse_elevation_meters(row.get("verbatimElevation", ""))
        if minimum is not None:
            elevation_note = "elevation_source=original_dwc"
    if minimum is not None and maximum is not None:
        curated["minimumElevationInMeters"], curated["maximumElevationInMeters"] = str(minimum), str(maximum)
    elif selected and parse_decimal(selected.candidate_elevation_meters) is not None:
        value = str(round_elevation(float(selected.candidate_elevation_meters), INFERRED_ELEVATION_GRANULARITY_METERS))
        curated["minimumElevationInMeters"] = curated["maximumElevationInMeters"] = value
        elevation_note = "elevation_source=estimated; elevation_granularity_m=10; vertical_accuracy_not_guaranteed"
    curated["georeferenceRemarks"] = "; ".join(filter(None, (f"VASCULUM decision={decision}", f"curation_status={curation_status}",
        f"confidence={curated['confidence']}", selected.curation_reasons if selected else reason, elevation_note, *notes,
        row.get("georeferenceRemarks", "") if selected is original else "")))
    if not candidates:
        candidates.append(CoordinateCandidate(**candidate_base(label), candidate_source="none", curation_status=curation_status, curation_reasons=reason))
    candidates.sort(key=lambda candidate: candidate is not selected)
    for index, candidate in enumerate(candidates, 1):
        candidate.original_latitude, candidate.original_longitude, candidate.original_datum = row.get("decimalLatitude", ""), row.get("decimalLongitude", ""), row.get("geodeticDatum", "")
        candidate.input_type, candidate.input_source = row.get("vasculumInputType", ""), row.get("vasculumInputSource", "")
        candidate.candidate_rank, candidate.selected = index, "true" if candidate is selected else "false"
        candidate.decision, candidate.verification_status, candidate.habitat_prior = decision, "unverified", options.habitat_prior
        if candidate is selected:
            candidate.curation_status = curation_status
    return CuratedResult(row=curated, candidates=candidates, decision=decision, verification_status="unverified", selected_candidate=selected,
        notes=notes, include_in_dwc=curation_status == "accepted", exclusion_reason=reason, curation_status=curation_status,
        original_coordinates={key: row.get(key, "") for key in ("decimalLatitude", "decimalLongitude", "geodeticDatum", "coordinateUncertaintyInMeters", "georeferenceProtocol", "georeferenceVerificationStatus")})
