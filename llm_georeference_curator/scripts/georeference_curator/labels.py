from __future__ import annotations

import re

from .inputs import first_present
from .models import LabelRead


def detect_languages(text: str):
    languages = []
    if re.search(r"[\u3040-\u30ff]", text):
        languages.append("ja")
    if re.search(r"[\u4e00-\u9fff]", text):
        languages.append("zh" if "ja" not in languages else "ja/zh")
    if re.search(r"[A-Za-z]", text):
        languages.append("en/la")
    return languages or ["und"]


def dwc_label_text(row) -> str:
    parts = [
        ("country", row.get("country", "")),
        ("stateProvince", row.get("stateProvince", "")),
        ("county", row.get("county", "")),
        ("municipality", row.get("municipality", "")),
        ("locality", row.get("locality", "")),
        ("verbatimLocality", row.get("verbatimLocality", "")),
        ("verbatimElevation", row.get("verbatimElevation", "")),
        ("eventDate", row.get("eventDate", "")),
        ("recordedBy", row.get("recordedBy", "")),
        ("recordNumber", row.get("recordNumber", "")),
    ]
    return "\n".join(f"{key}: {value}" for key, value in parts if value.strip())


def split_languages(value: str):
    return [piece.strip() for piece in re.split(r"[|,;]", value) if piece.strip()]


def read_label(row, catalog_number: str, image_path: str, sidecar_rows) -> LabelRead:
    sidecar = sidecar_rows.get(catalog_number, {})
    if sidecar:
        transcription = first_present(
            sidecar,
            "labelTranscription",
            "label_transcription",
            "transcription",
            "text",
        )
        languages = split_languages(
            first_present(sidecar, "detectedLanguages", "detected_languages", "language")
        )
        if not languages:
            languages = detect_languages(transcription)
        return LabelRead(
            catalog_number=catalog_number,
            image_path=first_present(sidecar, "imagePath", "image_path") or image_path,
            detected_languages=languages,
            label_transcription=transcription,
            locality_text=first_present(sidecar, "localityText", "locality_text")
            or row.get("verbatimLocality", "")
            or row.get("locality", ""),
            event_date_text=first_present(sidecar, "eventDateText", "event_date_text")
            or row.get("eventDate", ""),
            collector_text=first_present(sidecar, "collectorText", "collector_text")
            or row.get("recordedBy", ""),
            elevation_text=first_present(sidecar, "elevationText", "elevation_text")
            or row.get("verbatimElevation", ""),
            label_source="sidecar_tsv",
            label_status="transcribed",
        )

    transcription = dwc_label_text(row)
    return LabelRead(
        catalog_number=catalog_number,
        image_path=image_path,
        detected_languages=detect_languages(transcription),
        label_transcription=transcription,
        locality_text=row.get("verbatimLocality", "") or row.get("locality", ""),
        event_date_text=row.get("eventDate", ""),
        collector_text=row.get("recordedBy", ""),
        elevation_text=row.get("verbatimElevation", ""),
        label_source="dwc_text",
        label_status="image_not_transcribed" if image_path else "dwc_text_only",
    )
