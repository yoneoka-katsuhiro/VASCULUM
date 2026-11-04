from __future__ import annotations

import csv
import hashlib
import json
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
    finished_at: datetime | None = None
    records_found: int = 0
    records: list[SpecimenRecord] = field(default_factory=list)
    sources: dict[str, SourceReport] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    images_downloaded: int = 0
    images_in_folder: int = 0

    @property
    def partial_failure(self) -> bool:
        return bool(self.errors)

    @property
    def records_written(self) -> int:
        return sum(record.download_status != "excluded_manual_misidentification" for record in self.records)

    @property
    def export_stem(self) -> str:
        return "dwc.partial" if self.partial_failure else "dwc"

    @property
    def summary_filename(self) -> str:
        return "summary.partial.txt" if self.partial_failure else "summary.txt"


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
        "datasetID": record.dataset_id,
        "datasetName": record.dataset_name,
        "vasculumPrimarySource": record.source,
        "vasculumSources": record.merged_from_sources or record.source,
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
        "vasculumCoordinateSource": record.coordinate_source or record.source_record_url or record.source,
        "geodeticDatum": record.geodetic_datum,
        "coordinateUncertaintyInMeters": record.coordinate_uncertainty_meters,
        "coordinatePrecision": record.coordinate_precision,
        "georeferenceProtocol": record.georeference_protocol,
        "georeferenceSources": record.georeference_sources,
        "georeferenceVerificationStatus": record.georeference_verification_status,
        "georeferenceRemarks": record.georeference_remarks,
        "georeferencedBy": record.georeferenced_by,
        "georeferencedDate": record.georeferenced_date,
        "geospatialIssues": record.geospatial_issues or ("source_reported_geospatial_issue" if "issue" in record.coordinate_status else ""),
        "verbatimElevation": record.elevation,
        "minimumElevationInMeters": record.minimum_elevation_meters,
        "maximumElevationInMeters": record.maximum_elevation_meters,
        "identifiedBy": record.identified_by,
        "typeStatus": record.type_status,
        "associatedMedia": " | ".join(media),
        "references": record.source_record_url,
        "license": record.data_license,
        "rightsHolder": record.data_rights_holder,
        "vasculumImageLicense": record.image_license,
        "vasculumImageRightsHolder": record.rights_holder,
    }


def write_dwc(path: Path, records: list[SpecimenRecord], accepted_name: str, delimiter: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dwc_row(SpecimenRecord(), accepted_name).keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        for record in records:
            if record.download_status == "excluded_manual_misidentification":
                continue
            writer.writerow(dwc_row(record, accepted_name))


def write_dwc_exports(output_dir: Path, records: list[SpecimenRecord], accepted_name: str, *, stem: str = "dwc") -> None:
    write_dwc(output_dir / f"{stem}.csv", records, accepted_name, ",")
    write_dwc(output_dir / f"{stem}.tsv", records, accepted_name, "\t")


def write_summary(path: Path, report: RunReport) -> None:
    finished_at = report.finished_at or datetime.now().astimezone()
    elapsed_seconds = max(0, int((finished_at - report.started_at).total_seconds()))
    image_counts = Counter(record.download_status for record in report.records)
    manually_excluded = [record for record in report.records if record.download_status == "excluded_manual_misidentification"]
    coordinate_count = sum(
        1
        for record in report.records
        if record.decimal_latitude and record.decimal_longitude
    )
    image_link_count = sum(1 for record in report.records if record.image_url)
    type_count = sum(1 for record in report.records if record.type_status)
    merged_count = max(0, report.records_found - len(report.records))
    unreferenced_images = max(0, report.images_in_folder - report.images_downloaded)

    lines = [
        f"VASCULUM {report.version}",
        "",
        f"Run started: {report.started_at.isoformat(timespec='seconds')}",
        f"Run finished: {finished_at.isoformat(timespec='seconds')}",
        f"Elapsed seconds: {elapsed_seconds}",
        f"Accepted taxon: {report.accepted_name}",
        f"Search names: {' | '.join(report.search_names)}",
        f"Selected sources: {len(report.sources)}",
        f"Image resolution: {report.image_resolution}",
        "",
        "=== Totals ===",
        f"Records found: {report.records_found}",
        f"DwC records (after deduplication and manual review): {len(report.records) - len(manually_excluded)}",
        f"Specimens excluded by manual miss_ review: {len(manually_excluded)}",
        f"Images downloaded (present in output/images): {report.images_downloaded}",
        f"Errors: {len(report.errors)}",
        "",
        f"Duplicate portal records merged: {merged_count}",
        f"Duplicate gatherings grouped: {duplicate_gathering_count(report.records)}",
        f"Records with coordinates: {coordinate_count}",
        f"Records linked to images: {image_link_count}",
        f"Type specimens: {type_count}",
        f"Images newly downloaded this run: {image_counts.get('downloaded', 0)}",
        f"Images already present (reused): {image_counts.get('already_downloaded', 0)}",
        f"Images failed: {image_counts.get('rejected_or_failed', 0)}",
        f"Images skipped: {image_counts.get('skipped_by_option', 0)}",
        f"JPEG files in output/images: {report.images_in_folder}",
        f"Unreferenced JPEGs still in output/images: {unreferenced_images}",
    ]
    if unreferenced_images > 0:
        lines.append(
            "  (These are not referenced by the current DwC. They were kept "
            "by the default preservation policy or because a run was incomplete. "
            "Explicit --prune-unreferenced-images removes these only after a complete "
            "run; miss_ review markers are always preserved.)"
        )
    if manually_excluded:
        lines.extend(("", "Manual misidentification exclusions (original marked files preserved):"))
        lines.extend(f"- {specimen_code(record)} | {record.occurrence_id or record.source_record_url} | {record.notes}" for record in manually_excluded)
    lines += [
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
    dwc_path = report.output_dir / f"{report.export_stem}.tsv"
    if dwc_path.is_file():
        group = lambda source: source.upper() if source.lower() in {"gbif", "cvh"} else "Other"
        retrieved = Counter()
        for source in report.sources.values():
            retrieved[group(source.source)] += source.records
        unique = Counter(group(record.source) for record in report.records)
        excluded = Counter(group(record.source) for record in manually_excluded)
        provenance = {
            "version": 1, "dwcSha256": hashlib.sha256(dwc_path.read_bytes()).hexdigest(),
            "retrieved": dict(retrieved), "unique": dict(unique), "excluded": dict(excluded),
        }
        lines.extend(("", "Flow provenance: " + json.dumps(provenance, sort_keys=True)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
