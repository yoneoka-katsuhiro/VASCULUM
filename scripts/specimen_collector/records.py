from __future__ import annotations

import hashlib
import re
from collections import Counter
from urllib.parse import urlparse

from .models import SpecimenRecord


INSTITUTION_ALIASES = {
    "MNHN": "P",
    "MNHN-P": "P",
    "NHMUK": "BM",
    "BRITISHMUSEUM": "BM",
}

TYPE_NAMES = (
    "isolectotype",
    "isoneotype",
    "isoholotype",
    "lectotype",
    "holotype",
    "neotype",
    "syntype",
    "isotype",
    "paratype",
    "epitype",
    "type",
)


def normalize_institution_code(value: str) -> str:
    code = re.sub(r"[^A-Z0-9]+", "", value.upper())
    return INSTITUTION_ALIASES.get(code, code)


def herbarium_code(record: SpecimenRecord) -> str:
    institution = normalize_institution_code(record.institution_code)
    collection = re.sub(r"[^A-Z0-9]+", "", record.collection_code.upper())
    catalog = re.sub(r"[^A-Z0-9]+", "", record.catalog_number.upper())
    plausible_collection = bool(
        re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", collection)
    )
    if plausible_collection and catalog.startswith(collection):
        return collection
    if institution and catalog.startswith(institution):
        return institution
    if plausible_collection:
        return collection
    return institution


def normalized_identity_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return value.strip()
    path = re.sub(r"/+", "/", parsed.path)
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        fragment="",
    ).geturl()


def record_identity(record: SpecimenRecord) -> str:
    institution = herbarium_code(record)
    catalog = re.sub(r"[^A-Z0-9]+", "", record.catalog_number.upper())
    if institution and catalog:
        value = f"institution_catalog|{institution}|{catalog}"
    elif record.occurrence_id:
        occurrence = re.sub(r"\s+", " ", record.occurrence_id.strip()).lower()
        prefix = f"{institution}|" if institution else ""
        value = f"occurrence|{prefix}{occurrence}"
    elif record.image_url:
        value = normalized_identity_url(record.image_url)
    elif record.source_record_url:
        value = normalized_identity_url(record.source_record_url)
    else:
        value = "|".join((record.source, record.source_record_id, record.query_name))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record_priority(record: SpecimenRecord) -> tuple[int, int, int, int]:
    return (
        int(bool(type_token(record.type_status))),
        int(bool(record.image_url)),
        int(bool(record.decimal_latitude and record.decimal_longitude)),
        len(record.locality) + len(record.recorded_by) + len(record.event_date),
    )


def unique_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def append_note(existing: str, note: str) -> str:
    if not note:
        return existing
    return f"{existing}; {note}" if existing else note


def normalize_event_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def collection_event_key(record: SpecimenRecord) -> str:
    collector = normalize_event_text(record.recorded_by)
    number = normalize_event_text(record.record_number)
    date = normalize_event_text(record.event_date)
    locality = normalize_event_text(
        " ".join((record.country, record.state_province, record.locality))
    )
    if not (collector and date and locality):
        return ""
    value = "|".join((collector, number, date, locality))
    return hashlib.sha256(f"collection_event|{value}".encode("utf-8")).hexdigest()


def merge_record_group(key: str, records: list[SpecimenRecord]) -> SpecimenRecord:
    ordered = sorted(records, key=record_priority, reverse=True)
    merged = SpecimenRecord(**ordered[0].as_row())
    excluded = {
        "source",
        "source_record_id",
        "source_record_url",
        "download_url",
        "local_image_path",
        "image_sha256",
        "download_status",
        "notes",
    }
    for candidate in ordered[1:]:
        for field in SpecimenRecord.fieldnames():
            if field not in excluded and not getattr(merged, field) and getattr(candidate, field):
                setattr(merged, field, getattr(candidate, field))
        if not merged.image_url and candidate.image_url:
            merged.image_url = candidate.image_url
            merged.original_image_url = candidate.original_image_url or candidate.image_url
            merged.download_status = candidate.download_status

    merged.physical_specimen_key = key
    merged.collection_event_key = collection_event_key(merged)
    merged.merged_from_sources = "; ".join(unique_values([item.source for item in records]))
    merged.merged_record_count = str(len(records))
    return merged


def deduplicate(records: list[SpecimenRecord]) -> list[SpecimenRecord]:
    groups: dict[str, list[SpecimenRecord]] = {}
    for record in records:
        groups.setdefault(record_identity(record), []).append(record)

    result: list[SpecimenRecord] = []
    emitted: set[str] = set()
    for record in records:
        key = record_identity(record)
        if key not in emitted:
            emitted.add(key)
            result.append(merge_record_group(key, groups[key]))
    return result


def specimen_code(record: SpecimenRecord) -> str:
    institution = herbarium_code(record)
    catalog = re.sub(r"[^A-Z0-9]+", "", record.catalog_number.upper())
    if catalog and (not institution or catalog.startswith(institution)):
        return catalog
    if institution and catalog:
        return f"{institution}{catalog}"
    if catalog:
        return catalog

    source_id = re.sub(
        r"[^A-Z0-9]+",
        "",
        (record.source_record_id or record.occurrence_id).upper(),
    )
    if institution and source_id and not source_id.startswith(institution):
        return f"{institution}{source_id}"
    if source_id:
        source_code = normalize_institution_code(record.source)
        return (
            source_id
            if not source_code or source_id.startswith(source_code)
            else f"{source_code}{source_id}"
        )
    return f"{normalize_institution_code(record.source) or 'SPECIMEN'}UNKNOWN"


def scientific_name_token(value: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z-]*", value)
    selected = words[:2] if len(words) >= 2 else words
    return "_".join(selected) or "Unknown_taxon"


def type_token(value: str) -> str:
    normalized = re.sub(r"[^a-z]+", " ", value.lower())
    for name in TYPE_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", normalized):
            return name
    return ""


def image_basename(record: SpecimenRecord, accepted_name: str) -> str:
    parts = [specimen_code(record), scientific_name_token(accepted_name)]
    specimen_type = type_token(record.type_status)
    if specimen_type:
        parts.append(specimen_type)
    return "_".join(parts)


def duplicate_gathering_count(records: list[SpecimenRecord]) -> int:
    counts = Counter(
        record.collection_event_key or collection_event_key(record)
        for record in records
        if record.collection_event_key or collection_event_key(record)
    )
    return sum(1 for count in counts.values() if count > 1)
