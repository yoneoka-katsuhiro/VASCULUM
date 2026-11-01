from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class LabelRead:
    catalog_number: str = ""
    occurrence_id: str = ""
    image_path: str = ""
    image_paths: List[str] = field(default_factory=list)
    image_width: str = ""
    image_height: str = ""
    image_file_size_bytes: str = ""
    image_quality_status: str = ""
    image_quality_remarks: str = ""
    detected_languages: List[str] = field(default_factory=list)
    label_transcription: str = ""
    locality_text: str = ""
    event_date_text: str = ""
    collector_text: str = ""
    elevation_text: str = ""
    label_source: str = ""
    label_status: str = ""
    coordinate_uncertainty_meters: str = ""


@dataclass
class CoordinateCandidate:
    catalog_number: str = ""
    occurrence_id: str = ""
    image_path: str = ""
    image_paths: str = ""
    input_type: str = ""
    input_source: str = ""
    image_width: str = ""
    image_height: str = ""
    image_file_size_bytes: str = ""
    image_quality_status: str = ""
    image_quality_remarks: str = ""
    detected_languages: str = ""
    label_source: str = ""
    label_status: str = ""
    label_transcription: str = ""
    locality_text: str = ""
    event_date_text: str = ""
    collector_text: str = ""
    elevation_text: str = ""
    habitat_prior: str = ""
    habitat_review_status: str = "not_selected"
    habitat_review_remarks: str = ""
    habitat_review_sources: str = ""
    candidate_rank: int = 0
    candidate_latitude: str = ""
    candidate_longitude: str = ""
    candidate_geodetic_datum: str = ""
    candidate_uncertainty_meters: str = ""
    reported_uncertainty_meters: str = ""
    uncertainty_basis: str = "unknown"
    uncertainty_components: str = ""
    source_latitude: str = ""
    source_longitude: str = ""
    source_datum: str = ""
    coordinate_operation: str = ""
    support_status: str = "unverified"
    supporting_geometry: str = ""
    source_checks: str = ""
    original_latitude: str = ""
    original_longitude: str = ""
    original_datum: str = ""
    route_support: str = ""
    curation_status: str = "review"
    curation_reasons: str = ""
    candidate_elevation_meters: str = ""
    candidate_type: str = ""
    modern_place_name: str = ""
    historical_place_name: str = ""
    match_language: str = ""
    source_urls: str = ""
    evidence_layers: str = ""
    evidence: str = ""
    score: str = ""
    selected: str = ""
    decision: str = ""
    verification_status: str = ""
    candidate_source: str = ""
    remarks: str = ""

    @classmethod
    def column_map(cls):
        return [
            ("catalogNumber", "catalog_number"),
            ("occurrenceID", "occurrence_id"),
            ("imagePath", "image_path"),
            ("imagePaths", "image_paths"),
            ("inputType", "input_type"),
            ("inputSource", "input_source"),
            ("imageWidth", "image_width"),
            ("imageHeight", "image_height"),
            ("imageFileSizeBytes", "image_file_size_bytes"),
            ("imageQualityStatus", "image_quality_status"),
            ("imageQualityRemarks", "image_quality_remarks"),
            ("detectedLanguages", "detected_languages"),
            ("labelSource", "label_source"),
            ("labelStatus", "label_status"),
            ("labelTranscription", "label_transcription"),
            ("localityText", "locality_text"),
            ("eventDateText", "event_date_text"),
            ("collectorText", "collector_text"),
            ("elevationText", "elevation_text"),
            ("habitatPrior", "habitat_prior"),
            ("habitatReviewStatus", "habitat_review_status"),
            ("habitatReviewRemarks", "habitat_review_remarks"),
            ("habitatReviewSources", "habitat_review_sources"),
            ("candidateRank", "candidate_rank"),
            ("candidateLatitude", "candidate_latitude"),
            ("candidateLongitude", "candidate_longitude"),
            ("candidateGeodeticDatum", "candidate_geodetic_datum"),
            ("candidateUncertaintyMeters", "candidate_uncertainty_meters"),
            ("reportedUncertaintyMeters", "reported_uncertainty_meters"),
            ("uncertaintyBasis", "uncertainty_basis"),
            ("uncertaintyComponents", "uncertainty_components"),
            ("sourceLatitude", "source_latitude"),
            ("sourceLongitude", "source_longitude"),
            ("sourceGeodeticDatum", "source_datum"),
            ("coordinateOperation", "coordinate_operation"),
            ("supportStatus", "support_status"),
            ("supportingGeometry", "supporting_geometry"),
            ("sourceChecks", "source_checks"),
            ("originalDecimalLatitude", "original_latitude"),
            ("originalDecimalLongitude", "original_longitude"),
            ("originalGeodeticDatum", "original_datum"),
            ("routeSupport", "route_support"),
            ("curationStatus", "curation_status"),
            ("curationReasons", "curation_reasons"),
            ("candidateElevationMeters", "candidate_elevation_meters"),
            ("candidateType", "candidate_type"),
            ("modernPlaceName", "modern_place_name"),
            ("historicalPlaceName", "historical_place_name"),
            ("matchLanguage", "match_language"),
            ("sourceUrls", "source_urls"),
            ("evidenceLayers", "evidence_layers"),
            ("evidence", "evidence"),
            ("score", "score"),
            ("selected", "selected"),
            ("decision", "decision"),
            ("verificationStatus", "verification_status"),
            ("candidateSource", "candidate_source"),
            ("remarks", "remarks"),
        ]

    @classmethod
    def fieldnames(cls):
        return [external for external, _internal in cls.column_map()]

    def as_row(self):
        values = asdict(self)
        return {
            external: "" if values[internal] is None else str(values[internal])
            for external, internal in self.column_map()
        }


@dataclass
class CuratedResult:
    row: dict
    candidates: List[CoordinateCandidate]
    decision: str
    verification_status: str
    selected_candidate: Optional[CoordinateCandidate] = None
    notes: List[str] = field(default_factory=list)
    include_in_dwc: bool = True
    exclusion_reason: str = ""
    llm_usage: List[dict] = field(default_factory=list)
    habitat_review: dict = field(default_factory=dict)
    curation_status: str = "unresolved"
    original_coordinates: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    stage_errors: List[dict] = field(default_factory=list)


@dataclass
class RunReport:
    version: str
    input_dir: Path
    input_dwc: Path
    output_dir: Path
    started_at: datetime
    finished_at: Optional[datetime] = None
    curation_mode: str = "standard"
    llm_provider: str = ""
    llm_model: str = ""
    llm_reasoning_effort: str = ""
    llm_web_search: str = ""
    label_model: str = ""
    label_reasoning_effort: str = ""
    llm_usage_reported_requests: int = 0
    llm_input_tokens: int = 0
    llm_cached_input_tokens: int = 0
    llm_output_tokens: int = 0
    label_timeout_seconds: int = 600
    georeference_timeout_seconds: int = 600
    verification_timeout_seconds: int = 600
    worker_count: int = 1
    llm_rate_limit_retries: int = 0
    llm_rate_limit_waits: int = 0
    llm_cache_hits: int = 0
    llm_transcription_requests: int = 0
    llm_georeference_requests: int = 0
    llm_skipped_precise_original: int = 0
    llm_skipped_precise_label: int = 0
    llm_skipped_insufficient_locality: int = 0
    habitat_prior: str = ""
    records_read: int = 0
    records_written: int = 0
    kept_original: int = 0
    corrected_existing: int = 0
    inferred_missing: int = 0
    unresolved: int = 0
    review_required: int = 0
    excluded: int = 0
    excluded_insufficient_locality: int = 0
    excluded_manual_misidentification: int = 0
    candidate_rows: int = 0
    label_sidecar_rows: int = 0
    gazetteer_rows: int = 0
    image_records: int = 0
    image_review_records: int = 0
    image_missing_records: int = 0
    llm_attempted: int = 0
    llm_provider_attempts: int = 0
    max_llm_calls: int = 0
    llm_candidate_rows: int = 0
    llm_errors: int = 0
    geospatial_refinement_attempted: int = 0
    geospatial_refinement_succeeded: int = 0
    habitat_checks_attempted: int = 0
    habitat_review_warnings: int = 0
    habitat_review_unknown: int = 0
    habitat_review_unavailable: int = 0
    geospatial_refinement_warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    georeference_method: str = "evidence-assisted"
    max_uncertainty_meters: Optional[float] = None
    accepted: int = 0
    review: int = 0
    failed: int = 0
    resumed_records: int = 0
    checkpoint_path: str = ""
    configuration: dict = field(default_factory=dict)
    personal_dwc_records: int = 0
    photo_only_records: int = 0
    attached_personal_images: int = 0
    input_files: List[dict] = field(default_factory=list)
    input_warnings: List[str] = field(default_factory=list)
    images_exported: int = 0

    @property
    def partial_failure(self) -> bool:
        return bool(self.errors)
