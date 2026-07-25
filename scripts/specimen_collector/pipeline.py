from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from .http_client import PoliteHttpClient
from .images import download_images, gbif_image_cache_urls, prune_unreferenced_images
from .models import SpecimenRecord
from .outputs import RunReport, SourceReport, write_dwc_exports, write_summary
from .progress import TerminalProgress
from .records import (
    collection_event_key,
    deduplicate,
    duplicate_gathering_count,
)
from .sources import (
    ala_records,
    brahms_bol_records,
    cvh_records,
    dwca_records,
    gbif_records,
    jabot_records,
    jacq_records,
    kag_records,
    naturalis_records,
    nhm_records,
    nmnh_records,
    rbge_records,
    safe_token,
    symbiota_records,
    tai2_records,
    taif_records,
    ti_type_records,
    tns_webmuseum_records,
    tropicos_records,
)


SOURCE_ALIASES = {
    "gbif": "gbif",
    "pteridoportal": "pteridoportal",
    "pterido portal": "pteridoportal",
    "cvh": "cvh",
    "cnh": "cnh",
    "avh": "avh",
    "reflora": "reflora",
    "uc/jeps": "ucjeps",
    "uc-jeps": "ucjeps",
    "ucjeps": "ucjeps",
    "uc": "ucjeps",
    "jeps": "ucjeps",
    "mnhn": "p",
}

HANDLERS_WITH_SOURCE: dict[str, Callable[..., list[SpecimenRecord]]] = {
    "ala": ala_records,
    "brahms_bol": brahms_bol_records,
    "dwca": dwca_records,
    "jabot": jabot_records,
    "jacq": jacq_records,
    "kag": kag_records,
    "naturalis": naturalis_records,
    "nhm": nhm_records,
    "nmnh": nmnh_records,
    "rbge": rbge_records,
    "symbiota": symbiota_records,
    "tai2": tai2_records,
    "tropicos": tropicos_records,
}

HANDLERS_WITHOUT_SOURCE: dict[str, Callable[..., list[SpecimenRecord]]] = {
    "cvh": cvh_records,
    "taif": taif_records,
    "ti_type": ti_type_records,
    "tns_webmuseum": tns_webmuseum_records,
}
CONTROL_HANDLERS = {"blocked", "gbif", "multi"}


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


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


def prompt_taxon_names() -> list[str]:
    if not sys.stdin.isatty():
        return []
    accepted = input("Scientific name: ").strip()
    synonyms = input("Synonyms, comma-separated (optional): ").strip()
    return [accepted, *[item.strip() for item in synonyms.split(",") if item.strip()]]


def normalize_source_name(name: str) -> str:
    key = re.sub(r"\s+", " ", name.strip()).lower()
    if key in SOURCE_ALIASES:
        return SOURCE_ALIASES[key]
    return re.sub(r"[^a-z0-9_/-]+", "", key).replace("/", "")


def normalize_source_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalized = normalize_source_name(name)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def credential_env_names(config: dict) -> list[str]:
    values = config.get("credential_env") or config.get("credential_envs") or []
    if isinstance(values, str):
        values = [values]
    return [str(item).strip() for item in values if str(item).strip()]


def missing_credentials(config: dict) -> list[str]:
    return [
        name
        for name in credential_env_names(config)
        if not os.environ.get(name, "").strip()
    ]


def credential_pending_message(config: dict, missing: list[str]) -> str:
    credential_type = str(config.get("credential_type") or "API credential").strip()
    application_url = str(config.get("credential_application_url") or "").strip()
    message = (
        f"credentials_pending: requires {credential_type}; "
        f"set_env={','.join(missing)}"
    )
    return f"{message}; application_url={application_url}" if application_url else message


def validate_source_config(source: str, config: dict) -> None:
    handler = str(config.get("handler", source)).strip().lower()
    known = CONTROL_HANDLERS | set(HANDLERS_WITH_SOURCE) | set(HANDLERS_WITHOUT_SOURCE)
    if handler not in known:
        raise ValueError(f"{source}: unsupported handler '{handler}'")
    if handler != "multi":
        return
    components = config.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError(f"{source}: multi handler requires at least one component")
    for index, component in enumerate(components, start=1):
        if not isinstance(component, dict):
            raise ValueError(f"{source}: component {index} must be an object")
        name = str(component.get("component_name") or f"component_{index}")
        validate_source_config(f"{source}/{name}", component)


def _run_adapter(
    *,
    handler: str,
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    common = {
        "client": client,
        "query_name": query_name,
        "raw_dir": raw_dir,
        "settings": settings,
        "max_records": max_records,
        "record_offset": record_offset,
        "refresh": refresh,
    }
    if handler == "gbif":
        return gbif_records(**common, source_name=source)
    if handler in HANDLERS_WITHOUT_SOURCE:
        return HANDLERS_WITHOUT_SOURCE[handler](**common)
    if handler in HANDLERS_WITH_SOURCE:
        return HANDLERS_WITH_SOURCE[handler](**common, source=source)
    raise ValueError(f"Unsupported source handler: {handler}")


def collect_source_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    source_config: dict,
    max_records_per_name: int | None,
    record_offset: int = 0,
    refresh: bool = True,
) -> tuple[list[SpecimenRecord], str]:
    handler = str(source_config.get("handler", source)).strip().lower()
    missing = missing_credentials(source_config)
    if missing:
        return [], credential_pending_message(source_config, missing)
    if handler == "blocked":
        reason = source_config.get(
            "blocked_reason",
            "not available to automated public access",
        )
        return [], f"unavailable: {reason}"
    if handler == "multi":
        records: list[SpecimenRecord] = []
        messages: list[str] = []
        components = source_config.get("components") or []
        if not isinstance(components, list) or not components:
            return [], "unavailable: no components configured"
        for index, component in enumerate(components, start=1):
            if not isinstance(component, dict):
                continue
            component_name = str(
                component.get("component_name")
                or component.get("handler")
                or f"component_{index}"
            )
            remaining = (
                None
                if max_records_per_name is None
                else max_records_per_name - len(records)
            )
            if remaining is not None and remaining <= 0:
                break
            try:
                found, message = collect_source_records(
                    client=client,
                    source=source,
                    query_name=query_name,
                    raw_dir=raw_dir / safe_token(component_name),
                    source_config=component,
                    max_records_per_name=remaining,
                    record_offset=record_offset,
                    refresh=refresh,
                )
                records.extend(found)
                messages.append(f"{component_name}: {len(found)} records ({message})")
            except Exception as exc:
                messages.append(f"{component_name}: failed ({exc})")
                if bool(component.get("required", False)):
                    raise
        note = str(source_config.get("source_note", "")).strip()
        return records, "; ".join(item for item in [note, *messages] if item)

    records = _run_adapter(
        handler=handler,
        client=client,
        source=source,
        query_name=query_name,
        raw_dir=raw_dir,
        settings=source_config,
        max_records=max_records_per_name,
        record_offset=record_offset,
        refresh=refresh,
    )
    return records, str(source_config.get("source_note", handler))


def source_query_status(message: str, record_count: int) -> str:
    if record_count:
        return "complete"
    lowered = message.lower()
    if "credentials_pending" in lowered or "unavailable:" in lowered:
        return "unavailable"
    return "complete"


def collector_version(project_dir: Path) -> str:
    version_file = project_dir / "VERSION.txt"
    return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "v0.1.1"


def run_pipeline(
    *,
    project_dir: Path,
    contact_email: str,
    requested_sources: list[str] | None,
    taxon_names: list[str] | None,
    max_records_per_name: int | None,
    skip_images: bool,
    dry_run: bool,
    output_dir: Path | None = None,
    gbif_occurrence_mode: str | None = None,
    gbif_coordinate_filter: str | None = None,
) -> RunReport:
    load_env_file(project_dir / ".env")
    settings = read_json(project_dir / "config" / "source_settings.json")
    names = taxon_names or prompt_taxon_names()
    if not names or not names[0]:
        raise ValueError("A scientific name is required. Use --taxon or run in a terminal.")

    accepted_name = names[0]
    enabled_sources = normalize_source_names(
        requested_sources or list(settings.get("enabled_sources", []))
    )
    source_settings = {
        key: value
        for key, value in settings.items()
        if isinstance(value, dict) and key != "download"
    }
    unknown = [source for source in enabled_sources if source not in source_settings]
    if unknown:
        raise ValueError(f"Unknown source names: {', '.join(unknown)}")
    for source in enabled_sources:
        validate_source_config(source, settings[source])

    if gbif_occurrence_mode:
        settings["gbif"]["occurrence_mode"] = gbif_occurrence_mode
    if gbif_coordinate_filter:
        settings["gbif"]["coordinate_filter"] = gbif_coordinate_filter

    destination = (
        output_dir.resolve()
        if output_dir
        else project_dir / "output" / safe_token(accepted_name)
    )
    started_at = datetime.now().astimezone()
    report = RunReport(
        version=collector_version(project_dir),
        accepted_name=accepted_name,
        search_names=names,
        output_dir=destination,
        started_at=started_at,
        sources={source: SourceReport(source=source) for source in enabled_sources},
    )
    if dry_run:
        for source_report in report.sources.values():
            source_report.status = "validated"
        report.finished_at = datetime.now().astimezone()
        return report

    progress = TerminalProgress(enabled_sources)
    def count_retry() -> None:
        progress.increment_retry()

    download_settings = settings.get("download", {})
    client = PoliteHttpClient(
        contact_email=contact_email,
        timeout_seconds=int(download_settings.get("timeout_seconds", 60)),
        retry_count=int(download_settings.get("retry_count", 4)),
        retry_backoff_seconds=float(download_settings.get("retry_backoff_seconds", 5.0)),
        retry_callback=count_retry,
    )
    records: list[SpecimenRecord] = []

    try:
        with tempfile.TemporaryDirectory(prefix="herbarium_collector_") as temporary:
            temporary_root = Path(temporary)
            for source in enabled_sources:
                source_report = report.sources[source]
                messages: list[str] = []
                source_had_error = False
                progress.update_source(
                    source,
                    status="processing",
                    completed=0,
                    total=len(names),
                )
                for position, query_name in enumerate(names, start=1):
                    progress.set_task(f"{source} - searching {query_name}")
                    try:
                        found, message = collect_source_records(
                            client=client,
                            source=source,
                            query_name=query_name,
                            raw_dir=temporary_root / safe_token(source),
                            source_config=settings[source],
                            max_records_per_name=max_records_per_name,
                        )
                        records.extend(found)
                        source_report.records += len(found)
                        progress.set_totals(records_found=len(records))
                        messages.append(f"{query_name}: {message}")
                        if ": failed (" in message.lower():
                            source_had_error = True
                            error = (
                                f"{source}: {query_name}: "
                                "one or more configured components failed"
                            )
                            report.errors.append(error)
                            progress.add_error(error)
                    except Exception as exc:
                        source_had_error = True
                        error = f"{source}: {query_name}: {exc}"
                        report.errors.append(error)
                        progress.add_error(error)
                        messages.append(f"{query_name}: failed ({exc})")
                    progress.update_source(
                        source,
                        completed=position,
                        total=len(names),
                        records=source_report.records,
                    )

                source_report.message = "; ".join(messages)
                if source_had_error:
                    source_report.status = "partial" if source_report.records else "failed"
                else:
                    source_report.status = source_query_status(
                        source_report.message,
                        source_report.records,
                    )
                progress.update_source(
                    source,
                    status=source_report.status,
                    completed=len(names),
                    total=len(names),
                    records=source_report.records,
                )

        report.records_found = len(records)
        report.records = deduplicate(records)
        progress.set_totals(
            records_found=report.records_found,
            physical_specimens=len(report.records),
            duplicate_gatherings=duplicate_gathering_count(report.records),
        )
        download_images(
            client=client,
            output_dir=destination,
            records=report.records,
            accepted_name=accepted_name,
            source_settings=settings,
            download_settings=download_settings,
            skip_images=skip_images,
            progress=progress,
            source_reports=report.sources,
        )
        for error in progress.errors:
            if error not in report.errors:
                report.errors.append(error)
        if not report.errors and not skip_images:
            prune_unreferenced_images(destination, report.records)
        for source, row in progress.rows.items():
            report.sources[source].retries = row.retries

        report.finished_at = datetime.now().astimezone()
        write_dwc_exports(destination, report.records, accepted_name)
        write_summary(destination / "summary.txt", report)
        progress.set_task(f"complete - {destination}")
    finally:
        progress.finish()
    return report
