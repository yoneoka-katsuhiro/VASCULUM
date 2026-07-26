from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import SpecimenRecord
from .outputs import SourceReport


SCHEMA_VERSION = 1


class CheckpointError(ValueError):
    pass


def checkpoint_path(output_dir: Path) -> Path:
    return output_dir / ".resume" / "checkpoint.json"


def write_checkpoint(
    path: Path,
    *,
    signature: dict[str, object],
    started_at: str,
    phase: str,
    next_source_index: int,
    next_name_index: int,
    records_found: int,
    records: list[SpecimenRecord],
    sources: dict[str, SourceReport],
    errors: list[str],
) -> None:
    data = {
        "schema_version": SCHEMA_VERSION,
        "signature": signature,
        "started_at": started_at,
        "phase": phase,
        "next_source_index": next_source_index,
        "next_name_index": next_name_index,
        "records_found": records_found,
        "records": [record.as_row() for record in records],
        "sources": {
            source: asdict(source_report)
            for source, source_report in sources.items()
        },
        "errors": list(errors),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_checkpoint(path: Path, expected_signature: dict[str, object]) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"Checkpoint could not be read: {exc}") from exc
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CheckpointError("Checkpoint format is not supported by this version.")
    if data.get("signature") != expected_signature:
        raise CheckpointError(
            "Checkpoint options do not match this command. "
            "Use the original options or start again with --restart."
        )
    if data.get("phase") not in {"collecting", "images"}:
        raise CheckpointError("Checkpoint phase is invalid.")
    if not isinstance(data.get("records"), list):
        raise CheckpointError("Checkpoint records are invalid.")
    if not isinstance(data.get("sources"), dict):
        raise CheckpointError("Checkpoint source state is invalid.")
    return data


def records_from_checkpoint(data: dict) -> list[SpecimenRecord]:
    fieldnames = set(SpecimenRecord.fieldnames())
    records: list[SpecimenRecord] = []
    for raw in data.get("records", []):
        if not isinstance(raw, dict):
            continue
        values = {
            key: "" if value is None else str(value)
            for key, value in raw.items()
            if key in fieldnames
        }
        records.append(SpecimenRecord(**values))
    return records


def source_reports_from_checkpoint(
    data: dict,
    source_names: list[str],
) -> dict[str, SourceReport]:
    raw_sources = data.get("sources", {})
    reports: dict[str, SourceReport] = {}
    for source in source_names:
        raw = raw_sources.get(source, {})
        reports[source] = SourceReport(
            source=source,
            status=str(raw.get("status", "pending")),
            records=int(raw.get("records", 0)),
            images=int(raw.get("images", 0)),
            retries=int(raw.get("retries", 0)),
            message=str(raw.get("message", "")),
        )
    return reports
