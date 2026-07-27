from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from .geocoding import extract_decimal_coordinates, match_gazetteer, read_gazetteer
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
    build_user_prompt,
    configured_model_request,
    llm_response_to_candidates,
    make_client,
    normalize_provider,
    preflight_llm,
    selected_model_label,
    validate_llm_settings,
)
from .locality_quality import insufficient_locality_reason
from .models import RunReport
from .outputs import merged_fieldnames, write_candidates, write_dwc_exports, write_json_log, write_summary
from .progress import TerminalProgress
from .scoring import SelectionOptions, original_coordinate_status, select_result


def curator_version(project_dir: Path) -> str:
    version_file = project_dir / "VERSION.txt"
    return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "v0.1.6"


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

    provider = normalize_provider(llm_provider or DEFAULT_LLM_PROVIDER)
    llm_settings = LlmSettings(
        mode=llm_mode,
        provider=provider,
        model=configured_model_request(provider, llm_model or DEFAULT_MODEL_AUTO),
        reasoning_effort=llm_reasoning_effort or os.environ.get("OPENAI_REASONING_EFFORT") or os.environ.get("LLM_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT,
        api_key_env=llm_api_key_env,
        command=llm_command or os.environ.get("LLM_COMMAND", ""),
        timeout_seconds=llm_timeout_seconds,
        web_search_mode=llm_web_search,
    )
    validate_llm_settings(llm_settings)
    llm_client = make_client(llm_settings)
    refinement_settings = GeospatialRefinementSettings(
        enabled=bool(geospatial_refinement and use_dem),
        use_routes=use_trails,
    )
    refinement_cache = GeospatialRefinementCache()

    report.records_read = len(rows)
    report.label_sidecar_rows = len(sidecars)
    report.gazetteer_rows = len(gazetteer)
    report.llm_provider = llm_settings.provider
    report.llm_model = selected_model_label(llm_settings)
    report.llm_reasoning_effort = llm_settings.reasoning_effort
    report.llm_web_search = llm_settings.web_search_mode
    report.habitat_prior = normalized_habitat
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
    curated_rows = []
    all_candidates = []
    results = []

    for index, row in enumerate(rows, start=1):
        catalog = catalog_number(row)
        image_path = find_image_path(source_dir, row)
        label = read_label(row, catalog, image_path, sidecars)
        image_quality = inspect_image(source_dir, label.image_path)
        label.image_width = image_quality.width
        label.image_height = image_quality.height
        label.image_file_size_bytes = image_quality.file_size_bytes
        label.image_quality_status = image_quality.status
        label.image_quality_remarks = image_quality.remarks
        if label.image_path:
            report.image_records += 1
        if image_quality.status == "image_missing":
            report.image_missing_records += 1
        elif image_quality.status.startswith("review_") or image_quality.status.endswith("_unknown"):
            report.image_review_records += 1

        raw_coordinates = extract_decimal_coordinates("\n".join([label.label_transcription, label.locality_text, label.elevation_text]))
        gazetteer_matches = match_gazetteer(row, "\n".join([label.label_transcription, label.locality_text]), gazetteer)
        insufficient_reason = insufficient_locality_reason(row, label)
        llm_candidates = []
        original_status = original_coordinate_status(row, original_precision_decimals)

        has_untranscribed_image = bool(image_quality.path and label.label_status in {"image_not_transcribed", "dwc_text_only"})
        has_searchable_text = any(
            str(value or "").strip()
            for value in (
                row.get("locality"),
                row.get("verbatimLocality"),
                row.get("stateProvince"),
                label.locality_text,
                label.label_transcription,
            )
        )
        should_call_llm = bool(
            llm_client
            and original_status != "precise"
            and (has_untranscribed_image or has_searchable_text)
            and (not insufficient_reason or has_untranscribed_image)
        )
        if should_call_llm:
            llm_progress_message = (
                f"LLM {llm_settings.provider}:{selected_model_label(llm_settings)} "
                f"{index}/{len(rows)} {catalog}"
            )
            try:
                report.llm_attempted += 1
                image_paths = [image_quality.path] if image_quality.path and image_quality.path.exists() else []
                with progress.activity(llm_progress_message):
                    response = llm_client.create_json(
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
                        image_paths=image_paths,
                    )
                apply_llm_label_fields(label, response)
                raw_coordinates = extract_decimal_coordinates("\n".join([label.label_transcription, label.locality_text, label.elevation_text]))
                gazetteer_matches = match_gazetteer(row, "\n".join([label.label_transcription, label.locality_text]), gazetteer)
                insufficient_reason = insufficient_locality_reason(row, label)
                effective_model = str(response.get("_vasculum_model") or selected_model_label(llm_settings))
                llm_candidates = llm_response_to_candidates(response, label, effective_model)
                report.llm_candidate_rows += len(llm_candidates)
            except Exception as exc:
                report.llm_errors += 1
                report.errors.append(f"{catalog}: LLM georeferencing failed: {exc}")

        for _refinement_index in range(max(1, len(llm_candidates))):
            refinement_message = (
                f"Habitat/route/DEM {index}/{len(rows)} {catalog}"
            )
            with progress.activity(refinement_message):
                refinement = refine_llm_candidates(
                    llm_candidates,
                    label,
                    refinement_settings,
                    habitat_preference=habitat_preference,
                    cache=refinement_cache,
                )
            if not refinement.attempted:
                break
            report.geospatial_refinement_attempted += 1
            if habitat_preference.canonical:
                report.habitat_checks_attempted += 1
            if refinement.warning:
                report.geospatial_refinement_warnings.append(
                    f"{catalog}: {refinement.warning}"
                )
            if refinement.candidate:
                llm_candidates.append(refinement.candidate)
                report.geospatial_refinement_succeeded += 1
                break
            if refinement.rejected_anchor:
                report.habitat_conflicts_rejected += 1
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
        if result.include_in_dwc:
            curated_rows.append(result.row)
        else:
            report.excluded += 1
            if result.decision == "exclude_insufficient_locality":
                report.excluded_insufficient_locality += 1
        all_candidates.extend(result.candidates)
        results.append(result)

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
        if index % 25 == 0:
            progress.update(f"Processed {index}/{len(rows)} record(s)")

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
