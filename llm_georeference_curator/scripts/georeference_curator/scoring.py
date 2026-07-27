from __future__ import annotations

from dataclasses import dataclass

from .elevation import parse_elevation_meters, round_elevation
from .geocoding import (
    GazetteerMatch,
    RawCoordinate,
    haversine_km,
    is_precise_label_coordinate,
    parse_decimal,
    uncertainty_from_precision,
    valid_lat_lon,
)
from .models import CoordinateCandidate, CuratedResult, LabelRead


ROBUST_LABEL_MAX_UNCERTAINTY_METERS = 1500
ROBUST_LLM_MAX_UNCERTAINTY_METERS = 10000
ROBUST_LLM_MIN_SCORE = 0.60
ROBUST_WEB_ANCHOR_MAX_UNCERTAINTY_METERS = 5000
ROBUST_ROUTE_DEM_MAX_UNCERTAINTY_METERS = 5000
INFERRED_ELEVATION_GRANULARITY_METERS = 10


@dataclass
class SelectionOptions:
    original_precision_decimals: int = 4
    review_distance_km: float = 5.0
    curation_mode: str = "standard"
    georeferenced_by: str = "VASCULUM llm_georeference_curator"
    georeferenced_date: str = ""
    protocol: str = ""
    sources: str = ""
    habitat_prior: str = ""


def decimal_places(value: str) -> int:
    text = str(value).strip()
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def original_coordinate_status(row, precision_decimals: int) -> str:
    latitude = parse_decimal(row.get("decimalLatitude", ""))
    longitude = parse_decimal(row.get("decimalLongitude", ""))
    if latitude is None or longitude is None:
        return "missing"
    if not valid_lat_lon(latitude, longitude):
        return "invalid"
    if abs(latitude) < 1e-12 and abs(longitude) < 1e-12:
        return "zero_placeholder"
    if min(decimal_places(row.get("decimalLatitude", "")), decimal_places(row.get("decimalLongitude", ""))) >= precision_decimals:
        return "precise"
    return "coarse"


def candidate_base(label: LabelRead):
    return {
        "catalog_number": label.catalog_number,
        "image_path": label.image_path,
        "image_width": label.image_width,
        "image_height": label.image_height,
        "image_file_size_bytes": label.image_file_size_bytes,
        "image_quality_status": label.image_quality_status,
        "image_quality_remarks": label.image_quality_remarks,
        "detected_languages": " | ".join(label.detected_languages),
        "label_source": label.label_source,
        "label_status": label.label_status,
        "label_transcription": label.label_transcription,
        "locality_text": label.locality_text,
        "event_date_text": label.event_date_text,
        "collector_text": label.collector_text,
        "elevation_text": label.elevation_text,
    }


def original_candidate(row, label: LabelRead, status: str):
    latitude_text = row.get("decimalLatitude", "")
    longitude_text = row.get("decimalLongitude", "")
    latitude = parse_decimal(latitude_text)
    longitude = parse_decimal(longitude_text)
    if latitude is None or longitude is None or not valid_lat_lon(latitude, longitude):
        return None
    if status == "zero_placeholder":
        return None
    uncertainty = row.get("coordinateUncertaintyInMeters", "").strip()
    if not uncertainty:
        uncertainty = str(uncertainty_from_precision(latitude_text, longitude_text))
    return CoordinateCandidate(
        **candidate_base(label),
        candidate_latitude=str(latitude),
        candidate_longitude=str(longitude),
        candidate_geodetic_datum=row.get("geodeticDatum", "") or "WGS84",
        candidate_uncertainty_meters=uncertainty,
        candidate_type="original_coordinate",
        evidence_layers="original_dwc",
        evidence=f"original DwC coordinates; coordinate_status={status}",
        score="0.90" if status == "precise" else "0.45",
        candidate_source="original_dwc",
        remarks="Original coordinates are retained only when accepted by the curation mode.",
    )


def label_coordinate_candidates(raw_coordinates, label: LabelRead):
    base = candidate_base(label)
    minimum, maximum = parse_elevation_meters(
        label.elevation_text or label.label_transcription
    )
    elevation = ""
    if minimum is not None and maximum is not None:
        elevation = str(round((minimum + maximum) / 2))
    candidates = []
    for raw in raw_coordinates:
        if is_precise_label_coordinate(raw):
            latitude_text = f"{raw.latitude:.6f}"
            longitude_text = f"{raw.longitude:.6f}"
        else:
            latitude_text = f"{raw.latitude:.8f}".rstrip("0").rstrip(".")
            longitude_text = f"{raw.longitude:.8f}".rstrip("0").rstrip(".")
        candidates.append(
            CoordinateCandidate(
                **base,
                candidate_latitude=latitude_text,
                candidate_longitude=longitude_text,
                candidate_geodetic_datum=raw.datum or "WGS84",
                candidate_uncertainty_meters=str(
                    raw.uncertainty_meters
                    or uncertainty_from_precision(latitude_text, longitude_text)
                ),
                candidate_elevation_meters=elevation,
                candidate_type="verbatim_coordinate",
                evidence_layers="specimen_label",
                evidence=(
                    f"explicit coordinate found in label text: {raw.source_text}; "
                    f"source_precision={raw.precision_kind or 'unknown'}"
                ),
                score="0.80" if (raw.datum in {"", "WGS84"}) else "0.70",
                candidate_source=f"label:{raw.source}",
                remarks=(
                    "Coordinate parsed from label transcription. Coarse degree/minute values "
                    "are search anchors and are not automatically accepted in robust mode."
                ),
            )
        )
    return candidates


def gazetteer_coordinate_candidates(matches, label: LabelRead):
    candidates = []
    for match in matches:
        entry = match.entry
        candidates.append(
            CoordinateCandidate(
                **candidate_base(label),
                candidate_latitude=str(entry.latitude),
                candidate_longitude=str(entry.longitude),
                candidate_geodetic_datum="WGS84",
                candidate_uncertainty_meters=entry.uncertainty_meters or "10000",
                candidate_elevation_meters=entry.elevation_meters,
                candidate_type="place_centroid",
                modern_place_name=entry.place_name,
                historical_place_name=entry.historical_place_name,
                match_language=entry.language,
                evidence_layers="gazetteer",
                evidence=match.evidence,
                score=f"{match.score:.2f}",
                candidate_source=f"gazetteer:{entry.source}",
                remarks="Coordinate matched from DwC/label locality text using local gazetteer.",
            )
        )
    return candidates


def number_or(value, fallback: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def best_candidate(candidates):
    return max(
        candidates,
        key=lambda item: (
            number_or(item.score, 0.0),
            -number_or(item.candidate_uncertainty_meters, float("inf")),
        ),
    )


def is_wgs84(candidate) -> bool:
    datum = str(candidate.candidate_geodetic_datum or "").upper()
    compact = "".join(character for character in datum if character.isalnum())
    return compact in {"", "WGS84", "EPSG4326"}


def selectable_llm_candidates(candidates, options: SelectionOptions):
    refined = [
        candidate
        for candidate in candidates
        if candidate.candidate_source.startswith("llm:")
        and candidate.candidate_type == "refined_georeference"
        and is_wgs84(candidate)
    ]
    if options.curation_mode != "robust":
        return refined
    return [
        candidate
        for candidate in refined
        if candidate.source_urls
        and number_or(candidate.score, 0.0) >= ROBUST_LLM_MIN_SCORE
        and 0 < number_or(candidate.candidate_uncertainty_meters, float("inf"))
        <= ROBUST_LLM_MAX_UNCERTAINTY_METERS
    ]


def selectable_route_dem_candidates(candidates, options: SelectionOptions):
    refined = [
        candidate
        for candidate in candidates
        if candidate.candidate_source.startswith("geospatial_refinement:")
        and candidate.candidate_type == "route_dem_refinement"
        and candidate.source_urls
        and is_wgs84(candidate)
    ]
    if options.curation_mode != "robust":
        return refined
    return [
        candidate
        for candidate in refined
        if number_or(candidate.score, 0.0) >= ROBUST_LLM_MIN_SCORE
        and 0 < number_or(candidate.candidate_uncertainty_meters, float("inf"))
        <= ROBUST_ROUTE_DEM_MAX_UNCERTAINTY_METERS
    ]


def selectable_web_anchor_candidates(candidates, options: SelectionOptions):
    anchors = [
        candidate
        for candidate in candidates
        if candidate.candidate_source.startswith("llm:")
        and candidate.candidate_type == "place_centroid"
        and candidate.source_urls
        and is_wgs84(candidate)
    ]
    if options.curation_mode != "robust":
        return anchors
    return [
        candidate
        for candidate in anchors
        if number_or(candidate.score, 0.0) >= ROBUST_LLM_MIN_SCORE
        and 0 < number_or(candidate.candidate_uncertainty_meters, float("inf"))
        <= ROBUST_WEB_ANCHOR_MAX_UNCERTAINTY_METERS
    ]


def selectable_label_candidates(candidates, options: SelectionOptions):
    label_candidates = [
        candidate for candidate in candidates if candidate.candidate_source.startswith("label:")
    ]
    if options.curation_mode != "robust":
        return label_candidates
    return [
        candidate
        for candidate in label_candidates
        if 0 < number_or(candidate.candidate_uncertainty_meters, float("inf"))
        <= ROBUST_LABEL_MAX_UNCERTAINTY_METERS
        and is_wgs84(candidate)
    ]


def select_result(
    row,
    label: LabelRead,
    raw_label_coordinates,
    llm_candidates,
    gazetteer_matches,
    insufficient_locality: str,
    exclude_insufficient_locality: bool,
    options: SelectionOptions,
):
    status = original_coordinate_status(row, options.original_precision_decimals)
    candidates = []
    original = original_candidate(row, label, status)
    if original:
        candidates.append(original)
    candidates.extend(label_coordinate_candidates(raw_label_coordinates, label))
    candidates.extend(llm_candidates)
    candidates.extend(gazetteer_coordinate_candidates(gazetteer_matches, label))
    eligible_route_dem = selectable_route_dem_candidates(candidates, options)
    eligible_llm = selectable_llm_candidates(candidates, options)
    eligible_web_anchor = selectable_web_anchor_candidates(candidates, options)
    eligible_label = selectable_label_candidates(candidates, options)

    decision = "unresolved"
    verification_status = "needs_georeferencing"
    selected = None
    notes = []

    if status == "precise" and original:
        decision = "keep_original"
        verification_status = "accepted_original"
        selected = original
        for candidate in [item for item in candidates if item is not original]:
            distance = haversine_km(
                float(original.candidate_latitude),
                float(original.candidate_longitude),
                float(candidate.candidate_latitude),
                float(candidate.candidate_longitude),
            )
            if distance > options.review_distance_km:
                verification_status = "review_original_candidate_conflict"
                notes.append(f"Candidate coordinate is {distance:.1f} km from precise original coordinates.")
                break
    elif eligible_route_dem:
        selected = best_candidate(eligible_route_dem)
        decision = "infer_missing" if status == "missing" else "correct_existing"
        verification_status = "selected_route_dem_refinement"
        for label_candidate in [
            item for item in candidates if item.candidate_source.startswith("label:")
        ]:
            distance = haversine_km(
                float(selected.candidate_latitude),
                float(selected.candidate_longitude),
                float(label_candidate.candidate_latitude),
                float(label_candidate.candidate_longitude),
            )
            anchor_radius_km = max(
                options.review_distance_km,
                number_or(label_candidate.candidate_uncertainty_meters, 0.0) / 1000.0,
            )
            if distance > anchor_radius_km:
                verification_status = "review_route_dem_refinement_conflicts_verbatim_coordinate"
                notes.append(
                    f"Route/DEM point estimate is {distance:.1f} km from the verbatim "
                    "label coordinate; the label coordinate may be erroneous."
                )
                break
    elif eligible_llm:
        selected = best_candidate(eligible_llm)
        decision = "infer_missing" if status == "missing" else "correct_existing"
        verification_status = "selected_llm_web_georeference"
        for label_candidate in [
            item for item in candidates if item.candidate_source.startswith("label:")
        ]:
            distance = haversine_km(
                float(selected.candidate_latitude),
                float(selected.candidate_longitude),
                float(label_candidate.candidate_latitude),
                float(label_candidate.candidate_longitude),
            )
            anchor_radius_km = max(
                options.review_distance_km,
                number_or(label_candidate.candidate_uncertainty_meters, 0.0) / 1000.0,
            )
            if distance > anchor_radius_km:
                verification_status = "review_refined_candidate_conflicts_verbatim_coordinate"
                notes.append(
                    f"Refined web-georeference is {distance:.1f} km from the verbatim "
                    "label coordinate; the label coordinate may be erroneous."
                )
                break
    elif eligible_web_anchor:
        selected = best_candidate(eligible_web_anchor)
        decision = "infer_missing" if status == "missing" else "correct_existing"
        verification_status = "review_selected_llm_web_locality_anchor"
        notes.append(
            "No defensible exact collecting point was found; selected the best "
            "web-supported specific locality anchor as the point estimate."
        )
    elif eligible_label:
        selected = best_candidate(eligible_label)
        decision = "infer_missing" if status == "missing" else "correct_existing"
        verification_status = "selected_label_coordinate"
    elif gazetteer_matches:
        selected = next(item for item in candidates if item.candidate_source.startswith("gazetteer:"))
        decision = "infer_missing" if status == "missing" else "correct_existing"
        verification_status = "selected_locality_gazetteer"
    elif original and status == "coarse":
        if options.curation_mode == "robust":
            decision = "unresolved"
            verification_status = "review_coarse_original_not_accepted"
            notes.append("Original coordinates are coarse and no corroborating candidate was found; robust mode leaves final DwC coordinates blank.")
        else:
            decision = "keep_original"
            verification_status = "review_coarse_original"
            selected = original
            notes.append("Original coordinates are coarse and no better candidate was found.")
    elif original:
        if options.curation_mode == "robust":
            decision = "unresolved"
            verification_status = f"review_{status}_original_not_accepted"
            notes.append(f"Original coordinates have status={status}; robust mode leaves final DwC coordinates blank.")
        else:
            decision = "keep_original"
            verification_status = f"review_{status}"
            selected = original
            notes.append(f"Original coordinates have status={status}.")

    include_in_dwc = True
    exclusion_reason = ""
    locality_evidence_evaluated = (
        not label.image_path
        or label.label_status
        in {"transcribed", "llm_image_transcribed", "llm_text_augmented"}
    )
    if (
        exclude_insufficient_locality
        and insufficient_locality
        and locality_evidence_evaluated
        and not (original and status == "precise")
        and not eligible_route_dem
        and not eligible_llm
        and not eligible_web_anchor
        and not eligible_label
        and not gazetteer_matches
    ):
        decision = "exclude_insufficient_locality"
        verification_status = "excluded_insufficient_locality"
        selected = None
        include_in_dwc = False
        exclusion_reason = insufficient_locality
        notes.append(f"Excluded from final DwC: {insufficient_locality}.")

    if not candidates:
        candidates.append(
            CoordinateCandidate(
                **candidate_base(label),
                evidence="No usable coordinate was found in DwC, label transcription, LLM, or gazetteer evidence.",
                selected="false",
                decision=decision,
                verification_status=verification_status,
                candidate_source="none",
                remarks="Needs label reading/georeferencing review.",
            )
        )

    selected_index = -1
    for index, candidate in enumerate(candidates, start=1):
        if not candidate.habitat_prior:
            candidate.habitat_prior = options.habitat_prior
        candidate.candidate_rank = index
        candidate.decision = decision
        candidate.verification_status = verification_status
        candidate.selected = "true" if candidate is selected else "false"
        if candidate is selected:
            selected_index = index
    if selected_index > 1:
        candidates.insert(0, candidates.pop(selected_index - 1))
        for index, candidate in enumerate(candidates, start=1):
            candidate.candidate_rank = index

    curated = dict(row)
    has_selected_coordinate = bool(selected and selected.candidate_latitude and selected.candidate_longitude)
    if has_selected_coordinate:
        curated["decimalLatitude"] = selected.candidate_latitude
        curated["decimalLongitude"] = selected.candidate_longitude
        curated["coordinateUncertaintyInMeters"] = selected.candidate_uncertainty_meters
        curated["geodeticDatum"] = selected.candidate_geodetic_datum or "WGS84"
        curated["georeferencedBy"] = options.georeferenced_by
        curated["georeferencedDate"] = options.georeferenced_date
        curated["georeferenceProtocol"] = options.protocol
        selected_sources = options.sources
        if selected.source_urls:
            selected_sources += f" | {selected.source_urls}"
        curated["georeferenceSources"] = selected_sources
    else:
        curated["decimalLatitude"] = ""
        curated["decimalLongitude"] = ""
        curated["coordinateUncertaintyInMeters"] = ""
        curated["geodeticDatum"] = ""
        curated.setdefault("georeferencedBy", "")
        curated.setdefault("georeferencedDate", "")
        curated.setdefault("georeferenceProtocol", "")
        curated.setdefault("georeferenceSources", "")

    label_elevation_minimum, label_elevation_maximum = parse_elevation_meters(
        label.elevation_text
        or label.label_transcription
        or curated.get("verbatimElevation", "")
    )
    elevation_note = ""
    if label_elevation_minimum is not None and label_elevation_maximum is not None:
        curated["minimumElevationInMeters"] = str(label_elevation_minimum)
        curated["maximumElevationInMeters"] = str(label_elevation_maximum)
        elevation_note = "elevation_source=specimen_label"
    else:
        selected_elevation = (
            number_or(selected.candidate_elevation_meters, float("nan"))
            if selected
            else float("nan")
        )
        if selected_elevation == selected_elevation:
            rounded_elevation = round_elevation(
                selected_elevation,
                INFERRED_ELEVATION_GRANULARITY_METERS,
            )
            curated["minimumElevationInMeters"] = str(rounded_elevation)
            curated["maximumElevationInMeters"] = str(rounded_elevation)
            elevation_note = (
                "elevation_source=estimated; "
                f"elevation_granularity_m={INFERRED_ELEVATION_GRANULARITY_METERS}"
            )

    remarks = [
        f"VASCULUM decision={decision}",
        f"verification={verification_status}",
        f"label_status={label.label_status}",
    ]
    if selected:
        remarks.append(
            f"candidate_type={selected.candidate_type or 'unspecified'}"
        )
        if selected.candidate_uncertainty_meters:
            remarks.append(
                f"uncertainty_m={selected.candidate_uncertainty_meters}"
            )
    if elevation_note:
        remarks.append(elevation_note)
    remarks.extend(notes)
    curated["georeferenceRemarks"] = "; ".join(remarks)

    return CuratedResult(
        row=curated,
        candidates=candidates,
        decision=decision,
        verification_status=verification_status,
        selected_candidate=selected,
        notes=notes,
        include_in_dwc=include_in_dwc,
        exclusion_reason=exclusion_reason,
    )
