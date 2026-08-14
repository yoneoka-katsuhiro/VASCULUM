from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def safe_token(value: object, fallback: str = "checklist") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", clean_text(value)).strip("_")
    return text[:140] or fallback


def canonical_species_name(value: str) -> str:
    text = clean_text(value).replace("×", " x ")
    text = re.sub(
        r"\b(?:subsp|ssp|var|forma|f)\.?\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    words = re.findall(r"[A-Za-z][A-Za-z-]*", text)
    if len(words) < 2:
        return ""
    genus = words[0]
    epithet_index = 2 if len(words) > 2 and words[1].lower() == "x" else 1
    if len(words) <= epithet_index:
        return ""
    epithet = words[epithet_index]
    if len(epithet) < 2 or not epithet[0].islower():
        return ""
    return f"{genus} {epithet.lower()}"


def is_hybrid_name(value: str) -> bool:
    text = clean_text(value).replace("×", " x ")
    return bool(
        re.search(r"\b[xX]\s+[a-z][A-Za-z-]*", text)
        or re.search(r"\bnotho", text, flags=re.IGNORECASE)
    )


def authorship_from_name(full_name: str, canonical: str) -> str:
    full = clean_text(full_name)
    canonical = clean_text(canonical)
    if not full or not canonical:
        return ""
    if full.lower().startswith(canonical.lower()):
        return full[len(canonical) :].strip()
    return ""


def unique_sorted(values: set[str]) -> str:
    return " | ".join(sorted(value for value in values if value))


@dataclass(frozen=True)
class SearchArea:
    country: str = ""
    state: str = ""
    prefecture: str = ""
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None

    def validate(self) -> None:
        coords = [self.latitude is not None, self.longitude is not None, self.radius_km is not None]
        if any(coords) and not all(coords):
            raise ValueError("--latitude, --longitude, and --radius must be used together.")
        if self.radius_km is not None and self.radius_km <= 0:
            raise ValueError("--radius must be greater than 0 km.")
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            raise ValueError("--latitude must be between -90 and 90.")
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            raise ValueError("--longitude must be between -180 and 180.")
        if not self.has_value:
            raise ValueError("At least one geographic condition is required.")

    @property
    def has_value(self) -> bool:
        return bool(
            self.country
            or self.state
            or self.prefecture
            or self.latitude is not None
        )

    @property
    def state_or_prefecture(self) -> str:
        return self.prefecture or self.state

    def label(self) -> str:
        parts = []
        if self.country:
            parts.append(self.country)
        if self.state:
            parts.append(self.state)
        if self.prefecture:
            parts.append(self.prefecture)
        if self.latitude is not None and self.longitude is not None and self.radius_km is not None:
            parts.append(f"{self.latitude:g},{self.longitude:g} radius {self.radius_km:g} km")
        return "; ".join(parts)


@dataclass(frozen=True)
class TaxonFilter:
    taxon: str = ""
    cvh_taxa: tuple[str, ...] = ()

    def label(self) -> str:
        parts = []
        if self.taxon:
            parts.append(f"taxon={self.taxon}")
        if self.cvh_taxa:
            parts.append(f"cvh_taxa={', '.join(self.cvh_taxa)}")
        return "; ".join(parts) or "Plantae"


@dataclass
class EvidenceRecord:
    source: str
    source_record_id: str = ""
    source_record_url: str = ""
    occurrence_id: str = ""
    dataset_key: str = ""
    institution_code: str = ""
    catalog_number: str = ""
    family: str = ""
    genus: str = ""
    canonical_species: str = ""
    full_scientific_name: str = ""
    authorship: str = ""
    taxon_rank: str = ""
    taxon_key: str = ""
    accepted_taxon_key: str = ""
    basis_of_record: str = ""
    country: str = ""
    raw_country: str = ""
    state_province: str = ""
    county: str = ""
    municipality: str = ""
    locality: str = ""
    decimal_latitude: str = ""
    decimal_longitude: str = ""
    image_url: str = ""
    image_original_url: str = ""
    image_license: str = ""
    rights_holder: str = ""
    accessed_at: str = ""

    def identity_key(self) -> str:
        if self.occurrence_id:
            return f"occurrence|{self.occurrence_id.strip().lower()}"
        if self.institution_code and self.catalog_number:
            catalog = re.sub(r"[^A-Za-z0-9]+", "", self.catalog_number).upper()
            return f"catalog|{self.institution_code.strip().upper()}|{catalog}"
        if self.source_record_url:
            return f"url|{self.source_record_url.strip().lower()}"
        return f"{self.source}|{self.source_record_id}|{self.canonical_species}"


@dataclass
class SpeciesEntry:
    canonical_species: str
    full_scientific_name: str = ""
    authorship: str = ""
    family: str = ""
    genus: str = ""
    taxon_key: str = ""
    accepted_taxon_key: str = ""
    specimen_count: int = 0
    image_record_count: int = 0
    candidate_image_url: str = ""
    candidate_image_source_record_id: str = ""
    candidate_image_basis_of_record: str = ""
    candidate_image_alternate_url: str = ""
    candidate_image_source: str = ""
    candidate_image_license: str = ""
    candidate_image_rights_holder: str = ""
    candidate_image_institution_code: str = ""
    candidate_image_catalog_number: str = ""
    candidate_image_country: str = ""
    candidate_image_raw_country: str = ""
    candidate_image_state_province: str = ""
    candidate_image_county: str = ""
    candidate_image_municipality: str = ""
    candidate_image_decimal_latitude: str = ""
    candidate_image_decimal_longitude: str = ""
    candidate_image_distance_km: str = ""
    candidate_image_geographic_match: str = ""
    candidate_image_administrative_match: str = ""
    cvh_candidate_image_url: str = ""
    local_image_path: str = ""
    representative_record_url: str = ""
    image_candidates: list[dict[str, str]] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    state_provinces: set[str] = field(default_factory=set)
    counties: set[str] = field(default_factory=set)
    municipalities: set[str] = field(default_factory=set)

    def add_evidence(self, record: EvidenceRecord) -> None:
        self.specimen_count += 1
        if record.image_url:
            self.image_record_count += 1
            self.add_image_candidate(
                record.source,
                record.image_url,
                record.image_license,
                record.rights_holder,
                record.image_original_url,
                record.institution_code,
                record.catalog_number,
                record.country,
                record.state_province,
                record.decimal_latitude,
                record.decimal_longitude,
                "",
                record.source_record_id,
                record.basis_of_record,
                record.county,
                record.municipality,
                raw_country=record.raw_country,
            )
            if not self.candidate_image_url:
                self.candidate_image_url = record.image_url
                self.candidate_image_source_record_id = record.source_record_id
                self.candidate_image_alternate_url = record.image_original_url
                self.candidate_image_source = record.source
                self.candidate_image_license = record.image_license
                self.candidate_image_rights_holder = record.rights_holder
                self.candidate_image_institution_code = record.institution_code
                self.candidate_image_catalog_number = record.catalog_number
                self.candidate_image_country = record.country
                self.candidate_image_raw_country = record.raw_country
                self.candidate_image_state_province = record.state_province
                self.candidate_image_decimal_latitude = record.decimal_latitude
                self.candidate_image_decimal_longitude = record.decimal_longitude
        if record.source_record_url and not self.representative_record_url:
            self.representative_record_url = record.source_record_url
        self.sources.add(record.source)
        if record.country:
            self.countries.add(record.country)
        if record.state_province:
            self.state_provinces.add(record.state_province)
        if record.county:
            self.counties.add(record.county)
        if record.municipality:
            self.municipalities.add(record.municipality)
        for attr in (
            "full_scientific_name",
            "authorship",
            "family",
            "genus",
            "taxon_key",
            "accepted_taxon_key",
        ):
            if not getattr(self, attr) and getattr(record, attr):
                setattr(self, attr, getattr(record, attr))

    def add_image_candidate(
        self,
        source: str,
        image_url: str,
        image_license: str = "",
        rights_holder: str = "",
        alternate_url: str = "",
        institution_code: str = "",
        catalog_number: str = "",
        country: str = "",
        state_province: str = "",
        decimal_latitude: str = "",
        decimal_longitude: str = "",
        distance_km: str = "",
        source_record_id: str = "",
        basis_of_record: str = "",
        county: str = "",
        municipality: str = "",
        geographic_match: str = "",
        raw_country: str = "",
    ) -> None:
        source = clean_text(source)
        image_url = clean_text(image_url)
        if not source or not image_url:
            return
        if source.lower() == "cvh":
            self.cvh_candidate_image_url = image_url
        candidate = {
            "source": source,
            "source_record_id": clean_text(source_record_id),
            "taxon": self.canonical_species,
            "basis_of_record": clean_text(basis_of_record),
            "image_url": image_url,
            "alternate_url": clean_text(alternate_url),
            "license": clean_text(image_license),
            "rights_holder": clean_text(rights_holder),
            "institution_code": clean_text(institution_code),
            "catalog_number": clean_text(catalog_number),
            "country": clean_text(country),
            "raw_country": clean_text(raw_country or country),
            "state_province": clean_text(state_province),
            "county": clean_text(county),
            "municipality": clean_text(municipality),
            "decimal_latitude": clean_text(decimal_latitude),
            "decimal_longitude": clean_text(decimal_longitude),
            "distance_km": clean_text(distance_km),
            "geographic_match": clean_text(geographic_match),
            "administrative_match": "",
        }
        candidate_key = (
            candidate["source"].lower(),
            candidate["source_record_id"],
            candidate["image_url"],
            candidate["alternate_url"],
            candidate["taxon"],
        )
        if not any(
            (
                existing.get("source", "").lower(),
                existing.get("source_record_id", ""),
                existing.get("image_url", ""),
                existing.get("alternate_url", ""),
                existing.get("taxon", ""),
            )
            == candidate_key
            for existing in self.image_candidates
        ):
            self.image_candidates.append(candidate)
        if not self.candidate_image_url:
            self.apply_image_candidate(candidate)

    def apply_image_candidate(self, candidate: dict[str, str]) -> None:
        self.candidate_image_url = clean_text(candidate.get("image_url", ""))
        self.candidate_image_source_record_id = clean_text(candidate.get("source_record_id", ""))
        self.candidate_image_basis_of_record = clean_text(candidate.get("basis_of_record", ""))
        self.candidate_image_alternate_url = clean_text(candidate.get("alternate_url", ""))
        self.candidate_image_source = clean_text(candidate.get("source", ""))
        self.candidate_image_license = clean_text(candidate.get("license", ""))
        self.candidate_image_rights_holder = clean_text(candidate.get("rights_holder", ""))
        self.candidate_image_institution_code = clean_text(candidate.get("institution_code", ""))
        self.candidate_image_catalog_number = clean_text(candidate.get("catalog_number", ""))
        self.candidate_image_country = clean_text(candidate.get("country", ""))
        self.candidate_image_raw_country = clean_text(candidate.get("raw_country", ""))
        self.candidate_image_state_province = clean_text(candidate.get("state_province", ""))
        self.candidate_image_county = clean_text(candidate.get("county", ""))
        self.candidate_image_municipality = clean_text(candidate.get("municipality", ""))
        self.candidate_image_decimal_latitude = clean_text(candidate.get("decimal_latitude", ""))
        self.candidate_image_decimal_longitude = clean_text(candidate.get("decimal_longitude", ""))
        self.candidate_image_distance_km = clean_text(candidate.get("distance_km", ""))
        self.candidate_image_geographic_match = clean_text(candidate.get("geographic_match", ""))
        self.candidate_image_administrative_match = clean_text(candidate.get("administrative_match", ""))

    def sort_key(self) -> tuple[str, str, str]:
        return (
            self.family.lower(),
            (self.genus or self.canonical_species.split(" ", 1)[0]).lower(),
            self.canonical_species.lower(),
        )

    def as_row(self, checklist_page: int | None = None) -> dict[str, str]:
        row = {
            "family": self.family,
            "genus": self.genus or self.canonical_species.split(" ", 1)[0],
            "scientificName": self.full_scientific_name or self.canonical_species,
            "canonicalSpecies": self.canonical_species,
            "authorship": self.authorship,
            "taxonKey": self.accepted_taxon_key or self.taxon_key,
            "specimenCount": str(self.specimen_count),
            "imageRecordCount": str(self.image_record_count),
            "sources": unique_sorted(self.sources),
            "countries": unique_sorted(self.countries),
            "stateProvinces": unique_sorted(self.state_provinces),
            "counties": unique_sorted(self.counties),
            "municipalities": unique_sorted(self.municipalities),
            "candidateImageUrl": self.candidate_image_url,
            "candidateImageSourceRecordId": self.candidate_image_source_record_id,
            "candidateImageBasisOfRecord": self.candidate_image_basis_of_record,
            "candidateImageAlternateUrl": self.candidate_image_alternate_url,
            "candidateImageSource": self.candidate_image_source,
            "candidateImageLicense": self.candidate_image_license,
            "candidateImageRightsHolder": self.candidate_image_rights_holder,
            "candidateImageInstitutionCode": self.candidate_image_institution_code,
            "candidateImageCatalogNumber": self.candidate_image_catalog_number,
            "candidateImageVoucher": self.image_voucher(),
            "candidateImageCountry": self.candidate_image_country,
            "candidateImageRawCountry": self.candidate_image_raw_country,
            "candidateImageStateProvince": self.candidate_image_state_province,
            "candidateImageCounty": self.candidate_image_county,
            "candidateImageMunicipality": self.candidate_image_municipality,
            "candidateImageDecimalLatitude": self.candidate_image_decimal_latitude,
            "candidateImageDecimalLongitude": self.candidate_image_decimal_longitude,
            "candidateImageDistanceKm": self.candidate_image_distance_km,
            "candidateImageGeographicMatch": self.candidate_image_geographic_match,
            "candidateImageAdministrativeMatch": self.candidate_image_administrative_match,
            "cvhCandidateImageUrl": self.cvh_candidate_image_url,
            "localImagePath": self.local_image_path,
            "representativeRecordUrl": self.representative_record_url,
        }
        if checklist_page is not None:
            row["visualChecklistPage"] = str(checklist_page)
        return row

    def image_voucher(self) -> str:
        institution = clean_text(self.candidate_image_institution_code)
        catalog = clean_text(self.candidate_image_catalog_number)
        compact_catalog = re.sub(r"[^A-Za-z0-9]+", "", catalog)
        compact_institution = re.sub(r"[^A-Za-z0-9]+", "", institution)
        if compact_catalog and compact_institution and compact_catalog.upper().startswith(compact_institution.upper()):
            return compact_catalog
        if compact_institution and compact_catalog:
            return f"{compact_institution}{compact_catalog}"
        return compact_catalog or compact_institution


@dataclass
class SourceReport:
    source: str
    status: str = "pending"
    records: int = 0
    species: int = 0
    message: str = ""


@dataclass
class ChecklistReport:
    version: str
    checklist_version: str
    title: str
    search_area: SearchArea
    taxon_filter: TaxonFilter
    output_dir: Path
    raw_command: str
    started_at: datetime
    finished_at: datetime | None = None
    records_found: int = 0
    evidence_records: list[EvidenceRecord] = field(default_factory=list)
    species: list[SpeciesEntry] = field(default_factory=list)
    sources: dict[str, SourceReport] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def partial_failure(self) -> bool:
        return bool(self.errors)
