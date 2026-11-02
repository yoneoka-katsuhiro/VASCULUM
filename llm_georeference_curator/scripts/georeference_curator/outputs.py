from __future__ import annotations

import csv
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime

from .models import CoordinateCandidate


STANDARD_GEOREFERENCE_FIELDS = [
    "decimalLatitude",
    "decimalLongitude",
    "coordinateUncertaintyInMeters",
    "geodeticDatum",
    "minimumElevationInMeters",
    "maximumElevationInMeters",
    "georeferencedBy",
    "georeferencedDate",
    "georeferenceProtocol",
    "georeferenceSources",
    "georeferenceRemarks",
    "georeferenceVerificationStatus",
    "curationStatus",
    "confidence",
]


@contextmanager
def atomic_text(path, *, newline=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline=newline) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def merged_fieldnames(input_fieldnames):
    fieldnames = list(input_fieldnames)
    for name in STANDARD_GEOREFERENCE_FIELDS:
        if name not in fieldnames:
            fieldnames.append(name)
    return fieldnames


def write_table(path, rows, fieldnames, delimiter):
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_dwc_exports(output_dir, rows, fieldnames):
    write_table(output_dir / "modified_dwc.csv", rows, fieldnames, ",")
    write_table(output_dir / "modified_dwc.tsv", rows, fieldnames, "\t")


def write_candidates(output_dir, candidates):
    write_table(
        output_dir / "georeference_candidates.tsv",
        [candidate.as_row() for candidate in candidates],
        CoordinateCandidate.fieldnames(),
        "\t",
    )


def write_summary(path, report):
    finished_at = report.finished_at or datetime.now().astimezone()
    elapsed_seconds = max(0, int((finished_at - report.started_at).total_seconds()))
    lines = [
        f"VASCULUM llm_georeference_curator {report.version}",
        "",
        f"Run started: {report.started_at.isoformat(timespec='seconds')}",
        f"Run finished: {finished_at.isoformat(timespec='seconds')}",
        f"Elapsed seconds: {elapsed_seconds}",
        f"Input directory: {report.input_dir}",
        f"Input DwC: {report.input_dwc}",
        f"Additional personal DwC records loaded: {report.personal_dwc_records}",
        f"Photo-only personal specimens loaded: {report.photo_only_records}",
        f"Personal photographs attached to existing records: {report.attached_personal_images}",
        "Input counts above describe the loaded dataset before --limit is applied.",
        f"Output directory: {report.output_dir}",
        "Final DwC policy: accepted records only; all records retained in audit outputs",
        "Input policy: plausible existing coordinates, otherwise best point-radius from legible specimen-label locality",
        f"Maximum accepted horizontal uncertainty (m): {report.max_uncertainty_meters or 'not set'}",
        "Six decimal places specify output formatting, not empirical accuracy.",
        "No uniform 10 km accuracy or 10 m vertical accuracy is claimed.",
        f"Accepted records: {report.accepted}",
        "Accepted confidence counts: " + json.dumps(report.confidence_counts, sort_keys=True),
        "Verification status: unverified; final validation is performed by the user",
        f"Failed records (retryable, not insufficient locality): {report.failed}",
        f"Resumed completed records: {report.resumed_records}",
        f"Checkpoint journal: {report.checkpoint_path}",
        f"LLM provider: {report.llm_provider}",
        f"LLM model: {report.llm_model}",
        f"LLM reasoning effort: {report.llm_reasoning_effort}",
        f"LLM web search: {report.llm_web_search}",
        f"Label-reading model: {report.label_model}",
        f"Label-reading reasoning effort: {report.label_reasoning_effort}",
        f"Label-reading timeout seconds: {report.label_timeout_seconds}",
        f"Coordinate-research timeout seconds: {report.georeference_timeout_seconds}",
        f"Coordinate-verification timeout seconds: {report.verification_timeout_seconds}",
        f"Workers: {report.worker_count}",
        f"LLM rate-limit retries: {report.llm_rate_limit_retries}",
        f"LLM rate-limit waits: {report.llm_rate_limit_waits}",
        f"LLM cache hits: {report.llm_cache_hits}",
        f"LLM successful requests with reported token usage: {report.llm_usage_reported_requests}",
        f"LLM input tokens (includes provider-cached input): {report.llm_input_tokens}",
        f"LLM provider-cached input tokens: {report.llm_cached_input_tokens}",
        f"LLM output tokens: {report.llm_output_tokens}",
        "Token totals cover reported successful fresh calls; failed/unreported calls are not included.",
        f"LLM transcription requests: {report.llm_transcription_requests}",
        f"LLM georeference requests: {report.llm_georeference_requests}",
        f"LLM skipped - trusted original coordinate: {report.llm_skipped_precise_original}",
        f"LLM skipped - precise label coordinate: {report.llm_skipped_precise_label}",
        f"LLM skipped - insufficient locality: {report.llm_skipped_insufficient_locality}",
        f"Habitat review input: {report.habitat_prior or 'none'}",
        "Habitat review policy: warnings only; no coordinate, ranking or eligibility changes.",
        "",
        f"Records read: {report.records_read}",
        f"Records written: {report.records_written}",
        f"Ribbon PDF: {report.ribbon_path or 'not produced; see errors'}",
        "Ribbon counts: " + json.dumps(report.ribbon_counts, sort_keys=True),
        f"Final images exported (included records only, no miss_ images): {report.images_exported}",
        f"Excluded records: {report.excluded}",
        f"Candidate rows: {report.candidate_rows}",
        f"Label sidecar rows: {report.label_sidecar_rows}",
        f"Gazetteer rows: {report.gazetteer_rows}",
        f"Records with local images: {report.image_records}",
        f"Images requiring label-reading review: {report.image_review_records}",
        f"Image files missing: {report.image_missing_records}",
        f"LLM requests attempted: {report.llm_attempted}",
        f"Actual fresh provider attempts (including fallback/retry): {report.llm_provider_attempts}",
        f"Run-wide fresh provider attempt limit: {report.max_llm_calls or 'not set'}",
        f"LLM candidate rows: {report.llm_candidate_rows}",
        f"LLM errors: {report.llm_errors}",
        f"Environmental refinements/checks attempted: {report.geospatial_refinement_attempted}",
        f"Habitat checks attempted: {report.habitat_checks_attempted}",
        f"Records flagged for habitat review: {report.habitat_review_warnings}",
        f"Habitat review unknown: {report.habitat_review_unknown}",
        f"Habitat review unavailable: {report.habitat_review_unavailable}",
        f"Route/DEM refinements succeeded: {report.geospatial_refinement_succeeded}",
        f"Environmental refinement warnings: {len(report.geospatial_refinement_warnings)}",
        "",
        f"Original coordinates retained in final DwC: {report.kept_original}",
        f"Existing coordinates corrected in final DwC: {report.corrected_existing}",
        f"Missing coordinates inferred in final DwC: {report.inferred_missing}",
        f"Excluded for insufficient locality: {report.excluded_insufficient_locality}",
        f"Excluded by manual miss_ review: {report.excluded_manual_misidentification}",
        f"Excluded - no photo and no trusted coordinates: {report.excluded_missing_source_image}",
        "",
        f"Errors: {len(report.errors)}",
    ]
    lines.extend(f"- {error}" for error in report.errors)
    if report.input_warnings:
        lines.extend(("", "Input warnings:"))
        lines.extend(f"- {warning}" for warning in report.input_warnings)
    lines.extend(("", "Input file provenance:", json.dumps(report.input_files, ensure_ascii=False, sort_keys=True)))
    if report.geospatial_refinement_warnings:
        lines.extend(("", "Environmental refinement warnings:"))
        lines.extend(
            f"- {warning}" for warning in report.geospatial_refinement_warnings
        )
    lines.extend(("", "Reproducibility configuration (no API key values):", json.dumps(report.configuration, ensure_ascii=False, sort_keys=True, default=str)))
    with atomic_text(path) as handle:
        handle.write("\n".join(lines) + "\n")


def write_json_log(path, results):
    with atomic_text(path) as handle:
        for result in results:
            payload = {
                "catalogNumber": result.row.get("catalogNumber", ""),
                "occurrenceID": result.row.get("occurrenceID", ""),
                "decision": result.decision,
                "curationStatus": result.curation_status,
                "confidence": result.row.get("confidence", "low"),
                "originalCoordinates": result.original_coordinates,
                "modifiedCoordinates": {key: result.row.get(key, "") for key in ("decimalLatitude", "decimalLongitude", "geodeticDatum", "coordinateUncertaintyInMeters")},
                "provenance": result.provenance,
                "stageErrors": result.stage_errors,
                "verificationStatus": result.verification_status,
                "selectedCandidate": result.selected_candidate.as_row() if result.selected_candidate else None,
                "notes": result.notes,
                "candidateCount": len(result.candidates),
                "includeInDwc": result.include_in_dwc,
                "exclusionReason": result.exclusion_reason,
                "llmUsage": result.llm_usage,
                "habitatReview": result.habitat_review,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
