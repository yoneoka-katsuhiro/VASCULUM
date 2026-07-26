from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .models import SpecimenRecord
from .records import duplicate_gathering_count, specimen_code, unique_values


@dataclass
class SourceReport:
    source: str
    status: str = "pending"
    records: int = 0
    images: int = 0
    retries: int = 0
    message: str = ""


@dataclass
class RunReport:
    version: str
    accepted_name: str
    search_names: list[str]
    image_resolution: str
    output_dir: Path
    started_at: datetime
    log_path: Path | None = None
    finished_at: datetime | None = None
    records_found: int = 0
    records: list[SpecimenRecord] = field(default_factory=list)
    sources: dict[str, SourceReport] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def partial_failure(self) -> bool:
        return bool(self.errors)


def dwc_row(record: SpecimenRecord, accepted_name: str) -> dict[str, str]:
    code = specimen_code(record)
    original_catalog = record.catalog_number.strip()
    remote_media = [
        value
        for value in (record.download_url, record.image_url)
        if value.startswith(("http://", "https://"))
    ]
    media = unique_values([record.local_image_path, *remote_media])
    return {
        "occurrenceID": record.occurrence_id or record.source_record_url or record.source_record_id,
        "basisOfRecord": record.basis_of_record,
        "institutionCode": record.institution_code,
        "collectionCode": record.collection_code,
        "catalogNumber": code,
        "otherCatalogNumbers": original_catalog if original_catalog and original_catalog != code else "",
        "datasetName": record.merged_from_sources or record.source,
        "scientificName": record.scientific_name,
        "acceptedNameUsage": accepted_name,
        "recordedBy": record.recorded_by,
        "recordNumber": record.record_number,
        "eventID": record.collection_event_key,
        "eventDate": record.event_date,
        "country": record.country,
        "stateProvince": record.state_province,
        "locality": record.locality,
        "verbatimLocality": record.verbatim_locality,
        "decimalLatitude": record.decimal_latitude,
        "decimalLongitude": record.decimal_longitude,
        "verbatimElevation": record.elevation,
        "identifiedBy": record.identified_by,
        "typeStatus": record.type_status,
        "associatedMedia": " | ".join(media),
        "references": record.source_record_url,
        "license": record.image_license,
        "rightsHolder": record.rights_holder,
    }


def write_dwc(path: Path, records: list[SpecimenRecord], accepted_name: str, delimiter: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dwc_row(SpecimenRecord(), accepted_name).keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        for record in records:
            writer.writerow(dwc_row(record, accepted_name))


def write_dwc_exports(output_dir: Path, records: list[SpecimenRecord], accepted_name: str) -> None:
    write_dwc(output_dir / "dwc.csv", records, accepted_name, ",")
    write_dwc(output_dir / "dwc.tsv", records, accepted_name, "\t")


def write_summary(path: Path, report: RunReport) -> None:
    finished_at = report.finished_at or datetime.now().astimezone()
    elapsed_seconds = max(0, int((finished_at - report.started_at).total_seconds()))
    image_counts = Counter(record.download_status for record in report.records)
    coordinate_count = sum(
        1
        for record in report.records
        if record.decimal_latitude and record.decimal_longitude
    )
    image_link_count = sum(1 for record in report.records if record.image_url)
    type_count = sum(1 for record in report.records if record.type_status)
    merged_count = max(0, report.records_found - len(report.records))

    lines = [
        f"Herbarium Specimen Collector {report.version}",
        "",
        f"Run started: {report.started_at.isoformat(timespec='seconds')}",
        f"Run finished: {finished_at.isoformat(timespec='seconds')}",
        f"Elapsed seconds: {elapsed_seconds}",
        f"Accepted taxon: {report.accepted_name}",
        f"Search names: {' | '.join(report.search_names)}",
        f"Selected sources: {len(report.sources)}",
        f"Image resolution: {report.image_resolution}",
        (
            f"Log file: {report.log_path.relative_to(report.output_dir)}"
            if report.log_path
            else "Log file: none"
        ),
        "",
        f"Records found: {report.records_found}",
        f"Physical specimens after deduplication: {len(report.records)}",
        f"Duplicate portal records merged: {merged_count}",
        f"Duplicate gatherings grouped: {duplicate_gathering_count(report.records)}",
        f"Records with coordinates: {coordinate_count}",
        f"Records linked to images: {image_link_count}",
        f"Type specimens: {type_count}",
        f"Images downloaded: {image_counts.get('downloaded', 0)}",
        f"Images already present: {image_counts.get('already_downloaded', 0)}",
        f"Images failed: {image_counts.get('rejected_or_failed', 0)}",
        f"Images skipped: {image_counts.get('skipped_by_option', 0)}",
        "",
        "Source results:",
        "SOURCE\tSTATUS\tRECORDS\tIMAGES\tRETRIES\tMESSAGE",
    ]
    for source_report in report.sources.values():
        message = source_report.message.replace("\n", " ").replace("\t", " ")
        lines.append(
            "\t".join(
                (
                    source_report.source,
                    source_report.status,
                    str(source_report.records),
                    str(source_report.images),
                    str(source_report.retries),
                    message,
                )
            )
        )

    lines.extend(["", f"Errors: {len(report.errors)}"])
    lines.extend(f"- {error}" for error in report.errors)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
