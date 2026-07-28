from __future__ import annotations

import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .geocoding import (
    extract_decimal_coordinates,
    is_precise_label_coordinate,
    match_gazetteer,
    read_gazetteer,
)
from .geospatial_refinement import (
    GeospatialRefinementCache,
    GeospatialRefinementSettings,
    refine_llm_candidates,
)
from .habitat import parse_habitats
from .image_quality import inspect_image
from .inputs import catalog_number, detect_dwc_path, find_image_path, read_label_sidecar, read_table
from .labels import read_label
from .llm import (
    DEFAULT_LLM_PROVIDER,
    DEFAULT_MODEL_AUTO,
    DEFAULT_REASONING_EFFORT,
    LlmSettings,
    apply_llm_label_fields,
    build_transcription_prompt,
    build_user_prompt,
    configured_model_request,
    llm_response_to_candidates,
    make_client,
    normalize_provider,
    preflight_llm,
    selected_model_label,
    validate_llm_settings,
)
from .llm_cache import LlmResponseCache
from .locality_quality import insufficient_locality_reason
from .models import RunReport
from .outputs import merged_fieldnames, write_candidates, write_dwc_exports, write_json_log, write_summary
from .parallel import RateLimitBackoff, is_rate_limit_error, resolve_worker_count
from .progress import TerminalProgress
from .scoring import SelectionOptions, original_coordinate_status, select_result


def curator_version(project_dir: Path) -> str:
    version_file = project_dir / "VERSION.txt"
    return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "v0.1.8"


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
    habitat_conflicts_rejected: int = 0


def protocol_text(
    version: str,
    curation_mode: str,
    prompt_profile: str,
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
        priors.append("vegetation")
    prior_text = ",".join(priors) if priors else "none"
    habitat = f"; taxon_habitat={taxon_habitat}" if taxon_habitat else ""
    return (
        f"VASCULUM llm_georeference_curator {version}; "
        f"curation_mode={curation_mode}; "
        f"prompt_profile={prompt_profile}; web_search={web_search_mode}; "
        f"evidence_priors={prior_text}{habitat}"
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
    cache_key = cache.key(
        purpose=purpose,
        provider=settings.provider,
        model=selected_model_label(settings),
        reasoning_effort=settings.reasoning_effort,
        web_search_mode=settings.web_search_mode,
        prompt=user_prompt,
        image_paths=image_paths,
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 0, 0, True
    attempts = 0
    while True:
        backoff.wait()
        try:
            response = llm_client.create_json(user_prompt, image_paths=image_paths)
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


def coordinate_evidence_text(row, label) -> str:
    return "\n".join(
        str(value or "")
        for value in (
            label.label_transcription,
            label.locality_text,
            label.elevation_text,
            row.get("verbatimCoordinates"),
            row.get("verbatimLatitude"),
            row.get("verbatimLongitude"),
        )
        if str(value or "").strip()
    )


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
    original_precision_decimals: int,
    exclude_insufficient_locality: bool,
    options: SelectionOptions,
    prompt_profile: str,
    use_trails: bool,
    use_hydrology: bool,
    use_dem: bool,
    use_vegetation_prior: bool,
    progress: TerminalProgress,
):
    catalog = catalog_number(row)
    outcome = RowOutcome(index=index, result=None)
    image_path = find_image_path(source_dir, row)
    label = read_label(row, catalog, image_path, sidecars)
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

    raw_coordinates = extract_decimal_coordinates(coordinate_evidence_text(row, label))
    gazetteer_matches = match_gazetteer(row, "\n".join([label.label_transcription, label.locality_text]), gazetteer)
    insufficient_reason = insufficient_locality_reason(row, label)
    llm_candidates = []
    original_status = original_coordinate_status(row, original_precision_decimals)

    has_untranscribed_image = bool(image_quality.path and label.label_status in {"image_not_transcribed", "dwc_text_only"})
    if original_status == "precise" and (llm_client or transcription_llm_client):
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
            image_paths = [image_quality.path]
            with progress.activity(transcription_message):
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
            if cache_hit:
                outcome.llm_attempted -= 1
                outcome.llm_transcription_requests -= 1
            apply_llm_label_fields(label, response)
            raw_coordinates = extract_decimal_coordinates(coordinate_evidence_text(row, label))
            gazetteer_matches = match_gazetteer(
                row,
                "\n".join([label.label_transcription, label.locality_text]),
                gazetteer,
            )
            insufficient_reason = insufficient_locality_reason(row, label)
        except Exception as exc:
            outcome.llm_errors += 1
            outcome.errors.append(f"{catalog}: label transcription failed: {exc}")

    precise_label_coordinates = [
        coordinate for coordinate in raw_coordinates if is_precise_label_coordinate(coordinate)
    ]
    has_searchable_text = any(
        str(value or "").strip()
        for value in (
            row.get("locality"),
            row.get("verbatimLocality"),
            row.get("municipality"),
            row.get("county"),
            label.locality_text,
            label.label_transcription,
        )
    )
    should_call_georeference_llm = bool(
        llm_client
        and original_status != "precise"
        and not precise_label_coordinates
        and has_searchable_text
        and not insufficient_reason
    )
    if (
        llm_client
        and original_status != "precise"
        and precise_label_coordinates
    ):
        outcome.llm_skipped_precise_label += 1
    elif (
        llm_client
        and original_status != "precise"
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
            with progress.activity(llm_progress_message):
                response, rate_retries, rate_waits, cache_hit = create_llm_json_with_backoff(
                    llm_client,
                    build_user_prompt(
                        row,
                        label,
                        normalized_habitat,
                        prompt_profile=prompt_profile,
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
            if cache_hit:
                outcome.llm_attempted -= 1
                outcome.llm_georeference_requests -= 1
            apply_llm_label_fields(label, response)
            raw_coordinates = extract_decimal_coordinates(coordinate_evidence_text(row, label))
            gazetteer_matches = match_gazetteer(row, "\n".join([label.label_transcription, label.locality_text]), gazetteer)
            insufficient_reason = insufficient_locality_reason(row, label)
            effective_model = str(response.get("_vasculum_model") or selected_model_label(llm_settings))
            llm_candidates = llm_response_to_candidates(response, label, effective_model)
            outcome.llm_candidate_rows += len(llm_candidates)
        except Exception as exc:
            outcome.llm_errors += 1
            outcome.errors.append(f"{catalog}: LLM georeferencing failed: {exc}")

    refinement_cache = GeospatialRefinementCache()
    row_refinement_settings = replace(
        refinement_settings,
        timeout_seconds=verification_timeout_seconds,
        deadline_monotonic=time.monotonic() + verification_timeout_seconds,
    )
    for _refinement_index in range(
        len(llm_candidates) if row_refinement_settings.enabled else 0
    ):
        refinement_message = (
            f"Coordinate verification {index}/{total_rows} {catalog}"
        )
        with progress.activity(refinement_message):
            refinement = refine_llm_candidates(
                llm_candidates,
                label,
                row_refinement_settings,
                habitat_preference=habitat_preference,
                cache=refinement_cache,
            )
        if not refinement.attempted:
            break
        outcome.geospatial_refinement_attempted += 1
        if habitat_preference.canonical:
            outcome.habitat_checks_attempted += 1
        if refinement.warning:
            outcome.geospatial_refinement_warnings.append(f"{catalog}: {refinement.warning}")
        if refinement.candidate:
            llm_candidates.append(refinement.candidate)
            outcome.geospatial_refinement_succeeded += 1
            break
        if refinement.rejected_anchor:
            outcome.habitat_conflicts_rejected += 1
        if not refinement.rejected_anchor:
            break

    result = select_result(
        row=row,
        label=label,
        raw_label_coordinates=raw_coordinates,
        llm_candidates=llm_candidates,
        gazetteer_matches=gazetteer_matches,
        insufficient_locality=insufficient_reason,
        exclude_insufficient_locality=exclude_insufficient_locality,
        options=options,
    )
    outcome.result = result
    outcome.candidates = result.candidates
    return outcome


def aggregate_outcome(report: RunReport, outcome: RowOutcome):
    result = outcome.result
    report.image_records += outcome.image_records
    report.image_review_records += outcome.image_review_records
    report.image_missing_records += outcome.image_missing_records
    report.llm_attempted += outcome.llm_attempted
    report.llm_candidate_rows += outcome.llm_candidate_rows
    report.llm_errors += outcome.llm_errors
    report.llm_rate_limit_retries += outcome.llm_rate_limit_retries
    report.llm_rate_limit_waits += outcome.llm_rate_limit_waits
    report.llm_cache_hits += outcome.llm_cache_hits
    report.llm_transcription_requests += outcome.llm_transcription_requests
    report.llm_georeference_requests += outcome.llm_georeference_requests
    report.llm_skipped_precise_original += outcome.llm_skipped_precise_original
    report.llm_skipped_precise_label += outcome.llm_skipped_precise_label
    report.llm_skipped_insufficient_locality += outcome.llm_skipped_insufficient_locality
    report.geospatial_refinement_attempted += outcome.geospatial_refinement_attempted
    report.geospatial_refinement_succeeded += outcome.geospatial_refinement_succeeded
    report.habitat_checks_attempted += outcome.habitat_checks_attempted
    report.habitat_conflicts_rejected += outcome.habitat_conflicts_rejected
    report.geospatial_refinement_warnings.extend(outcome.geospatial_refinement_warnings)
    report.errors.extend(outcome.errors)

    if not result.include_in_dwc:
        report.excluded += 1
        if result.decision == "exclude_insufficient_locality":
            report.excluded_insufficient_locality += 1
    if result.decision == "keep_original":
        report.kept_original += 1
    elif result.decision == "correct_existing":
        report.corrected_existing += 1
    elif result.decision == "infer_missing":
        report.inferred_missing += 1
    elif result.decision not in {"exclude_insufficient_locality"}:
        report.unresolved += 1
    if "review" in result.verification_status:
        report.review_required += 1


def run_pipeline(
    project_dir: Path,
    input_dir: Path,
    input_dwc,
    output_dir,
    label_tsv,
    gazetteer_tsv,
    dry_run: bool,
    curation_mode: str,
    original_precision_decimals: int,
    review_distance_km: float,
    exclude_insufficient_locality: bool,
    llm_mode: str,
    llm_provider: str,
    llm_model: str,
    llm_reasoning_effort: str,
    llm_web_search: str,
    llm_command: str,
    llm_api_key_env: str,
    llm_timeout_seconds: int,
    llm_rate_limit_retries: int,
    confirm_llm: bool,
    georeferenced_by: str,
    prompt_profile: str,
    use_trails: bool,
    use_hydrology: bool,
    use_dem: bool,
    use_vegetation_prior: bool,
    taxon_habitat: str,
    debug_log: bool,
    geospatial_refinement: bool = True,
    limit: int = 0,
    workers: str = "auto",
    llm_cache_enabled: bool = True,
    llm_cache_dir=None,
    label_timeout_seconds=None,
    georeference_timeout_seconds=None,
    verification_timeout_seconds=None,
    progress=None,
):
    load_env_file(project_dir / ".env")
    if curation_mode not in {"standard", "robust"}:
        raise ValueError("curation_mode must be one of: standard, robust")
    progress = progress or TerminalProgress()
    habitat_preference = parse_habitats(taxon_habitat)
    normalized_habitat = habitat_preference.display
    source_dir = input_dir.expanduser().resolve()
    if not source_dir.exists():
        raise FileNotFoundError(f"Input directory was not found: {source_dir}")
    dwc_path = detect_dwc_path(source_dir, input_dwc)
    destination = output_dir.expanduser().resolve() if output_dir else default_output_dir(project_dir, source_dir).resolve()
    version = curator_version(project_dir)
    started_at = datetime.now().astimezone()
    report = RunReport(version=version, input_dir=source_dir, input_dwc=dwc_path, output_dir=destination, started_at=started_at)
    report.curation_mode = curation_mode

    rows, input_fieldnames = read_table(dwc_path)
    if limit and limit > 0:
        rows = rows[:limit]
    sidecars = read_label_sidecar(label_tsv.expanduser().resolve() if label_tsv else None)
    gazetteer = read_gazetteer(gazetteer_tsv.expanduser().resolve() if gazetteer_tsv else None)
    label_timeout_seconds = label_timeout_seconds or llm_timeout_seconds
    georeference_timeout_seconds = georeference_timeout_seconds or llm_timeout_seconds
    verification_timeout_seconds = verification_timeout_seconds or llm_timeout_seconds

    provider = normalize_provider(llm_provider or DEFAULT_LLM_PROVIDER)
    llm_settings = LlmSettings(
        mode=llm_mode,
        provider=provider,
        model=configured_model_request(provider, llm_model or DEFAULT_MODEL_AUTO),
        reasoning_effort=llm_reasoning_effort or os.environ.get("OPENAI_REASONING_EFFORT") or os.environ.get("LLM_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT,
        api_key_env=llm_api_key_env,
        command=llm_command or os.environ.get("LLM_COMMAND", ""),
        timeout_seconds=georeference_timeout_seconds,
        web_search_mode=llm_web_search,
    )
    validate_llm_settings(llm_settings)
    llm_client = make_client(llm_settings)
    transcription_llm_settings = replace(
        llm_settings,
        reasoning_effort="medium",
        web_search_mode="disabled",
        timeout_seconds=label_timeout_seconds,
    )
    transcription_llm_client = make_client(transcription_llm_settings)
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
        enabled=bool(geospatial_refinement and use_dem),
        use_routes=use_trails,
        timeout_seconds=verification_timeout_seconds,
    )

    report.records_read = len(rows)
    report.label_sidecar_rows = len(sidecars)
    report.gazetteer_rows = len(gazetteer)
    report.llm_provider = llm_settings.provider
    report.llm_model = selected_model_label(llm_settings)
    report.llm_reasoning_effort = llm_settings.reasoning_effort
    report.llm_web_search = llm_settings.web_search_mode
    report.label_timeout_seconds = label_timeout_seconds
    report.georeference_timeout_seconds = georeference_timeout_seconds
    report.verification_timeout_seconds = verification_timeout_seconds
    report.habitat_prior = normalized_habitat
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
    if dry_run:
        report.finished_at = datetime.now().astimezone()
        return report
    if llm_client:
        preflight_llm(
            llm_settings,
            confirm=confirm_llm,
            external_geospatial_services=refinement_settings.enabled,
        )

    protocol = protocol_text(
        version,
        curation_mode,
        prompt_profile,
        llm_settings.web_search_mode,
        use_trails,
        use_hydrology,
        use_dem,
        use_vegetation_prior,
        normalized_habitat,
    )
    sources = "collector DwC | specimen label transcription"
    if llm_client:
        sources += f" | {llm_settings.provider}:{selected_model_label(llm_settings)}"
        if llm_settings.web_search_mode != "disabled":
            sources += f" | {llm_settings.web_search_mode} web search"
    if gazetteer:
        sources += " | local gazetteer"
    if use_trails:
        sources += " | trail prior"
    if use_hydrology:
        sources += " | hydrology prior"
    if use_dem:
        sources += " | DEM"
    if use_vegetation_prior:
        sources += " | vegetation prior"
    if refinement_settings.enabled:
        sources += " | OpenStreetMap contributors (ODbL) environmental context"
        if refinement_settings.use_routes:
            sources += " and route geometry"
        sources += " | Open-Meteo / Copernicus DEM GLO-90"

    options = SelectionOptions(
        original_precision_decimals=original_precision_decimals,
        review_distance_km=review_distance_km,
        curation_mode=curation_mode,
        georeferenced_by=georeferenced_by,
        georeferenced_date=started_at.date().isoformat(),
        protocol=protocol,
        sources=sources,
        habitat_prior=normalized_habitat,
    )

    progress.update(f"Reading {len(rows)} DwC record(s) from {dwc_path}")
    if normalized_habitat:
        progress.update(f"Habitat prior: {normalized_habitat}")
    progress.update(f"Workers: {worker_count}")
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
    llm_backoff = RateLimitBackoff(retries=llm_rate_limit_retries)

    if worker_count <= 1:
        for index, row in enumerate(rows, start=1):
            outcome = process_row(
                index=index,
                total_rows=len(rows),
                row=row,
                source_dir=source_dir,
                sidecars=sidecars,
                gazetteer=gazetteer,
                llm_client=llm_client,
                llm_settings=llm_settings,
                transcription_llm_client=transcription_llm_client,
                transcription_llm_settings=transcription_llm_settings,
                llm_cache=response_cache,
                llm_rate_limit_retries=llm_rate_limit_retries,
                llm_backoff=llm_backoff,
                refinement_settings=refinement_settings,
                verification_timeout_seconds=verification_timeout_seconds,
                habitat_preference=habitat_preference,
                normalized_habitat=normalized_habitat,
                original_precision_decimals=original_precision_decimals,
                exclude_insufficient_locality=exclude_insufficient_locality,
                options=options,
                prompt_profile=prompt_profile,
                use_trails=use_trails,
                use_hydrology=use_hydrology,
                use_dem=use_dem,
                use_vegetation_prior=use_vegetation_prior,
                progress=progress,
            )
            outcomes[index] = outcome
            aggregate_outcome(report, outcome)
            if index % 25 == 0:
                progress.update(f"Processed {index}/{len(rows)} record(s)")
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    process_row,
                    index=index,
                    total_rows=len(rows),
                    row=row,
                    source_dir=source_dir,
                    sidecars=sidecars,
                    gazetteer=gazetteer,
                    llm_client=llm_client,
                    llm_settings=llm_settings,
                    transcription_llm_client=transcription_llm_client,
                    transcription_llm_settings=transcription_llm_settings,
                    llm_cache=response_cache,
                    llm_rate_limit_retries=llm_rate_limit_retries,
                    llm_backoff=llm_backoff,
                    refinement_settings=refinement_settings,
                    verification_timeout_seconds=verification_timeout_seconds,
                    habitat_preference=habitat_preference,
                    normalized_habitat=normalized_habitat,
                    original_precision_decimals=original_precision_decimals,
                    exclude_insufficient_locality=exclude_insufficient_locality,
                    options=options,
                    prompt_profile=prompt_profile,
                    use_trails=use_trails,
                    use_hydrology=use_hydrology,
                    use_dem=use_dem,
                    use_vegetation_prior=use_vegetation_prior,
                    progress=progress,
                ): index
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
                    if completed % 5 == 0 or completed == len(rows):
                        progress.update(f"Processed {completed}/{len(rows)} record(s)")

    for index in range(1, len(rows) + 1):
        outcome = outcomes[index]
        result = outcome.result
        if result.include_in_dwc:
            curated_rows.append(result.row)
        all_candidates.extend(outcome.candidates)
        results.append(result)

    destination.mkdir(parents=True, exist_ok=True)
    fieldnames = merged_fieldnames(input_fieldnames)
    write_dwc_exports(destination, curated_rows, fieldnames)
    write_candidates(destination, all_candidates)
    if debug_log:
        write_json_log(destination / "georeference.log.jsonl", results)
    report.records_written = len(curated_rows)
    report.candidate_rows = len(all_candidates)
    report.finished_at = datetime.now().astimezone()
    write_summary(destination / "summary.txt", report)
    progress.update(f"Complete - {destination}")
    return report
