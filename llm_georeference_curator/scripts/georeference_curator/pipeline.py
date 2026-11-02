from __future__ import annotations

import os
import json
import math
import re
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .geocoding import (
    extract_decimal_coordinates,
    is_precise_label_coordinate,
    match_gazetteer,
    read_gazetteer,
)
from .checkpoints import RecordJournal, file_hash, fingerprint, implementation_signature
from .geospatial_refinement import (
    GeospatialRefinementCache,
    GeospatialRefinementSettings,
    refine_llm_candidates,
    review_selected_habitat,
    selected_dem_elevation,
)
from .habitat import parse_habitats
from .image_quality import inspect_image
from .image_exports import export_final_images
from .inputs import catalog_number, find_image_path, label_image_paths, read_label_sidecar
from .personal_inputs import load_inputs
from .manual_review import find_miss_markers, mark_misidentification
from .labels import read_label
from .llm import (
    DEFAULT_LLM_PROVIDER,
    DEFAULT_MODEL_AUTO,
    DEFAULT_REASONING_EFFORT,
    PROMPT_VERSION,
    LlmSettings,
    apply_llm_label_fields,
    build_transcription_prompt,
    build_user_prompt,
    cache_prompt,
    configured_model_request,
    llm_response_to_candidates,
    make_client,
    model_candidates_for,
    normalize_provider,
    preflight_llm,
    selected_model_label,
    validate_llm_settings,
)
from .llm_cache import LlmResponseCache
from .locality_quality import insufficient_locality_reason
from .models import RunReport, CoordinateCandidate, CuratedResult
from .outputs import merged_fieldnames, write_candidates, write_dwc_exports, write_json_log, write_summary
from .parallel import RateLimitBackoff, is_rate_limit_error, resolve_worker_count
from .progress import TerminalProgress
from .scoring import SelectionOptions, candidate_base, original_coordinate_status, select_result
from .policy import read_reference_regions
from .sources import SourceAuditor, audit_candidates
from .ribbon import prepare_ribbon_renderer, renderer_provenance, ribbon_counts, write_ribbon_plot


def curator_version(project_dir: Path) -> str:
    version_file = project_dir / "VERSION.txt"
    return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "unknown"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def default_output_dir(project_dir: Path, input_dir: Path) -> Path:
    return project_dir / "output" / f"{input_dir.name}_georeferenced"


@dataclass
class RowOutcome:
    index: int
    result: object
    candidates: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    geospatial_refinement_warnings: list = field(default_factory=list)
    llm_usage: list = field(default_factory=list)
    stage_errors: list = field(default_factory=list)
    checkpoint_reused: bool = False
    image_records: int = 0
    image_review_records: int = 0
    image_missing_records: int = 0
    llm_attempted: int = 0
    llm_candidate_rows: int = 0
    llm_errors: int = 0
    llm_rate_limit_retries: int = 0
    llm_rate_limit_waits: int = 0
    llm_cache_hits: int = 0
    llm_transcription_requests: int = 0
    llm_georeference_requests: int = 0
    llm_skipped_precise_original: int = 0
    llm_skipped_precise_label: int = 0
    llm_skipped_insufficient_locality: int = 0
    geospatial_refinement_attempted: int = 0
    geospatial_refinement_succeeded: int = 0
    habitat_checks_attempted: int = 0
    habitat_review_warnings: int = 0
    habitat_review_unknown: int = 0
    habitat_review_unavailable: int = 0


def protocol_text(
    version: str,
    web_search_mode: str,
    use_trails: bool,
    use_hydrology: bool,
    use_dem: bool,
    use_vegetation_prior: bool,
    taxon_habitat: str,
) -> str:
    priors = []
    if use_trails:
        priors.append("trails")
    if use_hydrology:
        priors.append("hydrology")
    if use_dem:
        priors.append("dem")
    if use_vegetation_prior:
        priors.append("recorded_vegetation")
    prior_text = ",".join(priors) if priors else "none"
    habitat = f"; habitat_review_only={taxon_habitat}" if taxon_habitat else ""
    return (
        f"VASCULUM llm_georeference_curator {version}; "
        "export_policy=accepted_only; "
        f"input_policy=photo_label_first; configured_web_search={web_search_mode}; "
        f"configured_evidence={prior_text}{habitat}"
    )


def create_llm_json_with_backoff(
    llm_client,
    user_prompt: str,
    image_paths: list,
    *,
    backoff: RateLimitBackoff,
    retries: int,
    progress: TerminalProgress,
    catalog: str,
    settings: LlmSettings,
    purpose: str,
    cache: LlmResponseCache,
):
    deadline = time.monotonic() + settings.timeout_seconds
    cache_key = cache.key(
        purpose=purpose,
        provider=settings.provider,
        model=selected_model_label(settings),
        reasoning_effort=settings.reasoning_effort,
        web_search_mode=settings.web_search_mode,
        prompt=cache_prompt(settings, user_prompt),
        image_paths=image_paths,
    )
    # Recheck inside the per-request lock so parallel duplicates share one call.
    with cache.request_lock(cache_key):
        cached = cache.get(cache_key)
        if cached is not None:
            return cached, 0, 0, True
        attempts = 0
        while True:
            backoff.wait(deadline=deadline)
            try:
                if time.monotonic() >= deadline:
                    raise TimeoutError("LLM stage time limit reached")
                if hasattr(llm_client, "request_gate"):
                    llm_client.request_gate = backoff.reserve_call
                else:
                    backoff.reserve_call()
                call_started = time.monotonic()
                if hasattr(llm_client, "request_gate"):
                    response = llm_client.create_json(user_prompt, image_paths=image_paths, deadline=deadline)
                else:
                    response = llm_client.create_json(user_prompt, image_paths=image_paths)
                response["_vasculum_request"] = {
                    "purpose": purpose, "provider": settings.provider,
                    "model": response.get("_vasculum_model") or selected_model_label(settings),
                    "reasoningEffort": settings.reasoning_effort, "webSearch": settings.web_search_mode,
                    "promptVersion": PROMPT_VERSION, "promptHash": fingerprint(cache_prompt(settings, user_prompt)),
                    "responseHash": fingerprint(response), "completedAt": datetime.now().astimezone().isoformat(),
                    "elapsedSeconds": round(time.monotonic() - call_started, 3),
                    "prompt": cache_prompt(settings, user_prompt), "responseCacheKey": cache_key,
                }
                backoff.note_success()
                cache.put(cache_key, response)
                return response, attempts, attempts, False
            except Exception as exc:
                if attempts < retries and is_rate_limit_error(exc):
                    attempts += 1
                    delay = backoff.note_rate_limit()
                    progress.update(
                        f"Rate limit/usage limit for {catalog}; retry {attempts}/{retries} "
                        f"after about {int(delay)}s."
                    )
                    continue
                raise


def coordinate_evidence_text(label) -> str:
    return "\n".join(filter(None, (label.label_transcription, label.locality_text)))


def record_llm_usage(outcome, response, settings, *, stage: str, cache_hit: bool):
    outcome.llm_usage.append({
        "stage": stage,
        "model": response.get("_vasculum_model") or selected_model_label(settings),
        "reasoningEffort": settings.reasoning_effort,
        "cacheHit": cache_hit,
        "usage": {} if cache_hit else response.get("_vasculum_usage", {}),
        "request": response.get("_vasculum_request", {}),
    })


def screened_outcome(outcome, row, label, *, status, decision, reason):
    candidate = CoordinateCandidate(
        **candidate_base(label), candidate_source="input_screening",
        curation_status=status, curation_reasons=reason,
        decision=decision, verification_status=status,
        original_latitude=row.get("decimalLatitude", ""),
        original_longitude=row.get("decimalLongitude", ""),
        original_datum=row.get("geodeticDatum", ""),
        input_type=row.get("vasculumInputType", ""),
        input_source=row.get("vasculumInputSource", ""),
    )
    outcome.result = CuratedResult(
        row=dict(row, georeferenceVerificationStatus=status), candidates=[candidate],
        decision=decision, verification_status=status, curation_status=status,
        include_in_dwc=False, exclusion_reason=reason, notes=[reason],
        original_coordinates={key: row.get(key, "") for key in (
            "decimalLatitude", "decimalLongitude", "geodeticDatum", "coordinateUncertaintyInMeters")},
        provenance={"inputPolicy": "photo_label_first"},
        stage_errors=outcome.stage_errors, llm_usage=outcome.llm_usage,
    )
    outcome.candidates = [candidate]
    return outcome


def process_row(
    *,
    index: int,
    total_rows: int,
    row,
    source_dir: Path,
    sidecars,
    gazetteer,
    llm_client,
    llm_settings: LlmSettings,
    transcription_llm_client,
    transcription_llm_settings: LlmSettings,
    llm_cache: LlmResponseCache,
    llm_rate_limit_retries: int,
    llm_backoff: RateLimitBackoff,
    refinement_settings: GeospatialRefinementSettings,
    verification_timeout_seconds: int,
    habitat_preference,
    normalized_habitat: str,
    options: SelectionOptions,
    use_trails: bool,
    use_hydrology: bool,
    use_dem: bool,
    use_vegetation_prior: bool,
    progress: TerminalProgress,
    source_auditor=None,
):
    catalog = catalog_number(row)
    outcome = RowOutcome(index=index, result=None)
    if row.get("_vasculumMissMarkers"):
        reason = "Manual misidentification exclusion (miss_ filename): " + " | ".join(row["_vasculumMissMarkers"])
        label = read_label(row, catalog, "", sidecars)
        candidate = CoordinateCandidate(
            catalog_number=catalog, occurrence_id=row.get("occurrenceID", ""),
            image_paths=" | ".join(row["_vasculumMissMarkers"]),
            label_transcription=label.label_transcription, locality_text=label.locality_text,
            label_status="not_read_manual_exclusion", input_type=row.get("vasculumInputType", ""),
            input_source=row.get("vasculumInputSource", ""), candidate_source="human_filename_review",
            curation_status="excluded", curation_reasons=reason, selected="false",
            decision="exclude_manual_misidentification", verification_status="excluded_manual_misidentification",
            original_latitude=row.get("decimalLatitude", ""), original_longitude=row.get("decimalLongitude", ""),
            original_datum=row.get("geodeticDatum", ""),
        )
        result = CuratedResult(
            row=dict(row, georeferenceVerificationStatus="excluded"), candidates=[candidate],
            decision=candidate.decision, verification_status=candidate.verification_status,
            curation_status="excluded", include_in_dwc=False, exclusion_reason=reason, notes=[reason],
            original_coordinates={key: row.get(key, "") for key in ("decimalLatitude", "decimalLongitude", "geodeticDatum", "coordinateUncertaintyInMeters")},
            provenance={"manualReview": "miss_filename", "markers": row["_vasculumMissMarkers"]},
        )
        outcome.result, outcome.candidates = result, [candidate]
        return outcome
    image_path = find_image_path(source_dir, row)
    label = read_label(row, catalog, image_path, sidecars)
    label.image_paths = label_image_paths(source_dir, row, sidecars)
    if label.image_paths:
        label.image_path = label.image_paths[0]
    label.occurrence_id = row.get("occurrenceID", "")
    image_quality = inspect_image(source_dir, label.image_path)
    label.image_width = image_quality.width
    label.image_height = image_quality.height
    label.image_file_size_bytes = image_quality.file_size_bytes
    label.image_quality_status = image_quality.status
    label.image_quality_remarks = image_quality.remarks
    if label.image_path:
        outcome.image_records += 1
    if image_quality.status == "image_missing":
        outcome.image_missing_records += 1
    elif image_quality.status.startswith("review_") or image_quality.status.endswith("_unknown"):
        outcome.image_review_records += 1

    original_status = original_coordinate_status(row, options.reference_regions)
    if original_status != "trusted" and not label.image_paths:
        return screened_outcome(outcome, row, label, status="excluded",
            decision="exclude_missing_source_image",
            reason="No local specimen photograph and no trusted existing coordinates.")
    raw_coordinates, gazetteer_matches, llm_candidates = [], [], []
    insufficient_reason = ""

    has_untranscribed_image = bool(label.image_paths and label.label_status in {"image_not_transcribed", "dwc_text_only"})
    if original_status == "trusted":
        outcome.llm_skipped_precise_original += 1
    elif has_untranscribed_image and transcription_llm_client:
        transcription_message = (
            f"Label transcription {transcription_llm_settings.provider}:"
            f"{selected_model_label(transcription_llm_settings)} "
            f"{index}/{total_rows} {catalog}"
        )
        try:
            outcome.llm_attempted += 1
            outcome.llm_transcription_requests += 1
            image_paths = [(source_dir / path).resolve() for path in label.image_paths] or [image_quality.path]
            with progress.activity(
                transcription_message,
                stage="Label reading",
                index=index,
                total=total_rows,
                catalog=catalog,
                model=(
                    f"{transcription_llm_settings.provider}:"
                    f"{selected_model_label(transcription_llm_settings)}"
                ),
            ):
                response, rate_retries, rate_waits, cache_hit = create_llm_json_with_backoff(
                    transcription_llm_client,
                    build_transcription_prompt(row, label),
                    image_paths,
                    backoff=llm_backoff,
                    retries=llm_rate_limit_retries,
                    progress=progress,
                    catalog=catalog,
                    settings=transcription_llm_settings,
                    purpose="transcription_only",
                    cache=llm_cache,
                )
            outcome.llm_rate_limit_retries += rate_retries
            outcome.llm_rate_limit_waits += rate_waits
            outcome.llm_cache_hits += int(cache_hit)
            record_llm_usage(outcome, response, transcription_llm_settings, stage="transcription", cache_hit=cache_hit)
            if cache_hit:
                outcome.llm_attempted -= 1
                outcome.llm_transcription_requests -= 1
            apply_llm_label_fields(label, response)
        except Exception as exc:
            outcome.llm_errors += 1
            outcome.errors.append(f"{catalog}: label transcription failed: {exc}")
            outcome.stage_errors.append({"stage": "label_reading", "retryable": True, "error": str(exc)})

    if original_status != "trusted":
        if outcome.stage_errors:
            return screened_outcome(outcome, row, label, status="failed", decision="unresolved",
                reason="Label reading failed; retry required. DwC locality is not a fallback.")
        if label.label_status == "image_conflict":
            return screened_outcome(outcome, row, label, status="review", decision="unresolved",
                reason="Conflicting collecting events in specimen labels/views require human review.")
        if label.label_status == "image_unreadable":
            return screened_outcome(outcome, row, label, status="review", decision="unresolved",
                reason="Collecting label is unreadable; no coordinate research was attempted.")
        if label.label_status not in {"transcribed", "llm_image_transcribed"} or not (
            label.label_transcription.strip() or label.locality_text.strip()
        ):
            return screened_outcome(outcome, row, label, status="unresolved", decision="unresolved",
                reason="Specimen photograph has not yielded a label transcription.")
        raw_coordinates = extract_decimal_coordinates(coordinate_evidence_text(label))
        # DwC provides administrative context, never replacement label locality.
        label_context = {key: row.get(key, "") for key in ("country", "stateProvince")}
        insufficient_reason = insufficient_locality_reason(label_context, label)
        if not label.locality_text.strip() and not any(is_precise_label_coordinate(value) for value in raw_coordinates):
            return screened_outcome(outcome, row, label, status="review", decision="unresolved",
                reason="No collecting locality could be read from the specimen label.")
        if insufficient_reason and not any(is_precise_label_coordinate(value) for value in raw_coordinates):
            outcome.llm_skipped_insufficient_locality += 1
            return screened_outcome(outcome, row, label, status="excluded",
                decision="exclude_insufficient_locality", reason=insufficient_reason)
        gazetteer_matches = match_gazetteer(label_context,
            "\n".join(filter(None, (label.label_transcription, label.locality_text))), gazetteer)

    precise_label_coordinates = [
        coordinate for coordinate in raw_coordinates if is_precise_label_coordinate(coordinate)
    ]
    has_searchable_text = bool(label.locality_text.strip() or label.label_transcription.strip())
    should_call_georeference_llm = bool(
        llm_client
        and original_status != "trusted"
        and not precise_label_coordinates
        and has_searchable_text
        and not insufficient_reason
        and bool(label.image_paths)
        and label.label_status in {"transcribed", "llm_image_transcribed"}
    )
    if (
        llm_client
        and original_status != "trusted"
        and precise_label_coordinates
    ):
        outcome.llm_skipped_precise_label += 1
    elif (
        llm_client
        and original_status != "trusted"
        and not should_call_georeference_llm
        and insufficient_reason
    ):
        outcome.llm_skipped_insufficient_locality += 1

    if should_call_georeference_llm:
        llm_progress_message = (
            f"Georeference {llm_settings.provider}:{selected_model_label(llm_settings)} "
            f"{index}/{total_rows} {catalog}"
        )
        try:
            outcome.llm_attempted += 1
            outcome.llm_georeference_requests += 1
            with progress.activity(
                llm_progress_message,
                stage="LLM georeference",
                index=index,
                total=total_rows,
                catalog=catalog,
                model=f"{llm_settings.provider}:{selected_model_label(llm_settings)}",
            ):
                response, rate_retries, rate_waits, cache_hit = create_llm_json_with_backoff(
                    llm_client,
                    build_user_prompt(
                        row,
                        label,
                        use_trails=use_trails,
                        use_hydrology=use_hydrology,
                        use_dem=use_dem,
                        use_vegetation_prior=use_vegetation_prior,
                    ),
                    [],
                    backoff=llm_backoff,
                    retries=llm_rate_limit_retries,
                    progress=progress,
                    catalog=catalog,
                    settings=llm_settings,
                    purpose="georeference",
                    cache=llm_cache,
                )
            outcome.llm_rate_limit_retries += rate_retries
            outcome.llm_rate_limit_waits += rate_waits
            outcome.llm_cache_hits += int(cache_hit)
            record_llm_usage(outcome, response, llm_settings, stage="georeference", cache_hit=cache_hit)
            if cache_hit:
                outcome.llm_attempted -= 1
                outcome.llm_georeference_requests -= 1
            # Research returns candidates; the original transcription stays authoritative.
            effective_model = str(response.get("_vasculum_model") or selected_model_label(llm_settings))
            llm_candidates = llm_response_to_candidates(response, label, effective_model)
            outcome.llm_candidate_rows += len(llm_candidates)
        except Exception as exc:
            outcome.llm_errors += 1
            outcome.errors.append(f"{catalog}: LLM georeferencing failed: {exc}")
            outcome.stage_errors.append({"stage": "coordinate_research", "retryable": True, "error": str(exc)})

    refinement_cache = GeospatialRefinementCache()
    row_refinement_settings = replace(
        refinement_settings,
        timeout_seconds=verification_timeout_seconds,
        deadline_monotonic=time.monotonic() + verification_timeout_seconds,
        audit_records=[],
    )
    if source_auditor is not None and llm_candidates:
        with progress.activity(f"Source audit {catalog}", stage="Source audit", index=index, total=total_rows, catalog=catalog, model="HTTP source snapshots"):
            audit_candidates(llm_candidates, source_auditor, timeout=min(15, verification_timeout_seconds), deadline=row_refinement_settings.deadline_monotonic)
    for _refinement_index in range(
        len(llm_candidates) if row_refinement_settings.enabled else 0
    ):
        refinement_message = (
            f"Coordinate verification {index}/{total_rows} {catalog}"
        )
        with progress.activity(
            refinement_message,
            stage="Coordinate verification",
            index=index,
            total=total_rows,
            catalog=catalog,
            model="Overpass/Open-Meteo",
        ):
            refinement = refine_llm_candidates(
                llm_candidates,
                label,
                row_refinement_settings,
                cache=refinement_cache,
            )
        if not refinement.attempted:
            break
        outcome.geospatial_refinement_attempted += 1
        if refinement.warning:
            outcome.geospatial_refinement_warnings.append(f"{catalog}: {refinement.warning}")
        if refinement.candidate:
            llm_candidates.append(refinement.candidate)
            outcome.geospatial_refinement_succeeded += 1
            break
        break

    result = select_result(
        row=row,
        label=label,
        raw_label_coordinates=raw_coordinates,
        llm_candidates=llm_candidates,
        gazetteer_matches=gazetteer_matches,
        insufficient_locality=insufficient_reason,
        options=options,
    )
    result.stage_errors = outcome.stage_errors
    result.provenance = {"inputPolicy": "photo_label_first", "sourceEvidenceEligible": True,
                         "promptVersion": PROMPT_VERSION, "stages": outcome.llm_usage}
    if use_dem and row_refinement_settings.enabled and result.selected_candidate:
        try:
            with progress.activity(f"DEM elevation {catalog}", stage="Coordinate verification", index=index, total=total_rows, catalog=catalog, model="Open-Meteo/Copernicus DEM"):
                elevation = selected_dem_elevation(result.selected_candidate, label, row_refinement_settings, refinement_cache)
            if elevation is not None:
                result.row["minimumElevationInMeters"] = result.row["maximumElevationInMeters"] = str(elevation)
                result.selected_candidate.candidate_elevation_meters = str(elevation)
                result.row["georeferenceRemarks"] += "; elevation_source=DEM; elevation_granularity_m=10; vertical_accuracy_not_guaranteed"
        except Exception as exc:
            outcome.geospatial_refinement_warnings.append(f"{catalog}: DEM elevation unavailable: {exc}")
    if outcome.stage_errors and result.curation_status != "accepted":
        result.curation_status = "failed"
        result.decision = "unresolved"
        result.verification_status = "failed_processing"
        result.exclusion_reason = ""
        result.row["georeferenceVerificationStatus"] = "failed"
        result.row["georeferenceRemarks"] += "; processing_failed_retryable; not an insufficient-locality conclusion"
        result.include_in_dwc = False
        for candidate in result.candidates:
            candidate.decision = result.decision
            candidate.verification_status = result.verification_status
    if habitat_preference.enabled:
        with progress.activity(
            f"Habitat review {index}/{total_rows} {catalog}",
            stage="Habitat review",
            index=index,
            total=total_rows,
            catalog=catalog,
            model="Overpass/Open-Meteo",
        ):
            review = review_selected_habitat(
                result.selected_candidate,
                habitat_preference,
                row_refinement_settings,
                cache=refinement_cache,
            )
        result.habitat_review = {
            "status": review.status, "remarks": review.remarks, "sources": review.sources,
        }
        outcome.habitat_checks_attempted += int(review.attempted)
        outcome.habitat_review_warnings += int(review.status == "warning")
        outcome.habitat_review_unknown += int(review.status == "unknown")
        outcome.habitat_review_unavailable += int(review.status == "unavailable")
        result.row["georeferenceRemarks"] += f"; habitat_review={review.status}; {review.remarks}"
        if result.selected_candidate:
            result.selected_candidate.habitat_review_status = review.status
            result.selected_candidate.habitat_review_remarks = review.remarks
            result.selected_candidate.habitat_review_sources = review.sources
        if review.status == "warning":
            result.notes.append(f"Habitat review warning: {review.remarks}")
            progress.update(f"Habitat review warning: {catalog} | coordinates retained | {review.remarks}")
    outcome.result = result
    result.provenance["geospatialSnapshots"] = row_refinement_settings.audit_records
    result.llm_usage = outcome.llm_usage
    outcome.candidates = result.candidates
    return outcome


def aggregate_outcome(report: RunReport, outcome: RowOutcome):
    result = outcome.result
    report.accepted += int(result.curation_status == "accepted")
    report.review += int(result.curation_status == "review")
    report.failed += int(result.curation_status == "failed")
    report.resumed_records += int(outcome.checkpoint_reused)
    report.image_records += outcome.image_records
    report.image_review_records += outcome.image_review_records
    report.image_missing_records += outcome.image_missing_records
    report.llm_attempted += outcome.llm_attempted
    report.llm_candidate_rows += outcome.llm_candidate_rows
    report.llm_errors += outcome.llm_errors
    report.llm_rate_limit_retries += outcome.llm_rate_limit_retries
    report.llm_rate_limit_waits += outcome.llm_rate_limit_waits
    report.llm_cache_hits += outcome.llm_cache_hits
    for entry in outcome.llm_usage:
        usage = entry["usage"]
        if usage:
            report.llm_usage_reported_requests += 1
            report.llm_input_tokens += usage.get("input_tokens", 0)
            report.llm_cached_input_tokens += usage.get("cached_input_tokens", 0)
            report.llm_output_tokens += usage.get("output_tokens", 0)
    report.llm_transcription_requests += outcome.llm_transcription_requests
    report.llm_georeference_requests += outcome.llm_georeference_requests
    report.llm_skipped_precise_original += outcome.llm_skipped_precise_original
    report.llm_skipped_precise_label += outcome.llm_skipped_precise_label
    report.llm_skipped_insufficient_locality += outcome.llm_skipped_insufficient_locality
    report.geospatial_refinement_attempted += outcome.geospatial_refinement_attempted
    report.geospatial_refinement_succeeded += outcome.geospatial_refinement_succeeded
    report.habitat_checks_attempted += outcome.habitat_checks_attempted
    report.habitat_review_warnings += outcome.habitat_review_warnings
    report.habitat_review_unknown += outcome.habitat_review_unknown
    report.habitat_review_unavailable += outcome.habitat_review_unavailable
    report.geospatial_refinement_warnings.extend(outcome.geospatial_refinement_warnings)
    report.errors.extend(outcome.errors)

    if not result.include_in_dwc:
        report.excluded += 1
        if result.decision == "exclude_insufficient_locality":
            report.excluded_insufficient_locality += 1
        elif result.decision == "exclude_manual_misidentification":
            report.excluded_manual_misidentification += 1
        elif result.decision == "exclude_missing_source_image":
            report.excluded_missing_source_image += 1
    if result.include_in_dwc and result.decision == "keep_original":
        report.kept_original += 1
    elif result.include_in_dwc and result.decision == "correct_existing":
        report.corrected_existing += 1
    elif result.include_in_dwc and result.decision == "infer_missing":
        report.inferred_missing += 1
    if result.curation_status == "unresolved":
        report.unresolved += 1
    if "review" in result.verification_status or result.habitat_review.get("status") == "warning":
        report.review_required += 1


def run_pipeline(
    project_dir: Path,
    input_dir: Path,
    input_dwc,
    output_dir,
    label_tsv,
    gazetteer_tsv,
    dry_run: bool,
    review_distance_km: float,
    llm_mode: str,
    llm_provider: str,
    llm_model: str,
    llm_reasoning_effort: str,
    llm_web_search: str,
    llm_command: str,
    llm_api_key_env: str,
    llm_rate_limit_retries: int,
    confirm_llm: bool,
    georeferenced_by: str,
    use_trails: bool,
    use_hydrology: bool,
    use_dem: bool,
    use_vegetation_prior: bool,
    taxon_habitat: str,
    geospatial_refinement: bool = True,
    limit: int = 0,
    workers: str = "auto",
    llm_cache_enabled: bool = True,
    llm_cache_dir=None,
    label_timeout_seconds=600,
    georeference_timeout_seconds=600,
    verification_timeout_seconds=600,
    label_model: str = "",
    label_reasoning_effort: str = "",
    max_uncertainty_meters: float | None = None,
    label_coordinate_uncertainty_meters: float | None = None,
    reference_regions_tsv=None,
    resume: bool = True,
    verify_source_urls: bool = True,
    refresh_sources: bool = False,
    max_llm_calls: int = 0,
    additional_inputs=(),
    include_personal: bool = True,
    import_unlinked_images: bool = False,
    export_images: bool = False,
    progress=None,
):
    load_env_file(project_dir / ".env")
    progress = progress or TerminalProgress()
    if max_llm_calls < 0:
        raise ValueError("max_llm_calls must not be negative")
    for name, value in (("max_uncertainty_meters", max_uncertainty_meters), ("label_coordinate_uncertainty_meters", label_coordinate_uncertainty_meters)):
        if value is not None and (value <= 0 or not math.isfinite(value)):
            raise ValueError(f"{name} must be finite and positive")
    reference_regions = read_reference_regions(reference_regions_tsv.expanduser().resolve() if reference_regions_tsv else None)
    habitat_preference = parse_habitats(taxon_habitat)
    normalized_habitat = habitat_preference.display
    source_dir = input_dir.expanduser().resolve()
    if not source_dir.exists():
        raise FileNotFoundError(f"Input directory was not found: {source_dir}")
    bundle = load_inputs(source_dir, input_dwc=input_dwc, additional_inputs=additional_inputs,
        include_personal=include_personal, import_unlinked_images=import_unlinked_images)
    dwc_path = bundle.dwc_path
    destination = output_dir.expanduser().resolve() if output_dir else default_output_dir(project_dir, source_dir).resolve()
    version = curator_version(project_dir)
    started_at = datetime.now().astimezone()
    report = RunReport(version=version, input_dir=source_dir, input_dwc=dwc_path, output_dir=destination, started_at=started_at)
    report.max_llm_calls = max_llm_calls

    rows, input_fieldnames = bundle.rows, bundle.fieldnames
    report.personal_dwc_records = bundle.personal_dwc_records
    report.photo_only_records = bundle.photo_only_records
    report.attached_personal_images = bundle.attached_personal_images
    report.input_files = bundle.files
    report.input_warnings = bundle.warnings
    if limit and limit > 0:
        rows = rows[:limit]
    sidecars = read_label_sidecar(label_tsv.expanduser().resolve() if label_tsv else None)
    catalog_counts = Counter(catalog_number(row) for row in bundle.rows) if sidecars else {}
    for catalog in sidecars:
        if catalog_counts[catalog] > 1:
            raise ValueError(f"Label sidecar catalogNumber {catalog!r} matches multiple input records. Use unique specimen identifiers; no label was attached by guessing.")
    for row in rows:
        sidecar = sidecars.get(catalog_number(row), {})
        markers = find_miss_markers(source_dir, row, explicit_media=sidecar.get("imagePath") or sidecar.get("image_path") or "")
        if markers:
            mark_misidentification(row, markers, source_dir)
    gazetteer = read_gazetteer(gazetteer_tsv.expanduser().resolve() if gazetteer_tsv else None)

    needs_llm = any(not row.get("_vasculumMissMarkers") and
        original_coordinate_status(row, reference_regions) != "trusted" and
        label_image_paths(source_dir, row, sidecars) for row in rows)
    provider = normalize_provider(llm_provider or DEFAULT_LLM_PROVIDER)
    llm_settings = LlmSettings(
        mode=llm_mode if needs_llm else "off",
        provider=provider,
        model=configured_model_request(provider, llm_model or DEFAULT_MODEL_AUTO),
        reasoning_effort=(
            llm_reasoning_effort
            or (os.environ.get("OPENAI_REASONING_EFFORT") if provider == "openai" else "")
            or os.environ.get("LLM_REASONING_EFFORT")
            or DEFAULT_REASONING_EFFORT
        ),
        api_key_env=llm_api_key_env,
        command=llm_command or os.environ.get("LLM_COMMAND", ""),
        timeout_seconds=georeference_timeout_seconds,
        web_search_mode=llm_web_search,
        task_mode="georeference",
    )
    validate_llm_settings(llm_settings)
    llm_client = make_client(llm_settings) if needs_llm else None
    transcription_llm_settings = replace(
        llm_settings,
        model=label_model or os.environ.get("LLM_LABEL_MODEL") or llm_settings.model,
        reasoning_effort=label_reasoning_effort or os.environ.get("LLM_LABEL_REASONING_EFFORT") or "medium",
        web_search_mode="disabled",
        timeout_seconds=label_timeout_seconds,
        task_mode="transcription_only",
    )
    validate_llm_settings(transcription_llm_settings)
    transcription_llm_client = make_client(transcription_llm_settings) if needs_llm else None
    cache_directory = (
        llm_cache_dir.expanduser().resolve()
        if llm_cache_dir
        else (project_dir / ".cache" / "llm_georeference_curator").resolve()
    )
    response_cache = LlmResponseCache(
        cache_directory,
        enabled=llm_cache_enabled,
    )
    refinement_settings = GeospatialRefinementSettings(
        enabled=bool(geospatial_refinement),
        use_routes=use_trails,
        timeout_seconds=verification_timeout_seconds,
        snapshot_directory=str(cache_directory / "geospatial"),
        refresh_snapshots=refresh_sources,
    )

    report.records_read = len(rows)
    report.label_sidecar_rows = len(sidecars)
    report.gazetteer_rows = len(gazetteer)
    report.llm_provider = llm_settings.provider
    report.llm_model = selected_model_label(llm_settings)
    report.llm_reasoning_effort = llm_settings.reasoning_effort
    report.llm_web_search = llm_settings.web_search_mode
    report.label_model = selected_model_label(transcription_llm_settings)
    report.label_reasoning_effort = transcription_llm_settings.reasoning_effort
    report.label_timeout_seconds = label_timeout_seconds
    report.georeference_timeout_seconds = georeference_timeout_seconds
    report.verification_timeout_seconds = verification_timeout_seconds
    report.habitat_prior = normalized_habitat
    report.max_uncertainty_meters = max_uncertainty_meters
    worker_count = resolve_worker_count(
        workers,
        project_dir=project_dir,
        provider=llm_settings.provider,
        model_label=selected_model_label(llm_settings),
        web_search_mode=llm_settings.web_search_mode,
        records=len(rows),
    )
    report.worker_count = worker_count
    if hasattr(progress, "set_parallel"):
        progress.set_parallel(worker_count > 1)
    if hasattr(progress, "configure_run"):
        progress.configure_run(total_records=len(rows), worker_count=worker_count)
    if dry_run:
        report.finished_at = datetime.now().astimezone()
        return report
    ribbon_renderer = prepare_ribbon_renderer(project_dir)
    ribbon_runtime = renderer_provenance(project_dir, ribbon_renderer)
    progress.update(f"Input selection: {len(rows)} records | personal DwC={bundle.personal_dwc_records}, photo-only={bundle.photo_only_records}, additional photos={bundle.attached_personal_images}")
    if llm_client:
        progress.update(
            f"Label reading: {report.label_model} ({report.label_reasoning_effort}, no web)"
        )
        preflight_llm(
            llm_settings,
            confirm=confirm_llm,
            external_geospatial_services=refinement_settings.enabled,
        )

    protocol = protocol_text(
        version,
        llm_settings.web_search_mode,
        use_trails,
        use_hydrology,
        use_dem,
        use_vegetation_prior,
        normalized_habitat,
    )
    options = SelectionOptions(
        review_distance_km=review_distance_km,
        georeferenced_by=georeferenced_by,
        georeferenced_date=started_at.date().isoformat(),
        protocol=protocol,
        habitat_prior=normalized_habitat,
        max_uncertainty_meters=max_uncertainty_meters,
        label_coordinate_uncertainty_meters=label_coordinate_uncertainty_meters,
        reference_regions=reference_regions,
    )

    progress.update(f"Reading {len(rows)} DwC record(s) from {dwc_path}")
    progress.update(f"Personal inputs: {bundle.personal_dwc_records} DwC rows | {bundle.photo_only_records} photo-only specimens | {bundle.attached_personal_images} photos linked to existing records")
    for warning in bundle.warnings:
        progress.update(f"Input warning: {warning}")
    if normalized_habitat:
        progress.update(f"Habitat review only: {normalized_habitat}")
    progress.update(f"Workers: {worker_count}")
    progress.update(
        f"Label reading: {report.label_model} ({report.label_reasoning_effort}, no web) | "
        f"Coordinate research: {report.llm_model} ({report.llm_reasoning_effort})"
    )
    progress.update(
        "Stage timeouts: "
        f"label={label_timeout_seconds}s, "
        f"georeference={georeference_timeout_seconds}s, "
        f"verification={verification_timeout_seconds}s"
    )
    if llm_client and llm_cache_enabled:
        progress.update(f"LLM response cache: {cache_directory}")
    curated_rows = []
    all_candidates = []
    results = []
    outcomes = {}
    llm_backoff = RateLimitBackoff(retries=llm_rate_limit_retries, max_calls=max_llm_calls)

    configuration = {
        "implementation": implementation_signature(Path(__file__).parent),
        "promptVersion": PROMPT_VERSION, "inputPolicy": "photo_label_first",
        "selection": asdict(options), "research": asdict(llm_settings),
        "transcription": asdict(transcription_llm_settings),
        "refinement": asdict(refinement_settings), "habitat": normalized_habitat,
        "sidecars": fingerprint(sidecars), "gazetteer": fingerprint([asdict(entry) for entry in gazetteer]),
        "sourceAudit": verify_source_urls,
        "effectiveResearchModels": model_candidates_for(llm_settings),
        "effectiveTranscriptionModels": model_candidates_for(transcription_llm_settings),
        "curatorVersion": version,
        "ribbonRenderer": ribbon_runtime,
        "vasculumVersion": (project_dir.parent / "VERSION.txt").read_text().strip() if (project_dir.parent / "VERSION.txt").exists() else "unknown",
        "inputDwcSha256": file_hash(dwc_path) if dwc_path else "",
        "inputFiles": bundle.files,
    }
    # Execution date must not invalidate otherwise identical scientific results.
    configuration["selection"].pop("georeferenced_date", None)
    for stage in ("research", "transcription"):
        command = configuration[stage].pop("command", "")
        configuration[stage]["commandHash"] = fingerprint(command)
    # Added/edited records invalidate only their own checkpoint, not every completed specimen.
    journal_configuration = {key: value for key, value in configuration.items() if key not in {"inputDwcSha256", "inputFiles", "ribbonRenderer"}}
    journal = RecordJournal(cache_directory / "runs", journal_configuration, enabled=resume and not refresh_sources)
    report.configuration = configuration
    report.checkpoint_path = str(journal.path) if journal.enabled else "disabled"
    auditor = SourceAuditor(cache_directory / "sources", enabled=verify_source_urls, refresh=refresh_sources)

    def failed_outcome(index, row, exc):
        error = {"stage": "record_processing", "retryable": True, "error": str(exc)}
        candidate = CoordinateCandidate(
            catalog_number=catalog_number(row), occurrence_id=row.get("occurrenceID", ""),
            candidate_source="processing_failed", curation_status="failed", decision="unresolved",
            verification_status="failed_processing", remarks=str(exc),
            original_latitude=row.get("decimalLatitude", ""), original_longitude=row.get("decimalLongitude", ""),
            original_datum=row.get("geodeticDatum", ""),
        )
        result = CuratedResult(
            row=dict(row, georeferenceVerificationStatus="failed"), candidates=[candidate],
            decision="unresolved", verification_status="failed_processing", curation_status="failed",
            include_in_dwc=False, stage_errors=[error], original_coordinates=dict(row),
            provenance={"configurationHash": journal.configuration_hash, "inputRecordHash": fingerprint(row)},
        )
        return RowOutcome(index=index, result=result, candidates=[candidate], errors=[f"{catalog_number(row)}: {exc}"], stage_errors=[error])

    def run_record(index, row):
        images = label_image_paths(source_dir, row, sidecars) if not row.get("_vasculumMissMarkers") else []
        image_digests = [{"path": image, "sha256": file_hash((source_dir / image).resolve())} for image in images]
        image_digest = fingerprint(image_digests)
        key = fingerprint({"row": row, "images": image_digests})
        saved = journal.get(key)
        if saved:
            result_data = dict(saved["result"])
            restored_candidates = [CoordinateCandidate(**item) for item in result_data.pop("candidates")]
            result_data.pop("selected_candidate", None)
            result_data["candidates"] = restored_candidates
            result_data["selected_candidate"] = next((item for item in restored_candidates if item.selected == "true"), None)
            outcome = RowOutcome(**{**saved, "index": index, "result": CuratedResult(**result_data), "candidates": restored_candidates})
            outcome.checkpoint_reused = True
            for name in ("llm_attempted", "llm_transcription_requests", "llm_georeference_requests", "llm_rate_limit_retries", "llm_rate_limit_waits"):
                setattr(outcome, name, 0)
            outcome.llm_usage = []
            outcome.result.provenance = dict(outcome.result.provenance, checkpointReused=True, checkpointKey=key, currentInputFiles=bundle.files)
            return outcome
        try:
            outcome = process_row(
                index=index, total_rows=len(rows), row=row, source_dir=source_dir,
                sidecars=sidecars, gazetteer=gazetteer, llm_client=llm_client,
                llm_settings=llm_settings, transcription_llm_client=transcription_llm_client,
                transcription_llm_settings=transcription_llm_settings, llm_cache=response_cache,
                llm_rate_limit_retries=llm_rate_limit_retries, llm_backoff=llm_backoff,
                refinement_settings=refinement_settings, verification_timeout_seconds=verification_timeout_seconds,
                habitat_preference=habitat_preference, normalized_habitat=normalized_habitat,
                options=options,
                use_trails=use_trails, use_hydrology=use_hydrology,
                use_dem=use_dem, use_vegetation_prior=use_vegetation_prior, progress=progress,
                source_auditor=auditor,
            )
        except Exception as exc:
            outcome = failed_outcome(index, row, exc)
        outcome.result.provenance.update(
            configurationHash=journal.configuration_hash, implementation=configuration["implementation"],
            inputDwcSha256=configuration["inputDwcSha256"], inputRecordHash=fingerprint(row),
            imageSha256=image_digest,
            images=image_digests, inputType=row.get("vasculumInputType", ""),
            inputSource=row.get("vasculumInputSource", ""), currentInputFiles=bundle.files,
            recordKey=key, completedAt=datetime.now().astimezone().isoformat(),
        )
        retryable = bool(outcome.stage_errors or outcome.geospatial_refinement_warnings or outcome.habitat_review_unavailable)
        source_unavailable = any(
            check.get("status") == "unavailable"
            for item in outcome.candidates for check in json.loads(item.source_checks or "[]")
        )
        if not retryable and not source_unavailable:
            journal.put(key, asdict(outcome))
        return outcome

    def safe_run_record(index, row):
        try:
            return run_record(index, row)
        except Exception as exc:
            return failed_outcome(index, row, exc)

    if worker_count <= 1:
        for index, row in enumerate(rows, start=1):
            outcome = safe_run_record(index, row)
            outcomes[index] = outcome
            aggregate_outcome(report, outcome)
            progress.complete_record(
                index,
                len(rows),
                catalog_number(row),
                outcome.result.decision,
                outcome.result.verification_status,
            )
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(safe_run_record, index, row): index
                for index, row in enumerate(rows, start=1)
            }
            completed = 0
            pending = set(futures)
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    index = futures[future]
                    outcome = future.result()
                    outcomes[index] = outcome
                    aggregate_outcome(report, outcome)
                    completed += 1
                    progress.complete_record(
                        completed,
                        len(rows),
                        catalog_number(rows[index - 1]),
                        outcome.result.decision,
                        outcome.result.verification_status,
                    )

    for index in range(1, len(rows) + 1):
        outcome = outcomes[index]
        result = outcome.result
        if result.include_in_dwc:
            curated_rows.append(result.row)
        all_candidates.extend(outcome.candidates)
        results.append(result)

    destination.mkdir(parents=True, exist_ok=True)
    report.images_exported = export_final_images(destination, source_dir, results, sidecars, cache_directory, enabled=export_images)
    fieldnames = merged_fieldnames(input_fieldnames)
    write_dwc_exports(destination, curated_rows, fieldnames)
    write_candidates(destination, all_candidates)
    write_json_log(destination / "georeference.log.jsonl", results)
    report.records_written = len(curated_rows)
    report.llm_provider_attempts = llm_backoff.calls
    report.candidate_rows = len(all_candidates)
    try:
        report.ribbon_counts = ribbon_counts(rows, results, source_dir, dwc_path,
            complete_input=not limit and not additional_inputs and not bundle.personal_dwc_records and not bundle.photo_only_records)
        with progress.activity("Ribbon plot", stage="Ribbon plot", model="R/ggalluvial"):
            report.ribbon_path = str(write_ribbon_plot(project_dir, destination, report.ribbon_counts, ribbon_renderer))
    except Exception as exc:
        old_plot = destination / "georeference_flow.pdf"
        if old_plot.is_file():
            old_plot.unlink()
        report.errors.append(f"Ribbon plot: {exc}. DwC, candidate TSV and log were preserved.")
    report.finished_at = datetime.now().astimezone()
    write_summary(destination / "summary.txt", report)
    progress.update(f"Complete - {destination}")
    if hasattr(progress, "finish_run"):
        progress.finish_run()
    return report
