from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime

from .models import CoordinateCandidate


STANDARD_GEOREFERENCE_FIELDS = [
    "coordinateUncertaintyInMeters",
    "geodeticDatum",
    "minimumElevationInMeters",
    "maximumElevationInMeters",
    "georeferencedBy",
    "georeferencedDate",
    "georeferenceProtocol",
    "georeferenceSources",
    "georeferenceRemarks",
]


def merged_fieldnames(input_fieldnames):
    fieldnames = list(input_fieldnames)
    for name in STANDARD_GEOREFERENCE_FIELDS:
        if name not in fieldnames:
            fieldnames.append(name)
    return fieldnames


def write_table(path, rows, fieldnames, delimiter):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
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
        f"Output directory: {report.output_dir}",
        f"Curation mode: {report.curation_mode}",
        f"LLM provider: {report.llm_provider}",
        f"LLM model: {report.llm_model}",
        f"LLM reasoning effort: {report.llm_reasoning_effort}",
        f"LLM web search: {report.llm_web_search}",
        f"Habitat prior: {report.habitat_prior or 'none'}",
        "",
        f"Records read: {report.records_read}",
        f"Records written: {report.records_written}",
        f"Records excluded from final DwC: {report.excluded}",
        f"Candidate rows: {report.candidate_rows}",
        f"Label sidecar rows: {report.label_sidecar_rows}",
        f"Gazetteer rows: {report.gazetteer_rows}",
        f"Records with local images: {report.image_records}",
        f"Images requiring label-reading review: {report.image_review_records}",
        f"Image files missing: {report.image_missing_records}",
        f"LLM requests attempted: {report.llm_attempted}",
        f"LLM candidate rows: {report.llm_candidate_rows}",
        f"LLM errors: {report.llm_errors}",
        f"Environmental refinements/checks attempted: {report.geospatial_refinement_attempted}",
        f"Habitat checks attempted: {report.habitat_checks_attempted}",
        f"Candidates rejected for habitat conflict: {report.habitat_conflicts_rejected}",
        f"Route/DEM refinements succeeded: {report.geospatial_refinement_succeeded}",
        f"Environmental refinement warnings: {len(report.geospatial_refinement_warnings)}",
        "",
        f"Original coordinates retained: {report.kept_original}",
        f"Existing coordinates corrected: {report.corrected_existing}",
        f"Missing coordinates inferred: {report.inferred_missing}",
        f"Unresolved records: {report.unresolved}",
        f"Records requiring review: {report.review_required}",
        f"Excluded for insufficient locality: {report.excluded_insufficient_locality}",
        "",
        f"Errors: {len(report.errors)}",
    ]
    lines.extend(f"- {error}" for error in report.errors)
    if report.geospatial_refinement_warnings:
        lines.extend(("", "Environmental refinement warnings:"))
        lines.extend(
            f"- {warning}" for warning in report.geospatial_refinement_warnings
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json_log(path, results):
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            payload = {
                "catalogNumber": result.row.get("catalogNumber", ""),
                "occurrenceID": result.row.get("occurrenceID", ""),
                "decision": result.decision,
                "verificationStatus": result.verification_status,
                "selectedCandidate": result.selected_candidate.as_row() if result.selected_candidate else None,
                "notes": result.notes,
                "candidateCount": len(result.candidates),
                "includeInDwc": result.include_in_dwc,
                "exclusionReason": result.exclusion_reason,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
