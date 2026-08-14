from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import GENERATED_WITH
from .models import ChecklistReport, EvidenceRecord, SpeciesEntry
from specimen_collector.http_client import PoliteHttpClient

from .pdf import write_pdf
from .taxonomy import checklist_page_numbers, families_in_species_order


def write_csv(path: Path, rows: list[dict[str, str]], delimiter: str = ",") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def evidence_row(record: EvidenceRecord) -> dict[str, str]:
    return {
        "source": record.source,
        "sourceRecordId": record.source_record_id,
        "occurrenceID": record.occurrence_id,
        "datasetKey": record.dataset_key,
        "institutionCode": record.institution_code,
        "catalogNumber": record.catalog_number,
        "family": record.family,
        "genus": record.genus,
        "canonicalSpecies": record.canonical_species,
        "scientificName": record.full_scientific_name,
        "authorship": record.authorship,
        "taxonRank": record.taxon_rank,
        "taxonKey": record.accepted_taxon_key or record.taxon_key,
        "basisOfRecord": record.basis_of_record,
        "country": record.country,
        "rawCountry": record.raw_country,
        "stateProvince": record.state_province,
        "county": record.county,
        "municipality": record.municipality,
        "locality": record.locality,
        "decimalLatitude": record.decimal_latitude,
        "decimalLongitude": record.decimal_longitude,
        "imageUrl": record.image_url,
        "originalImageUrl": record.image_original_url,
        "license": record.image_license,
        "rightsHolder": record.rights_holder,
        "references": record.source_record_url,
        "accessedAt": record.accessed_at,
    }


def visual_checklist_page_numbers(species: list[SpeciesEntry], first_page: int = 4) -> dict[str, int]:
    return checklist_page_numbers(species, first_page=first_page, per_page=4)


def write_species_exports(output_dir: Path, species: list[SpeciesEntry]) -> None:
    page_numbers = visual_checklist_page_numbers(species)
    rows = [entry.as_row(page_numbers.get(entry.canonical_species)) for entry in species]
    write_csv(output_dir / "species_list.csv", rows, ",")
    write_csv(output_dir / "species_list.tsv", rows, "\t")


def write_records_export(output_dir: Path, records: list[EvidenceRecord]) -> None:
    write_csv(output_dir / "records.csv", [evidence_row(record) for record in records], ",")


def write_markdown(output_dir: Path, report: ChecklistReport) -> None:
    page_numbers = visual_checklist_page_numbers(report.species)
    lines = [
        f"# {report.title}",
        "",
        GENERATED_WITH,
        "",
        f"- Publication date: {(report.finished_at or datetime.now().astimezone()).date().isoformat()}",
        f"- Geographic search area: {report.search_area.label()}",
        f"- Taxonomic scope: {report.taxon_filter.label()}",
        f"- Total accepted species: {len(report.species)}",
        "",
        "## Species List",
        "",
        "| Family | Scientific name | Specimens | Images | Page |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for entry in report.species:
        page = page_numbers.get(entry.canonical_species, "")
        name = entry.full_scientific_name or entry.canonical_species
        lines.append(
            f"| {entry.family} | {name} | {entry.specimen_count} | {entry.image_record_count} | {page} |"
        )
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            f"- Exact command: `{report.raw_command}`",
            f"- Software: {GENERATED_WITH}",
            f"- VASCULUM version: {report.version}",
            f"- CheckList version: {report.checklist_version}",
        ]
    )
    (output_dir / "species_list.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def summary_lines(report: ChecklistReport) -> list[str]:
    finished_at = report.finished_at or datetime.now().astimezone()
    elapsed_seconds = max(0, int((finished_at - report.started_at).total_seconds()))
    family_counts = Counter(entry.family or "(family unknown)" for entry in report.species)
    families = families_in_species_order(report.species)
    image_candidates = sum(1 for entry in report.species if entry.candidate_image_url)
    cvh_candidates = sum(1 for entry in report.species if entry.cvh_candidate_image_url)
    lines = [
        f"VASCULUM CheckList {report.checklist_version}",
        GENERATED_WITH,
        "",
        f"Run started: {report.started_at.isoformat(timespec='seconds')}",
        f"Run finished: {finished_at.isoformat(timespec='seconds')}",
        f"Elapsed seconds: {elapsed_seconds}",
        f"Title: {report.title}",
        f"Geographic search area: {report.search_area.label()}",
        f"Taxonomic scope: {report.taxon_filter.label()}",
        f"Exact command: {report.raw_command}",
        "",
        f"GBIF evidence records retained: {len(report.evidence_records)}",
        f"Accepted species count: {len(report.species)}",
        f"Species with image candidates: {image_candidates}",
        f"Species with CVH image candidates: {cvh_candidates}",
        "",
        "Family counts:",
    ]
    for family in families:
        count = family_counts[family]
        lines.append(f"- {family}: {count}")
    lines.extend(["", "Source results:", "SOURCE\tSTATUS\tRECORDS\tSPECIES\tMESSAGE"])
    for source_report in report.sources.values():
        message = source_report.message.replace("\n", " ").replace("\t", " ")
        lines.append(
            "\t".join(
                (
                    source_report.source,
                    source_report.status,
                    str(source_report.records),
                    str(source_report.species),
                    message,
                )
            )
        )
    lines.extend(["", f"Warnings: {len(report.warnings)}"])
    lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(["", f"Errors: {len(report.errors)}"])
    lines.extend(f"- {error}" for error in report.errors)
    return lines


def write_summary(path: Path, report: ChecklistReport) -> None:
    path.write_text("\n".join(summary_lines(report)) + "\n", encoding="utf-8")


def write_outputs(report: ChecklistReport, client: PoliteHttpClient | None = None) -> None:
    report.output_dir.mkdir(parents=True, exist_ok=True)
    write_species_exports(report.output_dir, report.species)
    write_records_export(report.output_dir, report.evidence_records)
    write_markdown(report.output_dir, report)
    write_summary(report.output_dir / "summary.txt", report)
    write_pdf(report, client)
