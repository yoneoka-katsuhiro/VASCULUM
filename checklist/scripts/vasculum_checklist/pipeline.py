from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path

from specimen_collector.http_client import PoliteHttpClient

from . import CHECKLIST_VERSION
from .models import ChecklistReport, SearchArea, SourceReport, TaxonFilter, safe_token
from .outputs import write_outputs
from .pdf import download_representative_images, require_codex_image_selector
from .runtime import load_env_file, read_json, vasculum_version
from .sources import add_gbif_image_candidates, build_species_entries, gbif_region_records
from .taxonomy import load_family_order, sort_species_by_taxonomy


SOURCE_ALIASES = {
    "gbif": "gbif",
    "cvh": "cvh",
}


def normalize_source_name(name: str) -> str:
    key = re.sub(r"\s+", " ", name.strip()).lower()
    return SOURCE_ALIASES.get(key, re.sub(r"[^a-z0-9_-]+", "", key))


def normalize_sources(names: list[str] | None) -> list[str]:
    requested = names or ["gbif"]
    result: list[str] = []
    seen: set[str] = set()
    for name in requested:
        normalized = normalize_source_name(name)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def checklist_output_dir(project_dir: Path, title: str, area: SearchArea) -> Path:
    date = datetime.now().astimezone().date().isoformat()
    token = safe_token(title or area.label() or "checklist")
    return project_dir / "output" / "checklist" / f"{date}_{token}"


def run_checklist(
    *,
    project_dir: Path,
    contact_email: str,
    area: SearchArea,
    taxon_filter: TaxonFilter,
    requested_sources: list[str] | None,
    title: str,
    output_dir: Path | None,
    raw_command: str,
    max_gbif_records: int | None,
    page_size: int,
    gbif_image_limit: int,
    image_radius_max: float,
    cvh_image_limit: int,
    max_pdf_images: int,
    dry_run: bool,
) -> ChecklistReport:
    load_env_file(project_dir / ".env")
    area.validate()
    if page_size <= 0:
        raise ValueError("--page-size must be greater than 0.")
    if max_gbif_records is not None and max_gbif_records <= 0:
        raise ValueError("--limit must be greater than 0.")
    if cvh_image_limit < 0:
        raise ValueError("--cvh-image-limit must be 0 or greater.")
    if gbif_image_limit < 0:
        raise ValueError("--gbif-image-limit must be 0 or greater.")
    if max_pdf_images < 0:
        raise ValueError("--max-pdf-images must be 0 or greater.")

    sources = normalize_sources(requested_sources)
    unknown = [source for source in sources if source not in SOURCE_ALIASES.values()]
    if unknown:
        raise ValueError(f"Unknown CheckList source names: {', '.join(unknown)}")

    settings = read_json(project_dir / "config" / "source_settings.json")
    family_ranks = load_family_order(project_dir)
    download_settings = settings.get("download", {})
    destination = output_dir.resolve() if output_dir else checklist_output_dir(project_dir, title, area)
    report = ChecklistReport(
        version=vasculum_version(project_dir),
        checklist_version=CHECKLIST_VERSION,
        title=title or "VASCULUM CheckList",
        search_area=area,
        taxon_filter=taxon_filter,
        output_dir=destination,
        raw_command=raw_command,
        started_at=datetime.now().astimezone(),
        sources={source: SourceReport(source=source) for source in sources},
    )

    if dry_run:
        for source_report in report.sources.values():
            source_report.status = "validated"
        report.finished_at = datetime.now().astimezone()
        return report

    client = PoliteHttpClient(
        contact_email=contact_email,
        timeout_seconds=int(download_settings.get("timeout_seconds", 60)),
        retry_count=int(download_settings.get("retry_count", 4)),
        retry_backoff_seconds=float(download_settings.get("retry_backoff_seconds", 5.0)),
    )

    with tempfile.TemporaryDirectory(prefix="vasculum_checklist_") as temporary:
        temporary_root = Path(temporary)
        if "gbif" in report.sources:
            source_report = report.sources["gbif"]
            try:
                result = gbif_region_records(
                    client=client,
                    area=area,
                    taxon_filter=taxon_filter,
                    raw_dir=temporary_root / "gbif",
                    max_records=max_gbif_records,
                    page_size=page_size,
                    request_delay_seconds=float(settings.get("gbif", {}).get("request_delay_seconds", 1.0)),
                )
                report.evidence_records = result.records
                report.records_found = len(result.records)
                report.species = sort_species_by_taxonomy(
                    build_species_entries(result.records),
                    family_ranks,
                )
                source_report.status = "partial" if result.limit_reached else "complete"
                source_report.records = len(result.records)
                source_report.species = len(report.species)
                source_report.message = result.message
                if result.total_reported is not None:
                    source_report.message += f"; GBIF reported {result.total_reported} matching occurrence records before local filters"
                if result.limit_reached:
                    report.errors.append(
                        "GBIF scan limit reached before end of records; increase --limit for a more complete checklist."
                    )
            except Exception as exc:
                source_report.status = "failed"
                source_report.message = str(exc)
                report.errors.append(f"gbif: {exc}")
        if not report.species:
            raise ValueError("No species were found for the supplied search conditions.")

        selector, _evaluation_mode, initial_batch_size = require_codex_image_selector(len(report.species))

        report.sources["gbif_images"] = SourceReport(source="gbif_images")
        image_report = report.sources["gbif_images"]
        found_images, message = add_gbif_image_candidates(
            client=client,
            species_entries=report.species,
            area=area,
            taxon_filter=taxon_filter,
            raw_dir=temporary_root / "gbif_images",
            limit_per_species=gbif_image_limit,
            max_radius_km=image_radius_max,
            request_delay_seconds=0.8,
        )
        image_report.status = "complete" if message.startswith("complete") else "partial"
        image_report.records = found_images
        image_report.species = sum(
            1
            for entry in report.species
            if any(candidate.get("source", "").lower() == "gbif" for candidate in entry.image_candidates)
        )
        image_report.message = message
        if message.startswith("partial:"):
            report.errors.append(f"gbif_images: {message}")

    report.finished_at = datetime.now().astimezone()
    cvh_enabled = "cvh" in report.sources
    cvh_settings = dict(settings.get("cvh", {})) if cvh_enabled else None
    with tempfile.TemporaryDirectory(prefix="vasculum_checklist_cvh_") as cvh_temp:
        image_result = download_representative_images(
            client,
            report,
            max_images=max_pdf_images,
            cvh_settings=cvh_settings,
            cvh_raw_dir=Path(cvh_temp) if cvh_enabled else None,
            cvh_image_limit=cvh_image_limit if cvh_enabled else 0,
            selector=selector,
            initial_candidate_batch_size=initial_batch_size,
        )
    if cvh_enabled:
        source_report = report.sources["cvh"]
        source_report.status = "partial" if image_result.cvh_message.startswith("partial:") else "complete"
        source_report.records = image_result.cvh_records
        source_report.species = len(image_result.cvh_species)
        source_report.message = (
            "CVH used for representative-image valid-candidate top-up only; "
            f"{image_result.cvh_message}"
        )
        if image_result.cvh_message.startswith("partial:"):
            report.errors.append(f"cvh: {image_result.cvh_message}")
    report.sources["pdf_images"] = SourceReport(
        source="pdf_images",
        status="complete",
        records=image_result.downloaded,
        species=sum(1 for entry in report.species if entry.local_image_path),
        message="representative images downloaded for PDF",
    )
    write_outputs(report, client)
    return report
