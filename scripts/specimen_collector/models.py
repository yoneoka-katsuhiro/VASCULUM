from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass
class SpecimenRecord:
    source: str = ""
    query_name: str = ""
    source_record_id: str = ""
    source_record_url: str = ""
    occurrence_id: str = ""
    institution_code: str = ""
    collection_code: str = ""
    catalog_number: str = ""
    scientific_name: str = ""
    recorded_by: str = ""
    record_number: str = ""
    event_date: str = ""
    country: str = ""
    state_province: str = ""
    locality: str = ""
    verbatim_locality: str = ""
    decimal_latitude: str = ""
    decimal_longitude: str = ""
    elevation: str = ""
    identified_by: str = ""
    type_status: str = ""
    basis_of_record: str = ""
    coordinate_status: str = ""
    image_url: str = ""
    original_image_url: str = ""
    download_url: str = ""
    image_license: str = ""
    rights_holder: str = ""
    local_image_path: str = ""
    image_sha256: str = ""
    accessed_at: str = ""
    physical_specimen_key: str = ""
    collection_event_key: str = ""
    merged_from_sources: str = ""
    merged_record_count: str = ""
    download_status: str = ""
    notes: str = ""

    @classmethod
    def fieldnames(cls) -> list[str]:
        return [field.name for field in fields(cls)]

    def as_row(self) -> dict[str, str]:
        return {key: "" if value is None else str(value) for key, value in asdict(self).items()}
