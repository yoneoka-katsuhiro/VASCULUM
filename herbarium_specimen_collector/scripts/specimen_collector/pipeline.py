from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from .checkpoint import (
    checkpoint_path,
    read_checkpoint,
    records_from_checkpoint,
    source_reports_from_checkpoint,
    write_checkpoint,
)
from .diagnostics import RunLogger
from .http_client import PoliteHttpClient
from .images import download_images, gbif_image_cache_urls, prune_unreferenced_images
from .models import SpecimenRecord
from .output_paths import resolve_output_directory
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
}

HANDLERS_WITHOUT_SOURCE: dict[str, Callable[..., list[SpecimenRecord]]] = {
    "cvh": cvh_records,
    "taif": taif_records,
    "ti_type": ti_type_records,
    "tns_webmuseum": tns_webmuseum_records,
}
CONTROL_HANDLERS = {"gbif", "multi"}


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
    if handler == "multi":
        records: list[SpecimenRecord] = []
        messages: list[str] = []
        components = source_config.get("components") or []
        if not isinstance(components, list) or not components:
            raise ValueError(f"{source}: no components configured")
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


def collector_version(project_dir: Path) -> str:
    version_file = project_dir / "VERSION.txt"
    return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "v0.1.4"


def image_download_settings(settings: dict, profile_name: str) -> dict:
    download = settings.get("download", {})
    if not isinstance(download, dict):
        raise ValueError("The download configuration must be an object.")
    profiles = download.get("image_resolution_profiles", {})
    if not isinstance(profiles, dict) or profile_name not in profiles:
        raise ValueError(f"Unknown image resolution profile: {profile_name}")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise ValueError(f"Image resolution profile '{profile_name}' must be an object.")
    merged = {
        key: value
        for key, value in download.items()
        if key != "image_resolution_profiles"
    }
    merged.update(profile)
    return merged


def configure_runtime_caches(config: dict, cache_root: Path) -> None:
    handler = str(config.get("handler", "")).strip().lower()
    if handler == "symbiota":
        base_url = str(config.get("base_url", "symbiota"))
        config["_shared_cache_root"] = str(
            cache_root / "symbiota" / safe_token(base_url)
        )
    for component in config.get("components", []):
        if isinstance(component, dict):
            configure_runtime_caches(component, cache_root)


def run_signature(
    *,
    version: str,
    accepted_name: str,
    names: list[str],
    sources: list[str],
    max_records_per_name: int | None,
    skip_images: bool,
    image_resolution: str,
    gbif_settings: dict,
    settings: dict,
) -> dict[str, object]:
    return {
        "version": version,
        "accepted_name": accepted_name,
        "search_names": names,
        "sources": sources,
        "max_records_per_name": max_records_per_name,
        "skip_images": skip_images,
        "image_resolution": image_resolution,
        "gbif_occurrence_mode": gbif_settings.get("occurrence_mode", "specimens"),
        "gbif_coordinate_filter": gbif_settings.get("coordinate_filter", "any"),
        "configuration_sha256": hashlib.sha256(
            json.dumps(
                settings,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def append_source_message(source_report: SourceReport, message: str) -> None:
    source_report.message = "; ".join(
        value for value in (source_report.message, message) if value
    )


def run_pipeline(
    *,
    project_dir: Path,
    contact_email: str,
    requested_sources: list[str] | None,
    taxon_names: list[str] | None,
    max_records_per_name: int | None,
    skip_images: bool,
    image_resolution: str,
    dry_run: bool,
    resume: bool = False,
    restart: bool = False,
    output_dir: Path | None = None,
    gbif_occurrence_mode: str | None = None,
    gbif_coordinate_filter: str | None = None,
) -> RunReport:
    if resume and restart:
        raise ValueError("--resume and --restart cannot be used together.")
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
        if isinstance(value, dict) and key not in {"download", "network"}
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

    download_settings = image_download_settings(settings, image_resolution)
    version = collector_version(project_dir)
    signature = run_signature(
        version=version,
        accepted_name=accepted_name,
        names=names,
        sources=enabled_sources,
        max_records_per_name=max_records_per_name,
        skip_images=skip_images,
        image_resolution=image_resolution,
        gbif_settings=settings["gbif"],
        settings=settings,
    )
    destination = resolve_output_directory(
        project_dir=project_dir,
        accepted_name=accepted_name,
        signature=signature,
        output_dir=output_dir,
        resume=resume,
        restart=restart,
    )

    if dry_run:
        report = RunReport(
            version=version,
            accepted_name=accepted_name,
            search_names=names,
            image_resolution=image_resolution,
            output_dir=destination,
            started_at=datetime.now().astimezone(),
            sources={
                source: SourceReport(source=source)
                for source in enabled_sources
            },
        )
        for source_report in report.sources.values():
            source_report.status = "validated"
        report.finished_at = datetime.now().astimezone()
        return report

    state_path = checkpoint_path(destination)
    resume_dir = state_path.parent
    if restart and resume_dir.exists():
        shutil.rmtree(resume_dir)
    if resume and not state_path.exists():
        raise ValueError(
            f"No checkpoint was found at {state_path}. "
            "Run without --resume to start a new collection."
        )
    if state_path.exists() and not resume:
        raise ValueError(
            f"An incomplete run was found at {state_path}. "
            "Use --resume with the same options, or --restart to start again."
        )

    checkpoint_data = (
        read_checkpoint(state_path, signature)
        if resume
        else None
    )
    if checkpoint_data:
        started_at = datetime.fromisoformat(str(checkpoint_data["started_at"]))
        report = RunReport(
            version=version,
            accepted_name=accepted_name,
            search_names=names,
            image_resolution=image_resolution,
            output_dir=destination,
            started_at=started_at,
            records_found=int(checkpoint_data.get("records_found", 0)),
            records=records_from_checkpoint(checkpoint_data),
            sources=source_reports_from_checkpoint(
                checkpoint_data,
                enabled_sources,
            ),
            errors=[
                str(error)
                for error in checkpoint_data.get("errors", [])
            ],
        )
        phase = str(checkpoint_data["phase"])
        next_source_index = int(
            checkpoint_data.get("next_source_index", 0)
        )
        next_name_index = int(checkpoint_data.get("next_name_index", 0))
    else:
        started_at = datetime.now().astimezone()
        report = RunReport(
            version=version,
            accepted_name=accepted_name,
            search_names=names,
            image_resolution=image_resolution,
            output_dir=destination,
            started_at=started_at,
            sources={
                source: SourceReport(source=source)
                for source in enabled_sources
            },
        )
        phase = "collecting"
        next_source_index = 0
        next_name_index = 0

    logger = RunLogger(destination, version)
    report.log_path = logger.path
    logger.event(
        "INFO",
        "run_started",
        mode="resume" if resume else "new",
        accepted_name=accepted_name,
        search_names=names,
        selected_source_count=len(enabled_sources),
        sources=enabled_sources,
        image_resolution=image_resolution,
        output_dir=destination,
        checkpoint=state_path,
        options=signature,
        resume_phase=phase,
        resume_source_index=next_source_index,
        resume_name_index=next_name_index,
    )

    progress = TerminalProgress(enabled_sources)

    for source, source_report in report.sources.items():
        row = progress.rows[source]
        row.status = source_report.status
        row.records = source_report.records
        row.images = source_report.images
        row.retries = source_report.retries
        if source_report.status in {"complete", "partial", "failed"}:
            row.completed = len(names)
            row.total = len(names)
    progress.set_totals(
        records_found=report.records_found or len(report.records),
        physical_specimens=(
            len(report.records) if phase == "images" else 0
        ),
        duplicate_gatherings=(
            duplicate_gathering_count(report.records)
            if phase == "images"
            else 0
        ),
    )

    def count_retry() -> None:
        progress.increment_retry()

    client = PoliteHttpClient(
        contact_email=contact_email,
        timeout_seconds=int(download_settings.get("timeout_seconds", 60)),
        retry_count=int(download_settings.get("retry_count", 4)),
        retry_backoff_seconds=float(download_settings.get("retry_backoff_seconds", 5.0)),
        retry_callback=count_retry,
        logger=logger,
    )

    network_settings = settings.get("network", {})
    host_intervals = (
        network_settings.get("host_request_intervals", {})
        if isinstance(network_settings, dict)
        else {}
    )
    if isinstance(host_intervals, dict):
        for host_url, interval in host_intervals.items():
            client.set_host_interval(str(host_url), float(interval))

    cache_root = resume_dir / "cache"
    for source in enabled_sources:
        configure_runtime_caches(settings[source], cache_root)

    def save_state() -> None:
        for source, row in progress.rows.items():
            report.sources[source].retries = row.retries
        write_checkpoint(
            state_path,
            signature=signature,
            started_at=started_at.isoformat(),
            phase=phase,
            next_source_index=next_source_index,
            next_name_index=next_name_index,
            records_found=report.records_found,
            records=report.records,
            sources=report.sources,
            errors=report.errors,
        )

    try:
        save_state()
        if phase == "collecting":
            records = report.records
            for source_index in range(
                next_source_index,
                len(enabled_sources),
            ):
                source = enabled_sources[source_index]
                source_report = report.sources[source]
                first_name_index = (
                    next_name_index
                    if source_index == next_source_index
                    else 0
                )
                source_had_error = any(
                    error.startswith(f"{source}:")
                    for error in report.errors
                )
                progress.update_source(
                    source,
                    status="processing",
                    completed=first_name_index,
                    total=len(names),
                    records=source_report.records,
                )
                logger.event(
                    "INFO",
                    "source_started",
                    source=source,
                    resume_name_index=first_name_index,
                    total_names=len(names),
                )

                for name_index in range(first_name_index, len(names)):
                    query_name = names[name_index]
                    next_source_index = source_index
                    next_name_index = name_index
                    progress.set_task(f"{source} - searching {query_name}")
                    query_started = datetime.now().astimezone()
                    request_count_before = client.request_count
                    logger.event(
                        "INFO",
                        "source_query_started",
                        source=source,
                        query_name=query_name,
                        name_index=name_index,
                    )
                    try:
                        found, message = collect_source_records(
                            client=client,
                            source=source,
                            query_name=query_name,
                            raw_dir=cache_root / safe_token(source),
                            source_config=settings[source],
                            max_records_per_name=max_records_per_name,
                            refresh=False,
                        )
                        records.extend(found)
                        source_report.records += len(found)
                        progress.set_totals(records_found=len(records))
                        append_source_message(
                            source_report,
                            f"{query_name}: {message}",
                        )
                        if ": failed (" in message.lower():
                            source_had_error = True
                            error = (
                                f"{source}: {query_name}: "
                                "one or more configured components failed"
                            )
                            report.errors.append(error)
                            progress.add_error(error)
                            logger.event(
                                "ERROR",
                                "source_component_failed",
                                source=source,
                                query_name=query_name,
                                message=message,
                            )
                        logger.event(
                            "INFO",
                            "source_query_completed",
                            source=source,
                            query_name=query_name,
                            records=len(found),
                            elapsed_seconds=(
                                datetime.now().astimezone()
                                - query_started
                            ).total_seconds(),
                            network_requests=(
                                client.request_count
                                - request_count_before
                            ),
                            message=message,
                        )
                    except Exception as exc:
                        source_had_error = True
                        error = f"{source}: {query_name}: {exc}"
                        report.errors.append(error)
                        progress.add_error(error)
                        append_source_message(
                            source_report,
                            f"{query_name}: failed ({exc})",
                        )
                        logger.exception(
                            "source_query_failed",
                            exc,
                            source=source,
                            query_name=query_name,
                            network_requests=(
                                client.request_count
                                - request_count_before
                            ),
                        )

                    if name_index + 1 < len(names):
                        next_source_index = source_index
                        next_name_index = name_index + 1
                    else:
                        next_source_index = source_index + 1
                        next_name_index = 0
                    report.records = records
                    report.records_found = len(records)
                    progress.update_source(
                        source,
                        completed=name_index + 1,
                        total=len(names),
                        records=source_report.records,
                    )
                    save_state()

                if source_had_error:
                    source_report.status = "partial" if source_report.records else "failed"
                else:
                    source_report.status = "complete"
                progress.update_source(
                    source,
                    status=source_report.status,
                    completed=len(names),
                    total=len(names),
                    records=source_report.records,
                )
                logger.event(
                    "INFO",
                    "source_completed",
                    source=source,
                    status=source_report.status,
                    records=source_report.records,
                    retries=progress.rows[source].retries,
                )
                save_state()

            report.records_found = len(records)
            report.records = deduplicate(records)
            phase = "images"
            next_source_index = len(enabled_sources)
            next_name_index = 0
            save_state()

        progress.set_totals(
            records_found=report.records_found,
            physical_specimens=len(report.records),
            duplicate_gatherings=duplicate_gathering_count(report.records),
        )
        logger.event(
            "INFO",
            "deduplication_completed",
            records_found=report.records_found,
            physical_specimens=len(report.records),
            duplicate_portal_records=(
                report.records_found - len(report.records)
            ),
            duplicate_gatherings=duplicate_gathering_count(report.records),
        )

        def image_checkpoint(processed: int, total: int) -> None:
            if processed % 25 == 0 or processed == total:
                save_state()
                logger.event(
                    "INFO",
                    "image_checkpoint",
                    processed=processed,
                    total=total,
                )

        image_result = download_images(
            client=client,
            output_dir=destination,
            records=report.records,
            accepted_name=accepted_name,
            source_settings=settings,
            download_settings=download_settings,
            skip_images=skip_images,
            progress=progress,
            source_reports=report.sources,
            checkpoint_callback=image_checkpoint,
            logger=logger,
        )
        save_state()
        logger.event(
            "INFO",
            "image_phase_completed",
            downloaded=image_result.downloaded,
            existing=image_result.existing,
            failed=image_result.failed,
            skipped=image_result.skipped,
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
        logger.event(
            "INFO",
            "outputs_written",
            dwc_csv=destination / "dwc.csv",
            dwc_tsv=destination / "dwc.tsv",
            summary=destination / "summary.txt",
            image_directory=destination / "images",
        )
        progress.set_task(f"complete - {destination}")
        logger.event(
            "INFO",
            "run_completed",
            errors=len(report.errors),
            records=report.records_found,
            physical_specimens=len(report.records),
            network_requests=client.request_count,
            retry_events=client.retry_events,
        )
        shutil.rmtree(resume_dir)
    except BaseException as exc:
        try:
            save_state()
            logger.event(
                "INFO",
                "checkpoint_saved_after_stop",
                phase=phase,
                next_source_index=next_source_index,
                next_name_index=next_name_index,
                records=len(report.records),
            )
        except Exception as checkpoint_exc:
            logger.exception(
                "checkpoint_save_failed",
                checkpoint_exc,
            )
        logger.exception(
            "run_stopped",
            exc,
            phase=phase,
            next_source_index=next_source_index,
            next_name_index=next_name_index,
            network_requests=client.request_count,
            retry_events=client.retry_events,
        )
        print(f"Diagnostic log: {logger.path}", file=sys.stderr)
        print(
            f"Resume checkpoint: {state_path}",
            file=sys.stderr,
        )
        raise
    finally:
        progress.finish()
        logger.close()
    return report
