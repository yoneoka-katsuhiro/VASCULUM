from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from .elevation import parse_elevation_meters, round_elevation
from .geocoding import haversine_km, is_precise_label_coordinate, parse_decimal, valid_lat_lon
from .models import CoordinateCandidate, CuratedResult
from .policy import (
    canonical_crs,
    documented_original, extent_uncertainty, positive_number, region_conflict,
    rounding_radius, source_domains, to_wgs84,
)


INFERRED_ELEVATION_GRANULARITY_METERS = 10


@dataclass
class SelectionOptions:
    original_precision_decimals: int = 4  # Deprecated: never establishes trust.
    review_distance_km: float = 5.0
    curation_mode: str = "standard"
    georeferenced_by: str = "VASCULUM llm_georeference_curator"
    georeferenced_date: str = ""
    protocol: str = ""
    sources: str = ""
    habitat_prior: str = ""
    max_uncertainty_meters: float | None = None
    label_coordinate_uncertainty_meters: float | None = None
    reference_regions: list = field(default_factory=list)
    georeference_method: str = "evidence-assisted"


def original_coordinate_status(row, precision_decimals=4, reference_regions=None) -> str:
    lat, lon = parse_decimal(row.get("decimalLatitude", "")), parse_decimal(row.get("decimalLongitude", ""))
    if lat is None or lon is None:
        return "missing"
    if not valid_lat_lon(lat, lon):
        return "invalid"
    if lat == 0 and lon == 0:
        return "zero_placeholder"
    try:
        point = to_wgs84(lat, lon, row.get("geodeticDatum", ""))
    except (ValueError, RuntimeError):
        return "datum_unresolved"
    if region_conflict(row, point.latitude, point.longitude, reference_regions or []):
        return "region_conflict"
    radius = positive_number(row.get("coordinateUncertaintyInMeters", ""))
    if not radius:
        return "uncertainty_unknown"
    if radius < (rounding_radius(row.get("decimalLatitude"), row.get("decimalLongitude")) or 1):
        return "uncertainty_below_rounding"
    return "trusted" if documented_original(row) and point.accuracy_meters is not None else "unverified"


def candidate_base(label):
    return {name: getattr(label, name) for name in (
        "catalog_number", "occurrence_id", "image_path", "image_width", "image_height", "image_file_size_bytes",
        "image_quality_status", "image_quality_remarks", "label_source", "label_status",
        "label_transcription", "locality_text", "event_date_text", "collector_text", "elevation_text",
    )} | {"detected_languages": " | ".join(label.detected_languages), "image_paths": " | ".join(label.image_paths)}


def convert_candidate(candidate):
    candidate.source_latitude = candidate.source_latitude or candidate.candidate_latitude
    candidate.source_longitude = candidate.source_longitude or candidate.candidate_longitude
    candidate.source_datum = candidate.source_datum or candidate.candidate_geodetic_datum
    try:
        point = to_wgs84(float(candidate.candidate_latitude), float(candidate.candidate_longitude), candidate.candidate_geodetic_datum)
        candidate.candidate_latitude = f"{point.latitude:.6f}"
        candidate.candidate_longitude = f"{point.longitude:.6f}"
        candidate.candidate_geodetic_datum = point.datum
        candidate.coordinate_operation = point.operation
        radius = positive_number(candidate.candidate_uncertainty_meters)
        if point.accuracy_meters is None:
            candidate.uncertainty_basis = "transformation_accuracy_unknown"
        elif radius and point.accuracy_meters:
            candidate.candidate_uncertainty_meters = str(math.ceil(radius + point.accuracy_meters))
        return True
    except (ValueError, RuntimeError, TypeError) as exc:
        candidate.curation_reasons = str(exc)
        return False


def original_candidate(row, label, status):
    lat, lon = parse_decimal(row.get("decimalLatitude", "")), parse_decimal(row.get("decimalLongitude", ""))
    if lat is None or lon is None or not valid_lat_lon(lat, lon) or status == "zero_placeholder":
        return None
    radius = positive_number(row.get("coordinateUncertaintyInMeters", ""))
    candidate = CoordinateCandidate(
        **candidate_base(label), candidate_latitude=str(row["decimalLatitude"]), candidate_longitude=str(row["decimalLongitude"]),
        candidate_geodetic_datum=row.get("geodeticDatum", ""),
        candidate_uncertainty_meters=str(radius or ""), reported_uncertainty_meters=str(radius or ""),
        uncertainty_basis="documented_original" if radius else "unknown",
        support_status="documented_original" if status == "trusted" else "unverified_original",
        candidate_type="original_coordinate", candidate_source="original_dwc",
        evidence_layers="original_dwc", evidence=f"Original coordinate status={status}",
        uncertainty_components=json.dumps({"reportedRadiusMeters": radius, "roundingMinimumMeters": rounding_radius(row.get("decimalLatitude"), row.get("decimalLongitude"))}),
    )
    convert_candidate(candidate)
    if canonical_crs(row.get("geodeticDatum", "")) == "EPSG:4326":
        candidate.candidate_latitude = str(row["decimalLatitude"])
        candidate.candidate_longitude = str(row["decimalLongitude"])
    return candidate


def label_coordinate_candidates(raw_coordinates, label, options=None):
    options = options or SelectionOptions()
    measurement = positive_number(label.coordinate_uncertainty_meters) or options.label_coordinate_uncertainty_meters
    candidates = []
    for raw in raw_coordinates:
        minimum = raw.uncertainty_meters or rounding_radius(str(raw.latitude), str(raw.longitude)) or 1
        radius = math.ceil(minimum + measurement) if measurement else minimum
        candidate = CoordinateCandidate(
            **candidate_base(label), candidate_latitude=str(raw.latitude), candidate_longitude=str(raw.longitude),
            candidate_geodetic_datum=raw.datum, candidate_uncertainty_meters=str(radius),
            uncertainty_basis="documented_label" if measurement else "label_precision_minimum_only",
            uncertainty_components=json.dumps({"coordinateRoundingMinimumMeters": minimum, "measurementAllowanceMeters": measurement, "allowanceSource": "sidecar" if label.coordinate_uncertainty_meters else "user_policy" if measurement else "unknown"}),
            candidate_type="verbatim_coordinate", candidate_source=f"label:{raw.source}",
            support_status="explicit_label" if is_precise_label_coordinate(raw) else "coarse_label",
            evidence_layers="specimen_label", evidence=f"Printed coordinate: {raw.source_text}; precision={raw.precision_kind}",
        )
        convert_candidate(candidate)
        candidates.append(candidate)
    return candidates


def gazetteer_coordinate_candidates(matches, label):
    candidates = []
    for match in matches:
        entry = match.entry
        radius = positive_number(entry.uncertainty_meters)
        geometry = ""
        if entry.bounds:
            try:
                bounds = json.loads(entry.bounds)
                if len(bounds) == 4:
                    geometry = json.dumps({"type": "bbox", "coordinates": bounds})
                    radius = extent_uncertainty(float(entry.latitude), float(entry.longitude), geometry)
            except (ValueError, TypeError):
                pass
        candidate = CoordinateCandidate(
            **candidate_base(label), candidate_latitude=str(entry.latitude), candidate_longitude=str(entry.longitude),
            candidate_geodetic_datum=entry.geodetic_datum,
            candidate_uncertainty_meters=str(radius or ""), candidate_elevation_meters=entry.elevation_meters,
            uncertainty_basis="verified_reference_extent" if entry.verified and radius else "unverified_reference",
            support_status="verified_reference" if entry.verified else "gazetteer_name_match",
            supporting_geometry=geometry, candidate_type="place_centroid", modern_place_name=entry.place_name,
            historical_place_name=entry.historical_place_name, match_language=entry.language,
            source_urls=entry.source if source_domains(entry.source) else "", evidence_layers="gazetteer",
            evidence=match.evidence, candidate_source=f"gazetteer:{entry.source}",
        )
        convert_candidate(candidate)
        candidates.append(candidate)
    return candidates


def classify_candidate(candidate, row, options):
    reasons = []
    lat, lon = parse_decimal(candidate.candidate_latitude), parse_decimal(candidate.candidate_longitude)
    if lat is None or lon is None or not valid_lat_lon(lat, lon):
        return "unresolved", ["Invalid coordinate"]
    if candidate.candidate_geodetic_datum != "WGS84":
        reasons.append("Datum missing, unresolved or not converted to WGS84")
    conflict = region_conflict(row, lat, lon, options.reference_regions)
    if conflict:
        reasons.append(conflict)
    radius = positive_number(candidate.candidate_uncertainty_meters)
    defensible_basis = candidate.uncertainty_basis in {"documented_original", "documented_label", "verified_reference_extent", "mapped_locality_extent"}
    if not radius or not defensible_basis:
        reasons.append("Total uncertainty not independently established; a rounding minimum or LLM estimate is insufficient")
    if radius and options.max_uncertainty_meters is not None and radius > options.max_uncertainty_meters:
        reasons.append("Uncertainty exceeds the analysis-specific limit")
    if candidate.support_status not in {"documented_original", "explicit_label", "verified_reference", "mapped_locality"}:
        reasons.append("Locality/coordinate support requires review")
    if candidate.candidate_type in {"route_dem_refinement", "place_centroid"} and candidate.support_status != "verified_reference":
        reasons.append("Route vertex or place representative does not establish the collecting point")
    if candidate.curation_reasons:
        reasons.append(candidate.curation_reasons)
    return ("review", reasons) if reasons else ("accepted", ["Documented provenance, datum and uncertainty passed protocol checks"])


def best_candidate(candidates):
    priorities = {"documented_original": 5, "explicit_label": 4, "verified_reference": 3, "mapped_locality": 2}
    return max(candidates, key=lambda item: (
        item.curation_status == "accepted", priorities.get(item.support_status, 0),
        len(source_domains(item.source_urls)),
        -(positive_number(item.candidate_uncertainty_meters) or float("inf")),
    ))


def select_result(row, label, raw_label_coordinates, llm_candidates, gazetteer_matches,
                  insufficient_locality, exclude_insufficient_locality, options):
    status = original_coordinate_status(row, options.original_precision_decimals, options.reference_regions)
    original = original_candidate(row, label, status) if options.georeference_method != "xie-baseline" else None
    candidates = ([original] if original else [])
    if options.georeference_method != "xie-baseline":
        candidates.extend(label_coordinate_candidates(raw_label_coordinates, label, options))
        candidates.extend(gazetteer_coordinate_candidates(gazetteer_matches, label))
    for candidate in llm_candidates:
        convert_candidate(candidate)
        candidates.append(candidate)
    for candidate in candidates:
        candidate.occurrence_id = label.occurrence_id
        candidate.curation_status, reasons = classify_candidate(candidate, row, options)
        candidate.curation_reasons = "; ".join(reasons)
    usable = [candidate for candidate in candidates if candidate.curation_status != "unresolved"]
    selected = best_candidate(usable) if usable else None
    notes = []
    curation_status = selected.curation_status if selected else "unresolved"
    # Contradictory independently documented points require review, not an automatic override.
    if selected:
        for alternative in usable:
            if alternative is selected or alternative.support_status not in {"documented_original", "explicit_label", "verified_reference"}:
                continue
            distance = haversine_km(float(selected.candidate_latitude), float(selected.candidate_longitude), float(alternative.candidate_latitude), float(alternative.candidate_longitude))
            radius = (positive_number(selected.candidate_uncertainty_meters) or 0) + (positive_number(alternative.candidate_uncertainty_meters) or 0)
            if distance * 1000 > max(options.review_distance_km * 1000, radius):
                curation_status = "review"
                notes.append(f"Conflicting documented coordinate is {distance:.3f} km away")
    evaluated = not label.image_path or label.label_status in {"transcribed", "llm_image_transcribed", "llm_text_augmented"}
    excluded = bool(options.georeference_method != "xie-baseline" and exclude_insufficient_locality and insufficient_locality and evaluated and not (selected and selected.curation_status == "accepted"))
    if excluded:
        curation_status = "excluded"
        selected = None
    decision = "unresolved"
    if curation_status == "excluded":
        decision = "exclude_insufficient_locality"
    elif selected:
        decision = "keep_original" if selected is original else "infer_missing" if status == "missing" else "correct_existing"
    kind = selected.candidate_type if selected else "none"
    verification = f"{curation_status}_{kind}"
    include = curation_status != "excluded" and (options.curation_mode != "robust" or curation_status == "accepted")
    curated = dict(row)
    for key in ("decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters", "geodeticDatum"):
        curated[key] = ""
    if selected:
        curated.update(decimalLatitude=selected.candidate_latitude, decimalLongitude=selected.candidate_longitude,
                       geodeticDatum=selected.candidate_geodetic_datum,
                       coordinateUncertaintyInMeters=selected.candidate_uncertainty_meters if selected.uncertainty_basis in {"documented_original", "documented_label", "verified_reference_extent", "mapped_locality_extent"} else "")
        curated.update(georeferencedBy=options.georeferenced_by, georeferencedDate=options.georeferenced_date,
                       georeferenceProtocol=options.protocol, georeferenceSources=selected.source_urls or options.sources)
        if selected is original:
            curated["georeferenceProtocol"] = "; ".join(filter(None, (options.protocol, row.get("georeferenceProtocol", ""))))
            curated["georeferenceSources"] = " | ".join(filter(None, (row.get("georeferenceSources", ""), curated["georeferenceSources"])))
    curated["georeferenceVerificationStatus"] = curation_status
    minimum, maximum = parse_elevation_meters(label.elevation_text or label.label_transcription or row.get("verbatimElevation", ""))
    elevation_note = ""
    if minimum is not None and maximum is not None:
        curated["minimumElevationInMeters"], curated["maximumElevationInMeters"] = str(minimum), str(maximum)
        elevation_note = "elevation_source=specimen_label"
    elif selected:
        elevation = parse_decimal(selected.candidate_elevation_meters)
        if elevation is not None and math.isfinite(elevation):
            value = str(round_elevation(elevation, INFERRED_ELEVATION_GRANULARITY_METERS))
            curated["minimumElevationInMeters"] = curated["maximumElevationInMeters"] = value
            elevation_note = "elevation_source=estimated; elevation_granularity_m=10; vertical_accuracy_not_guaranteed"
    notes.extend([selected.curation_reasons] if selected else [])
    if excluded:
        notes.append(insufficient_locality)
    curated["georeferenceRemarks"] = "; ".join(filter(None, (
        f"VASCULUM decision={decision}", f"verification={verification}", f"curation_status={curation_status}",
        f"label_status={label.label_status}", elevation_note, *notes,
    )))
    if not candidates:
        candidates.append(CoordinateCandidate(**candidate_base(label), candidate_source="none", curation_status=curation_status))
    candidates.sort(key=lambda candidate: candidate is not selected)
    for index, candidate in enumerate(candidates, 1):
        candidate.original_latitude = row.get("decimalLatitude", "")
        candidate.original_longitude = row.get("decimalLongitude", "")
        candidate.original_datum = row.get("geodeticDatum", "")
        candidate.input_type = row.get("vasculumInputType", "")
        candidate.input_source = row.get("vasculumInputSource", "")
        candidate.candidate_rank = index
        candidate.selected = "true" if candidate is selected else "false"
        candidate.decision, candidate.verification_status = decision, verification
        candidate.habitat_prior = options.habitat_prior
    return CuratedResult(
        row=curated, candidates=candidates, decision=decision, verification_status=verification,
        selected_candidate=selected, notes=notes, include_in_dwc=include,
        exclusion_reason=insufficient_locality if excluded else "", curation_status=curation_status,
        original_coordinates={key: row.get(key, "") for key in ("decimalLatitude", "decimalLongitude", "geodeticDatum", "coordinateUncertaintyInMeters", "georeferenceProtocol", "georeferenceVerificationStatus")},
    )
