from __future__ import annotations

import hashlib
import math
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from specimen_collector.http_client import PoliteHttpClient, download_and_validate_image

from . import GENERATED_WITH
from .geo import canonical_country_code, country_code_for, distance_km
from .models import ChecklistReport, SpeciesEntry, safe_token
from .progress import Heartbeat, progress, progress_bar
from .sources import add_cvh_image_candidates_for_entry
from .taxonomy import checklist_page_numbers, families_in_species_order


A4_WIDTH = 1240
A4_HEIGHT = 1754
MARGIN = 54
CARD_GAP = 16
GRID_TOP = 92
CARD_WIDTH = 520
CARD_HEIGHT = 796
TILE_SIZE = 256
OSM_TILE_URL = "https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
MAX_WEB_MERCATOR_LATITUDE = 85.05112878

FAMILY_COLORS = [
    (54, 113, 129),
    (93, 132, 75),
    (151, 102, 60),
    (126, 91, 145),
    (66, 125, 102),
    (153, 82, 86),
    (82, 102, 156),
    (141, 126, 54),
]

MAX_REPRESENTATIVE_IMAGE_CANDIDATES = 10
FINAL_PDF_MAX_IMAGE_EDGE = 2400
FINAL_PDF_JPEG_QUALITY = 88


@dataclass
class DownloadedImageCandidate:
    candidate: dict[str, str]
    path: Path
    sha256: str
    url: str


@dataclass
class RepresentativeImageDownloadResult:
    downloaded: int = 0
    cvh_records: int = 0
    cvh_species: set[str] = field(default_factory=set)
    cvh_message: str = "not requested"


def adaptive_image_evaluation_mode(species_count: int) -> tuple[str, int]:
    if species_count <= 200:
        return "SMALL", 10
    if species_count <= 500:
        return "MEDIUM", 5
    return "LARGE", 3


def font_path() -> str:
    candidates = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return ""


def load_font(size: int) -> ImageFont.ImageFont:
    path = font_path()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def fitted_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    *,
    start_size: int,
    min_size: int,
) -> ImageFont.ImageFont:
    for size in range(start_size, min_size - 1, -1):
        font = load_font(size)
        if text_size(draw, text, font)[0] <= max_width:
            return font
    return load_font(min_size)


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if text_size(draw, word, font)[0] <= max_width:
            current = word
        else:
            current = ""
            chunk = ""
            for char in word:
                candidate_chunk = chunk + char
                if text_size(draw, candidate_chunk, font)[0] <= max_width:
                    chunk = candidate_chunk
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = char
            current = chunk
    if current:
        lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    max_width: int,
    line_spacing: int = 8,
    max_lines: int | None = None,
) -> int:
    x, y = xy
    lines = wrap_text(draw, text, font, max_width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".") + "..."
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += text_size(draw, line, font)[1] + line_spacing
    return y


def new_page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    page = Image.new("RGB", (A4_WIDTH, A4_HEIGHT), "white")
    return page, ImageDraw.Draw(page)


def family_color(family: str, families: list[str]) -> tuple[int, int, int]:
    try:
        index = families.index(family)
    except ValueError:
        index = 0
    return FAMILY_COLORS[index % len(FAMILY_COLORS)]


def draw_page_number(draw: ImageDraw.ImageDraw, number: int, font: ImageFont.ImageFont) -> None:
    text = str(number)
    width, height = text_size(draw, text, font)
    draw.text(((A4_WIDTH - width) // 2, A4_HEIGHT - MARGIN + 10), text, font=font, fill=(80, 80, 80))


def paste_contained(page: Image.Image, image_path: Path, box: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    try:
        with Image.open(image_path) as image:
            image.load()
            image = image.convert("RGB")
            max_width = box[2] - box[0]
            max_height = box[3] - box[1]
            image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            x = box[0] + (max_width - image.width) // 2
            y = box[1] + (max_height - image.height) // 2
            page.paste(image, (x, y))
        return (x, y, x + image.width, y + image.height)
    except (OSError, UnidentifiedImageError):
        return None


def existing_image_is_usable(image_path: Path) -> bool:
    try:
        with Image.open(image_path) as image:
            image.load()
            return image.width >= 120 and image.height >= 120
    except (OSError, UnidentifiedImageError):
        return False


def image_metadata_path(image_path: Path) -> Path:
    return image_path.with_suffix(image_path.suffix + ".json")


def image_metadata_matches(image_path: Path, entry: SpeciesEntry, candidate: dict[str, str]) -> bool:
    metadata_path = image_metadata_path(image_path)
    if not metadata_path.exists():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected_urls = [
        url
        for url in (candidate.get("image_url", ""), candidate.get("alternate_url", ""))
        if url
    ]
    return (
        metadata.get("canonical_species") == entry.canonical_species
        and metadata.get("taxon") == entry.canonical_species
        and metadata.get("source") == candidate.get("source", "")
        and metadata.get("source_record_id") == candidate.get("source_record_id", "")
        and metadata.get("url") in expected_urls
    )


def write_image_metadata(
    image_path: Path,
    entry: SpeciesEntry,
    candidate: dict[str, str],
    url: str,
    *,
    evaluated_url: str = "",
) -> None:
    metadata = {
        "canonical_species": entry.canonical_species,
        "taxon": entry.canonical_species,
        "source": candidate.get("source", ""),
        "source_record_id": candidate.get("source_record_id", ""),
        "basis_of_record": candidate.get("basis_of_record", ""),
        "image_url": candidate.get("image_url", ""),
        "alternate_url": candidate.get("alternate_url", ""),
        "url": url,
        "evaluated_url": evaluated_url,
        "final_pdf_url": url,
        "image_identifier": candidate.get("alternate_url", "") or candidate.get("image_url", ""),
        "distance_km": candidate.get("distance_km", ""),
        "geographic_match": candidate.get("geographic_match", ""),
        "administrative_match": candidate.get("administrative_match", ""),
        "raw_country": candidate.get("raw_country", ""),
        "voucher": entry.image_voucher(),
    }
    image_metadata_path(image_path).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def specimen_image_rejection_reason(image_path: Path, source: str = "", url: str = "") -> str:
    try:
        with Image.open(image_path) as image:
            image.load()
    except (OSError, UnidentifiedImageError):
        return "image file could not be opened"

    text = f"{image_path.name} {url}".lower()
    placeholder_tokens = (
        "placeholder",
        "noimage",
        "no_image",
        "not_available",
        "notavailable",
        "image-not-available",
        "digital_image_not_yet_created",
    )
    if any(token in text for token in placeholder_tokens):
        return "obvious placeholder image"
    return ""


def candidate_metadata_rejection_reason(candidate: dict[str, str], url: str = "") -> str:
    basis = str(candidate.get("basis_of_record") or "").strip().upper()
    if basis and basis not in {"PRESERVED_SPECIMEN", "SPECIMEN"}:
        return f"metadata basisOfRecord is not a preserved specimen ({basis})"
    text = " ".join(
        str(value or "")
        for value in (
            url,
            candidate.get("image_url", ""),
            candidate.get("alternate_url", ""),
            candidate.get("source_record_id", ""),
        )
    ).lower()
    if "/label/" in text or text.endswith("_label.jpg") or "labelimage" in text:
        return "metadata indicates a label image rather than a specimen image"
    if any(token in text for token in ("illustration", "drawing", "living", "observation", "field_photo")):
        return "metadata indicates a non-specimen image"
    return ""


def draw_placeholder(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str) -> None:
    draw.rectangle(box, fill=(242, 244, 242), outline=(196, 202, 196), width=2)
    font = load_font(22)
    lines = textwrap.wrap(text, width=24) or [text]
    total_height = sum(text_size(draw, line, font)[1] + 6 for line in lines)
    y = box[1] + ((box[3] - box[1]) - total_height) // 2
    for line in lines:
        width, height = text_size(draw, line, font)
        draw.text((box[0] + ((box[2] - box[0]) - width) // 2, y), line, font=font, fill=(100, 105, 100))
        y += height + 6


def image_headers(url: str) -> dict[str, str]:
    return {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}


def fallback_image_candidate(entry: SpeciesEntry) -> dict[str, str]:
    return {
        "source": entry.candidate_image_source,
        "source_record_id": entry.candidate_image_source_record_id,
        "taxon": entry.canonical_species,
        "basis_of_record": entry.candidate_image_basis_of_record,
        "image_url": entry.candidate_image_url,
        "alternate_url": entry.candidate_image_alternate_url,
        "license": entry.candidate_image_license,
        "rights_holder": entry.candidate_image_rights_holder,
        "institution_code": entry.candidate_image_institution_code,
        "catalog_number": entry.candidate_image_catalog_number,
        "country": entry.candidate_image_country,
        "raw_country": entry.candidate_image_raw_country,
        "state_province": entry.candidate_image_state_province,
        "county": entry.candidate_image_county,
        "municipality": entry.candidate_image_municipality,
        "decimal_latitude": entry.candidate_image_decimal_latitude,
        "decimal_longitude": entry.candidate_image_decimal_longitude,
        "distance_km": entry.candidate_image_distance_km,
        "geographic_match": entry.candidate_image_geographic_match,
        "administrative_match": entry.candidate_image_administrative_match,
    }


def candidate_distance(candidate: dict[str, str], report: ChecklistReport) -> float | None:
    distance_text = str(candidate.get("distance_km") or "").strip()
    if distance_text:
        try:
            return float(distance_text)
        except ValueError:
            pass
    if report.search_area.latitude is None or report.search_area.longitude is None:
        return None
    try:
        lat = float(candidate.get("decimal_latitude") or "")
        lon = float(candidate.get("decimal_longitude") or "")
    except ValueError:
        return None
    return distance_km(report.search_area.latitude, report.search_area.longitude, lat, lon)


def target_countries(report: ChecklistReport, entry: SpeciesEntry | None = None) -> list[str]:
    target_countries = []
    if report.search_area.country:
        target_countries.append(report.search_area.country)
    return [country for country in target_countries if str(country).strip()]


def candidate_country_matches(candidate: dict[str, str], report: ChecklistReport, entry: SpeciesEntry | None = None) -> bool:
    countries = target_countries(report, entry)
    if not countries:
        return False
    country = str(candidate.get("country") or "").strip()
    if not country:
        return False
    candidate_code = canonical_country_code(country, country) or canonical_country_code(str(candidate.get("raw_country") or ""))
    if candidate_code:
        return any(candidate_code == (canonical_country_code(target, target) or country_code_for(target)) for target in countries)
    return any(country_code_for(country) == country_code_for(target) for target in countries)


def candidate_country_is_comparable(report: ChecklistReport, entry: SpeciesEntry | None = None) -> bool:
    return bool(target_countries(report, entry))


def candidate_has_country(candidate: dict[str, str]) -> bool:
    return bool(str(candidate.get("country") or "").strip())


def normalized_area_label(value: str) -> str:
    text = value.casefold()
    for token in ("prefecture", "province", "state", "pref."):
        text = text.replace(token, " ")
    return "".join(ch for ch in text if ch.isalnum())


def candidate_admin_matches(
    candidate: dict[str, str],
    candidate_key: str,
    report_values: list[str],
) -> bool:
    candidate_value = normalized_area_label(str(candidate.get(candidate_key) or ""))
    if not candidate_value:
        return False
    targets = {normalized_area_label(value) for value in report_values}
    targets = {value for value in targets if value}
    return candidate_value in targets


def candidate_administrative_match(candidate: dict[str, str], entry: SpeciesEntry, report: ChecklistReport) -> str:
    if candidate_admin_matches(
        candidate,
        "state_province",
        [report.search_area.state_or_prefecture],
    ):
        return "stateProvince"
    if candidate_country_matches(candidate, report, entry):
        return "country"
    if not candidate_has_country(candidate) or not candidate_country_is_comparable(report, entry):
        return "insufficient"
    return "distant_or_mismatch"


def candidate_geographic_rank(
    candidate: dict[str, str],
    entry: SpeciesEntry,
    report: ChecklistReport,
) -> tuple[int, float, str]:
    distance = candidate_distance(candidate, report)
    admin_match = candidate_administrative_match(candidate, entry, report)
    candidate["administrative_match"] = "" if admin_match in {"insufficient", "distant_or_mismatch"} else admin_match
    if distance is not None:
        candidate["distance_km"] = f"{distance:.3f}"
        candidate["geographic_match"] = "coordinate"
        if distance <= 25:
            return (0, distance, admin_match)
        if admin_match == "municipality":
            return (1, distance, admin_match)
        if admin_match == "county":
            return (2, distance, admin_match)
        if distance <= 100:
            return (3, distance, admin_match)
        if admin_match == "stateProvince":
            return (4, distance, admin_match)
        if distance <= 500:
            return (5, distance, admin_match)
        if admin_match == "country":
            return (6, distance, admin_match)
        return (7, distance, admin_match)
    candidate["geographic_match"] = admin_match
    if admin_match == "municipality":
        return (1, 0.0, admin_match)
    if admin_match == "county":
        return (2, 0.0, admin_match)
    if admin_match == "stateProvince":
        return (4, 0.0, admin_match)
    if admin_match == "country":
        return (6, 0.0, admin_match)
    if admin_match == "insufficient":
        return (8, 0.0, admin_match)
    return (9, 0.0, admin_match)


def image_candidate_dicts(
    entry: SpeciesEntry,
    report: ChecklistReport,
    max_candidates: int = MAX_REPRESENTATIVE_IMAGE_CANDIDATES,
) -> list[dict[str, str]]:
    candidates = [candidate for candidate in entry.image_candidates if candidate.get("image_url")]
    if candidates:
        source_priority = {"gbif": 0, "cvh": 1}
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                *candidate_geographic_rank(candidate, entry, report),
                source_priority.get(candidate.get("source", "").lower(), 9),
                candidate.get("source_record_id", ""),
                candidate.get("image_url", ""),
            ),
        )
        return ordered[:max_candidates] if max_candidates > 0 else ordered
    candidate = fallback_image_candidate(entry)
    return [candidate] if candidate.get("image_url") else []


def candidate_identity(candidate: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        candidate.get("source", "").lower(),
        candidate.get("source_record_id", ""),
        candidate.get("image_url", ""),
        candidate.get("alternate_url", ""),
        candidate.get("taxon", ""),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_codex_image_selector():
    selector_spec = os.environ.get(
        "VASCULUM_CODEX_SELECTOR",
        "vasculum_checklist.codex_vision:select_representative_image",
    )
    module_name, separator, function_name = selector_spec.partition(":")
    if not separator or not module_name or not function_name:
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    selector = getattr(module, function_name, None)
    return selector if callable(selector) else None


def require_codex_image_selector(species_count: int) -> tuple[object, str, int]:
    selector = load_codex_image_selector()
    if not callable(selector):
        raise RuntimeError(
            "Codex specimen image selector is not available. "
            "Provide the existing VASCULUM selector as "
            "vasculum_checklist.codex_vision.select_representative_image or set VASCULUM_CODEX_SELECTOR=module:function."
        )
    if not sys.stdin.isatty():
        raise RuntimeError("Codex specimen image selection requires an interactive terminal confirmation.")
    module = importlib.import_module(selector.__module__)
    preflight = getattr(module, "preflight", None)
    if callable(preflight):
        preflight()
    mode, initial_batch_size = adaptive_image_evaluation_mode(species_count)
    print(f"Species found: {species_count}")
    print(f"Adaptive image evaluation mode: {mode}")
    print(f"Initial candidate images per species: {initial_batch_size}")
    print(f"Maximum candidate pool per species: {MAX_REPRESENTATIVE_IMAGE_CANDIDATES}")
    print(f"Estimated maximum initial Codex image evaluations: {species_count * initial_batch_size}")
    print()
    answer = input("Continue? [Y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        raise RuntimeError("Codex specimen image selection was cancelled by the user.")
    return selector, mode, initial_batch_size


def codex_assess_candidates(
    entry: SpeciesEntry,
    report: ChecklistReport,
    candidates: list[DownloadedImageCandidate],
    selector,
) -> tuple[DownloadedImageCandidate | None, set[str], set[str], bool]:
    if not candidates:
        return None, set(), set(), False
    assessment = selector(
        species=entry,
        search_area=report.search_area,
        candidates=[
            {
                **item.candidate,
                "local_path": str(item.path),
                "sha256": item.sha256,
                "downloaded_url": item.url,
            }
            for item in candidates
        ],
        instruction=(
            "First exclude living plant photographs, observation photographs, illustrations, "
            "label-only images, placeholders, and images not showing a herbarium specimen. "
            "Then choose the single most diagnostic preserved specimen image for the target species. "
            "Do not choose images representing another taxon. If at least one valid herbarium "
            "specimen image remains, select the best available valid specimen even when it is "
            "not diagnostically ideal. For every candidate, return valid_specimen and "
            "rejection_reason. Return diagnostic_sufficiency sufficient only when the selected "
            "specimen is useful enough to stop; if not, return insufficient so later candidates "
            "can be evaluated. Return rejected candidate record IDs or URLs when any candidate "
            "is unsuitable."
        ),
    )
    rejected_record_ids: set[str] = set()
    rejected_urls: set[str] = set()
    valid_indices: set[int] = set()
    assessment_indices_seen = False
    selected_record_id = ""
    selected_url = ""
    selected_sha256 = ""
    selected_index: int | None = None
    valid_specimen_count: int | None = None
    diagnostic_sufficiency = "sufficient"
    if isinstance(assessment, dict):
        rejected_record_ids = {
            str(value)
            for value in assessment.get("rejected_source_record_ids", [])
            if str(value)
        }
        rejected_urls = {
            str(value)
            for value in assessment.get("rejected_image_urls", [])
            if str(value)
        }
        selected_record_id = str(
            assessment.get("selected_source_record_id")
            or assessment.get("source_record_id")
            or ""
        )
        selected_url = str(
            assessment.get("selected_image_url")
            or assessment.get("image_url")
            or ""
        )
        selected_sha256 = str(assessment.get("selected_sha256") or assessment.get("sha256") or "")
        try:
            valid_specimen_count = int(assessment.get("valid_specimen_count"))
        except (TypeError, ValueError):
            valid_specimen_count = None
        raw_sufficiency = str(assessment.get("diagnostic_sufficiency") or "").strip().lower()
        if raw_sufficiency in {"sufficient", "insufficient"}:
            diagnostic_sufficiency = raw_sufficiency
        try:
            selected_index = int(assessment.get("selected_index")) if "selected_index" in assessment else None
        except (TypeError, ValueError):
            selected_index = None
        for raw_assessment in assessment.get("candidate_assessments", []):
            if not isinstance(raw_assessment, dict):
                continue
            try:
                candidate_index = int(raw_assessment.get("index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= candidate_index < len(candidates):
                continue
            assessment_indices_seen = True
            valid_specimen = bool(raw_assessment.get("valid_specimen"))
            reason = str(raw_assessment.get("rejection_reason") or "").strip()
            candidate = candidates[candidate_index].candidate
            candidate["codex_valid_specimen"] = "true" if valid_specimen else "false"
            candidate["codex_rejection_reason"] = reason
            if valid_specimen:
                valid_indices.add(candidate_index)
            else:
                record_id = candidate.get("source_record_id", "")
                if record_id:
                    rejected_record_ids.add(record_id)
                rejected_urls.add(candidates[candidate_index].url)
                image_url = candidate.get("image_url", "")
                alternate_url = candidate.get("alternate_url", "")
                if image_url:
                    rejected_urls.add(image_url)
                if alternate_url:
                    rejected_urls.add(alternate_url)
    if selected_index is not None and 0 <= selected_index < len(candidates):
        if assessment_indices_seen and selected_index not in valid_indices:
            if valid_indices:
                for candidate_index in sorted(valid_indices):
                    return (
                        candidates[candidate_index],
                        rejected_record_ids,
                        rejected_urls,
                        diagnostic_sufficiency == "sufficient",
                    )
            return None, rejected_record_ids, rejected_urls, False
        return candidates[selected_index], rejected_record_ids, rejected_urls, diagnostic_sufficiency == "sufficient"
    for index, item in enumerate(candidates):
        if assessment_indices_seen and index not in valid_indices:
            continue
        if selected_record_id and selected_record_id == item.candidate.get("source_record_id", ""):
            return item, rejected_record_ids, rejected_urls, diagnostic_sufficiency == "sufficient"
        if selected_sha256 and selected_sha256 == item.sha256:
            return item, rejected_record_ids, rejected_urls, diagnostic_sufficiency == "sufficient"
        if selected_url and selected_url in {
            item.candidate.get("image_url", ""),
            item.candidate.get("alternate_url", ""),
            item.url,
        }:
            return item, rejected_record_ids, rejected_urls, diagnostic_sufficiency == "sufficient"
    if assessment_indices_seen and valid_indices:
        for candidate_index in sorted(valid_indices):
            return (
                candidates[candidate_index],
                rejected_record_ids,
                rejected_urls,
                diagnostic_sufficiency == "sufficient",
            )
    return None, rejected_record_ids, rejected_urls, False


def evaluation_image_urls(candidate: dict[str, str]) -> list[str]:
    return [
        url
        for url in dict.fromkeys([candidate.get("image_url", ""), candidate.get("alternate_url", "")])
        if url
    ]


def highest_resolution_image_urls(candidate: dict[str, str]) -> list[str]:
    return [
        url
        for url in dict.fromkeys([candidate.get("alternate_url", ""), candidate.get("image_url", "")])
        if url
    ]


def gbif_cache_derivative_url(url: str, max_edge: int = FINAL_PDF_MAX_IMAGE_EDGE) -> str:
    if "/image/cache/" not in url:
        return ""
    return re.sub(r"(/image/cache/)\d+x/", rf"\g<1>{max_edge}x/", url)


def final_pdf_image_urls(candidate: dict[str, str]) -> list[str]:
    image_url = candidate.get("image_url", "")
    alternate_url = candidate.get("alternate_url", "")
    preferred_derivative = gbif_cache_derivative_url(image_url)
    return [
        url
        for url in dict.fromkeys([preferred_derivative, alternate_url, image_url])
        if url
    ]


def download_representative_images(
    client: PoliteHttpClient,
    report: ChecklistReport,
    *,
    max_images: int = 0,
    cvh_settings: dict | None = None,
    cvh_raw_dir: Path | None = None,
    cvh_image_limit: int = 0,
    selector=None,
    initial_candidate_batch_size: int = MAX_REPRESENTATIVE_IMAGE_CANDIDATES,
) -> RepresentativeImageDownloadResult:
    image_dir = report.output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    result = RepresentativeImageDownloadResult(
        cvh_message="not requested" if not cvh_settings or cvh_image_limit <= 0 else "requested as valid-candidate top-up"
    )
    candidates = [
        entry
        for entry in report.species
        if image_candidate_dicts(entry, report) or (cvh_settings and cvh_image_limit > 0)
    ]
    if max_images > 0:
        candidates = candidates[:max_images]
    if candidates and selector is None:
        selector, _mode, initial_candidate_batch_size = require_codex_image_selector(len(report.species))
    initial_candidate_batch_size = max(
        1,
        min(MAX_REPRESENTATIVE_IMAGE_CANDIDATES, int(initial_candidate_batch_size)),
    )
    for index, entry in enumerate(candidates, start=1):
        progress_bar(index, len(candidates), "species")
        filename = f"{safe_token(entry.canonical_species)}.jpg"
        destination = image_dir / filename
        relative_path = f"images/{filename}"
        entry.local_image_path = ""
        candidate_pool = image_candidate_dicts(entry, report, max_candidates=0)
        downloaded_candidates: list[DownloadedImageCandidate] = []
        seen_hashes: set[str] = set()
        attempted_candidates: set[tuple[str, str, str, str, str]] = set()
        errors: list[str] = []
        cvh_offset = 0
        cvh_exhausted = not cvh_settings or cvh_raw_dir is None or cvh_image_limit <= 0

        def can_fetch_cvh() -> bool:
            return (
                not cvh_exhausted
                and len(downloaded_candidates) < MAX_REPRESENTATIVE_IMAGE_CANDIDATES
                and cvh_offset < cvh_image_limit
            )

        def top_up_from_cvh() -> bool:
            nonlocal candidate_pool, pool_index, cvh_offset, cvh_exhausted
            if not can_fetch_cvh():
                return False
            remaining = MAX_REPRESENTATIVE_IMAGE_CANDIDATES - len(downloaded_candidates)
            fetch_count = min(cvh_image_limit - cvh_offset, remaining)
            if fetch_count <= 0:
                cvh_exhausted = True
                return False
            progress(
                f"CVH valid-candidate top-up {index}/{len(candidates)}: "
                f"{entry.canonical_species} ({remaining} needed)"
            )
            try:
                added, seen_records = add_cvh_image_candidates_for_entry(
                    client=client,
                    entry=entry,
                    raw_dir=cvh_raw_dir,
                    cvh_settings=cvh_settings,
                    max_records=fetch_count,
                    record_offset=cvh_offset,
                    progress_label=(
                        f"CVH valid-candidate top-up {index}/{len(candidates)}: "
                        f"{entry.canonical_species}"
                    ),
                )
            except Exception as exc:
                cvh_exhausted = True
                result.cvh_message = f"partial: CVH image top-up failed for {entry.canonical_species}: {exc}"
                report.warnings.append(result.cvh_message)
                return False
            cvh_offset += seen_records
            result.cvh_records += added
            if added:
                result.cvh_species.add(entry.canonical_species)
                candidate_pool = image_candidate_dicts(entry, report, max_candidates=0)
                pool_index = 0
            if seen_records <= 0 or cvh_offset >= cvh_image_limit:
                cvh_exhausted = True
            return bool(added or (seen_records > 0 and can_fetch_cvh()))

        with tempfile.TemporaryDirectory(prefix="vasculum_checklist_images_") as temporary:
            temporary_root = Path(temporary)
            pool_index = 0
            selected: DownloadedImageCandidate | None = None
            fallback_selected: DownloadedImageCandidate | None = None
            assessed_identities: set[tuple[str, str, str, str, str]] = set()

            def pending_codex_candidates() -> list[DownloadedImageCandidate]:
                return [
                    item
                    for item in downloaded_candidates
                    if candidate_identity(item.candidate) not in assessed_identities
                ]

            def remove_rejected_candidates(rejected_record_ids: set[str], rejected_urls: set[str]) -> bool:
                nonlocal downloaded_candidates, fallback_selected
                previous_candidate_count = len(downloaded_candidates)
                downloaded_candidates = [
                    item
                    for item in downloaded_candidates
                    if item.candidate.get("source_record_id", "") not in rejected_record_ids
                    and item.url not in rejected_urls
                    and item.candidate.get("image_url", "") not in rejected_urls
                    and item.candidate.get("alternate_url", "") not in rejected_urls
                ]
                if fallback_selected is not None and fallback_selected not in downloaded_candidates:
                    fallback_selected = None
                return len(downloaded_candidates) != previous_candidate_count

            while pool_index < len(candidate_pool) or downloaded_candidates or can_fetch_cvh():
                while (
                    len(pending_codex_candidates()) < initial_candidate_batch_size
                    and len(downloaded_candidates) < MAX_REPRESENTATIVE_IMAGE_CANDIDATES
                    and pool_index < len(candidate_pool)
                ):
                    candidate = candidate_pool[pool_index]
                    pool_index += 1
                    identity = candidate_identity(candidate)
                    if identity in attempted_candidates:
                        continue
                    attempted_candidates.add(identity)
                    candidate_number = pool_index
                    label = (
                        f"PDF image download {index}/{len(candidates)} "
                        f"candidate {candidate_number}/{len(candidate_pool)}: {entry.canonical_species}"
                    )
                    progress(label)
                    urls = evaluation_image_urls(candidate)
                    for url in dict.fromkeys(urls):
                        metadata_rejection = candidate_metadata_rejection_reason(candidate, url)
                        if metadata_rejection:
                            report.warnings.append(
                                f"image rejected: {entry.canonical_species}: {metadata_rejection}: {url}"
                            )
                            progress(
                                f"PDF image rejected {index}/{len(candidates)}: "
                                f"{entry.canonical_species}: {metadata_rejection}"
                            )
                            continue
                        candidate_path = temporary_root / f"candidate_{candidate_number:03d}.jpg"
                        try:
                            with Heartbeat(label, announce_immediately=False):
                                download_and_validate_image(
                                    client=client,
                                    url=url,
                                    destination=candidate_path,
                                    minimum_width=120,
                                    minimum_height=120,
                                    minimum_bytes=1024,
                                    headers=image_headers(url),
                                    max_image_dimension=1800,
                                    jpeg_quality=88,
                                    timeout_seconds=30,
                                    retry_count=2,
                                    retry_backoff_seconds=1.0,
                                    status_callback=lambda status, item=label: progress(f"{item}: {status}"),
                                )
                            rejection = specimen_image_rejection_reason(candidate_path, candidate.get("source", ""), url)
                            if rejection:
                                report.warnings.append(
                                    f"image rejected: {entry.canonical_species}: {rejection}: {url}"
                                )
                                progress(
                                    f"PDF image rejected {index}/{len(candidates)}: "
                                    f"{entry.canonical_species}: {rejection}"
                                )
                                continue
                            image_hash = sha256_file(candidate_path)
                            if image_hash in seen_hashes:
                                report.warnings.append(f"image rejected: {entry.canonical_species}: duplicate image: {url}")
                                progress(f"PDF image rejected {index}/{len(candidates)}: {entry.canonical_species}: duplicate")
                                continue
                            seen_hashes.add(image_hash)
                            downloaded_candidates.append(
                                DownloadedImageCandidate(
                                    candidate=dict(candidate),
                                    path=candidate_path,
                                    sha256=image_hash,
                                    url=url,
                                )
                            )
                            break
                        except Exception as exc:
                            errors.append(f"{url}: {exc}")
                if (
                    len(pending_codex_candidates()) < initial_candidate_batch_size
                    and len(downloaded_candidates) < MAX_REPRESENTATIVE_IMAGE_CANDIDATES
                    and can_fetch_cvh()
                ):
                    if top_up_from_cvh():
                        continue
                    if not downloaded_candidates and pool_index >= len(candidate_pool):
                        break
                if not downloaded_candidates:
                    break
                batch_candidates = pending_codex_candidates()[:initial_candidate_batch_size]
                if not batch_candidates:
                    if pool_index >= len(candidate_pool) and not can_fetch_cvh():
                        selected = fallback_selected
                        break
                    continue
                batch_selected, rejected_record_ids, rejected_urls, diagnostic_sufficient = codex_assess_candidates(
                    entry,
                    report,
                    batch_candidates,
                    selector,
                )
                for item in batch_candidates:
                    assessed_identities.add(candidate_identity(item.candidate))
                rejected_removed = remove_rejected_candidates(rejected_record_ids, rejected_urls)
                if batch_selected is not None:
                    if diagnostic_sufficient:
                        selected = batch_selected
                        break
                    if fallback_selected is None:
                        fallback_selected = batch_selected
                if not rejected_removed and pool_index >= len(candidate_pool) and not can_fetch_cvh() and not pending_codex_candidates():
                    selected = fallback_selected
                    break
            if selected is not None:
                entry.apply_image_candidate(selected.candidate)
                final_url = ""
                final_errors: list[str] = []
                for candidate_url in final_pdf_image_urls(selected.candidate):
                    try:
                        final_label = (
                            f"PDF final image download {index}/{len(candidates)}: "
                            f"{entry.canonical_species}"
                        )
                        with Heartbeat(final_label, announce_immediately=False):
                            download_and_validate_image(
                                client=client,
                                url=candidate_url,
                                destination=destination,
                                minimum_width=120,
                                minimum_height=120,
                                minimum_bytes=1024,
                                headers=image_headers(candidate_url),
                                max_image_dimension=FINAL_PDF_MAX_IMAGE_EDGE,
                                jpeg_quality=FINAL_PDF_JPEG_QUALITY,
                                timeout_seconds=45,
                                retry_count=2,
                                retry_backoff_seconds=1.0,
                                status_callback=lambda status, item=final_label: progress(f"{item}: {status}"),
                            )
                        final_url = candidate_url
                        break
                    except Exception as exc:
                        final_errors.append(f"{candidate_url}: {exc}")
                if not final_url:
                    shutil.copyfile(selected.path, destination)
                    final_url = selected.url
                    if final_errors:
                        report.warnings.append(
                            f"image: {entry.canonical_species}: high-resolution download failed; "
                            f"using evaluated image from the same specimen: {' | '.join(final_errors)}"
                        )
                write_image_metadata(
                    destination,
                    entry,
                    selected.candidate,
                    final_url,
                    evaluated_url=selected.url,
                )
                entry.local_image_path = relative_path
                result.downloaded += 1
                progress(f"PDF image selected {index}/{len(candidates)}: {entry.canonical_species}")
                continue
        if not entry.local_image_path:
            entry.local_image_path = ""
            if errors:
                report.warnings.append(f"image: {entry.canonical_species}: {' | '.join(errors)}")
            progress(f"PDF image unavailable {index}/{len(candidates)}: {entry.canonical_species}")
    if cvh_settings and cvh_image_limit > 0 and not result.cvh_message.startswith("partial:"):
        result.cvh_message = "complete"
    return result


def cover_page(report: ChecklistReport) -> Image.Image:
    page, draw = new_page()
    title_font = load_font(64)
    subtitle_font = load_font(32)
    body_font = load_font(26)
    small_font = load_font(22)
    draw.rectangle((0, 0, A4_WIDTH, 260), fill=(54, 113, 129))
    y = 360
    y = draw_wrapped(draw, (MARGIN, y), report.title, title_font, (35, 45, 42), A4_WIDTH - MARGIN * 2, 14, 4)
    y += 42
    draw.text((MARGIN, y), "CheckList", font=subtitle_font, fill=(54, 113, 129))
    y += 70
    draw.text((MARGIN, y), f"Search area: {report.search_area.label()}", font=body_font, fill=(45, 50, 48))
    y += 44
    draw.text((MARGIN, y), f"Taxonomic scope: {report.taxon_filter.label()}", font=body_font, fill=(45, 50, 48))
    y += 44
    draw.text((MARGIN, y), f"Accepted species: {len(report.species)}", font=body_font, fill=(45, 50, 48))
    y += 44
    date_text = (report.finished_at or report.started_at).date().isoformat()
    draw.text((MARGIN, y), f"Publication date: {date_text}", font=body_font, fill=(45, 50, 48))
    draw.text((MARGIN, A4_HEIGHT - 180), GENERATED_WITH, font=small_font, fill=(80, 90, 86))
    return page


def summary_page(report: ChecklistReport, page_number: int) -> Image.Image:
    page, draw = new_page()
    title_font = load_font(42)
    body_font = load_font(23)
    count_font = load_font(21)
    small_font = load_font(20)
    draw.text((MARGIN, MARGIN), "Taxonomic Species Counts", font=title_font, fill=(35, 45, 42))
    draw.text(
        (MARGIN, MARGIN + 74),
        f"Total accepted species: {len(report.species)}",
        font=body_font,
        fill=(45, 50, 48),
    )

    families = families_in_species_order(report.species)
    family_counts: dict[str, int] = {}
    for entry in report.species:
        family = entry.family or "(family unknown)"
        family_counts[family] = family_counts.get(family, 0) + 1

    columns = 2 if len(families) > 18 else 1
    usable_width = A4_WIDTH - MARGIN * 2
    column_gap = 46
    column_width = (usable_width - column_gap) // columns
    row_height = 45 if len(families) <= 30 else 39
    rows_per_column = math.ceil(len(families) / columns) if families else 1
    top = MARGIN + 148
    for index, family in enumerate(families):
        column = index // rows_per_column
        row = index % rows_per_column
        x = MARGIN + column * (column_width + column_gap)
        y = top + row * row_height
        color = family_color(family, families)
        draw.rectangle((x, y + 6, x + 22, y + 28), fill=color)
        count_text = str(family_counts[family])
        count_width, _ = text_size(draw, count_text, count_font)
        label_font = fitted_font(draw, family, column_width - 72, start_size=23, min_size=15)
        draw.text((x + 34, y), family, font=label_font, fill=(45, 50, 48))
        draw.text((x + column_width - count_width, y + 1), count_text, font=count_font, fill=(45, 50, 48))
    draw_page_number(draw, page_number, small_font)
    return page


def coordinate_bounds(report: ChecklistReport) -> tuple[float, float, float, float] | None:
    if (
        report.search_area.latitude is None
        or report.search_area.longitude is None
        or report.search_area.radius_km is None
    ):
        return None
    center_lat = report.search_area.latitude
    center_lon = report.search_area.longitude
    radius = report.search_area.radius_km
    lat_delta = radius / 111.32
    lon_delta = radius / (111.32 * max(0.2, math.cos(math.radians(center_lat))))
    padding = 1.25
    return (
        center_lat - lat_delta * padding,
        center_lon - lon_delta * padding,
        center_lat + lat_delta * padding,
        center_lon + lon_delta * padding,
    )


def map_xy(
    latitude: float,
    longitude: float,
    bounds: tuple[float, float, float, float],
    box: tuple[int, int, int, int],
) -> tuple[int, int]:
    min_lat, min_lon, max_lat, max_lon = bounds
    x = box[0] + int((longitude - min_lon) / max(max_lon - min_lon, 0.000001) * (box[2] - box[0]))
    y = box[3] - int((latitude - min_lat) / max(max_lat - min_lat, 0.000001) * (box[3] - box[1]))
    return x, y


def meters_per_pixel(latitude: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(latitude)) / (2**zoom)


def zoom_for_square(latitude: float, square_km: float, output_pixels: int) -> int:
    target_mpp = max(1.0, square_km * 1000 / max(1, output_pixels))
    numerator = 156543.03392 * max(0.2, math.cos(math.radians(latitude)))
    zoom = math.ceil(math.log2(numerator / target_mpp))
    return max(0, min(17, int(zoom)))


def latlon_to_world_pixels(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    latitude = max(-MAX_WEB_MERCATOR_LATITUDE, min(MAX_WEB_MERCATOR_LATITUDE, latitude))
    scale = TILE_SIZE * (2**zoom)
    x = (longitude + 180.0) / 360.0 * scale
    sin_lat = math.sin(math.radians(latitude))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * scale
    return x, y


def fetch_osm_tile(
    client: PoliteHttpClient,
    cache_dir: Path,
    zoom: int,
    tile_x: int,
    tile_y: int,
) -> Image.Image | None:
    max_tile = 2**zoom
    if tile_y < 0 or tile_y >= max_tile:
        return None
    wrapped_x = tile_x % max_tile
    cache_path = cache_dir / str(zoom) / f"{wrapped_x}_{tile_y}.png"
    if cache_path.exists():
        try:
            with Image.open(cache_path) as image:
                image.load()
                return image.convert("RGB")
        except (OSError, UnidentifiedImageError):
            pass
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    url = OSM_TILE_URL.format(zoom=zoom, x=wrapped_x, y=tile_y)
    data = client.get_bytes(
        url,
        headers={"Accept": "image/png,image/*;q=0.8"},
        timeout_seconds=20,
        retry_count=2,
        retry_backoff_seconds=1.0,
    )
    cache_path.write_bytes(data)
    with Image.open(BytesIO(data)) as image:
        image.load()
        return image.convert("RGB")


def render_osm_square_map(
    client: PoliteHttpClient | None,
    report: ChecklistReport,
    square_km: float,
    output_pixels: int,
) -> tuple[Image.Image, dict[str, float]] | None:
    if (
        client is None
        or report.search_area.latitude is None
        or report.search_area.longitude is None
    ):
        return None
    center_lat = report.search_area.latitude
    center_lon = report.search_area.longitude
    zoom = zoom_for_square(center_lat, square_km, output_pixels)
    source_side = max(256, int(math.ceil(square_km * 1000 / meters_per_pixel(center_lat, zoom))))
    center_x, center_y = latlon_to_world_pixels(center_lat, center_lon, zoom)
    left = center_x - source_side / 2
    top = center_y - source_side / 2
    right = left + source_side
    bottom = top + source_side
    tile_left = math.floor(left / TILE_SIZE)
    tile_top = math.floor(top / TILE_SIZE)
    tile_right = math.floor((right - 1) / TILE_SIZE)
    tile_bottom = math.floor((bottom - 1) / TILE_SIZE)
    tile_width = tile_right - tile_left + 1
    tile_height = tile_bottom - tile_top + 1
    canvas = Image.new("RGB", (tile_width * TILE_SIZE, tile_height * TILE_SIZE), (235, 238, 235))
    cache_dir = report.output_dir / "map_tiles"
    try:
        for tile_x in range(tile_left, tile_right + 1):
            for tile_y in range(tile_top, tile_bottom + 1):
                tile = fetch_osm_tile(client, cache_dir, zoom, tile_x, tile_y)
                if tile is None:
                    continue
                paste_x = (tile_x - tile_left) * TILE_SIZE
                paste_y = (tile_y - tile_top) * TILE_SIZE
                canvas.paste(tile, (paste_x, paste_y))
    except Exception as exc:
        progress(f"PDF map tile fallback: {exc}")
        return None
    crop = (
        int(round(left - tile_left * TILE_SIZE)),
        int(round(top - tile_top * TILE_SIZE)),
        int(round(right - tile_left * TILE_SIZE)),
        int(round(bottom - tile_top * TILE_SIZE)),
    )
    image = canvas.crop(crop).resize((output_pixels, output_pixels), Image.Resampling.LANCZOS)
    transform = {
        "zoom": float(zoom),
        "left": left,
        "top": top,
        "source_side": float(source_side),
        "output_pixels": float(output_pixels),
    }
    return image, transform


def map_point(
    latitude: float,
    longitude: float,
    transform: dict[str, float],
    box: tuple[int, int, int, int],
) -> tuple[int, int]:
    x, y = latlon_to_world_pixels(latitude, longitude, int(transform["zoom"]))
    scale = transform["output_pixels"] / transform["source_side"]
    return (
        box[0] + int(round((x - transform["left"]) * scale)),
        box[1] + int(round((y - transform["top"]) * scale)),
    )


def draw_map_fallback(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    report: ChecklistReport,
    square_km: float,
) -> dict[str, float] | None:
    draw.rectangle(box, fill=(244, 247, 245), outline=(185, 194, 188), width=2)
    for fraction in (0.25, 0.5, 0.75):
        x = box[0] + int((box[2] - box[0]) * fraction)
        y = box[1] + int((box[3] - box[1]) * fraction)
        draw.line((x, box[1], x, box[3]), fill=(222, 228, 224), width=1)
        draw.line((box[0], y, box[2], y), fill=(222, 228, 224), width=1)
    if report.search_area.latitude is None or report.search_area.longitude is None:
        return None
    center_lat = report.search_area.latitude
    center_lon = report.search_area.longitude
    zoom = zoom_for_square(center_lat, square_km, box[2] - box[0])
    source_side = max(256, int(math.ceil(square_km * 1000 / meters_per_pixel(center_lat, zoom))))
    center_x, center_y = latlon_to_world_pixels(center_lat, center_lon, zoom)
    return {
        "zoom": float(zoom),
        "left": center_x - source_side / 2,
        "top": center_y - source_side / 2,
        "source_side": float(source_side),
        "output_pixels": float(box[2] - box[0]),
    }


def draw_scale_bar(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    square_km: float,
    label_km: float,
    font: ImageFont.ImageFont,
) -> None:
    bar_width = int((label_km / square_km) * (box[2] - box[0]))
    bar_width = max(24, min(bar_width, box[2] - box[0] - 70))
    x = box[0] + 24
    y = box[3] - 34
    draw.rectangle((x - 8, y - 16, x + bar_width + 58, y + 22), fill=(255, 255, 255), outline=(215, 215, 215))
    draw.line((x, y, x + bar_width, y), fill=(35, 45, 42), width=5)
    draw.line((x, y - 8, x, y + 8), fill=(35, 45, 42), width=3)
    draw.line((x + bar_width, y - 8, x + bar_width, y + 8), fill=(35, 45, 42), width=3)
    draw.text((x + bar_width + 10, y - 12), f"{label_km:g} km", font=font, fill=(35, 45, 42))


def draw_map_panel(
    page: Image.Image,
    draw: ImageDraw.ImageDraw,
    report: ChecklistReport,
    client: PoliteHttpClient | None,
    box: tuple[int, int, int, int],
    title: str,
    square_km: float,
    scale_km: float,
    label_x: int,
    label_y: int,
) -> None:
    panel_title_font = load_font(27)
    body_font = load_font(20)
    small_font = load_font(15)
    draw.text((box[0], label_y), title, font=panel_title_font, fill=(35, 45, 42))
    rendered = render_osm_square_map(client, report, square_km, box[2] - box[0])
    if rendered is None:
        transform = draw_map_fallback(draw, box, report, square_km)
    else:
        tile_map, transform = rendered
        page.paste(tile_map, box[:2])
        draw.rectangle(box, outline=(120, 130, 125), width=2)
    if transform is None:
        draw_placeholder(draw, box, "No coordinate-radius search area")
        return

    plotted = 0
    for record in report.evidence_records:
        try:
            lat = float(record.decimal_latitude)
            lon = float(record.decimal_longitude)
        except ValueError:
            continue
        x, y = map_point(lat, lon, transform, box)
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(46, 105, 82))
            plotted += 1

    center_lat = report.search_area.latitude or 0.0
    center_lon = report.search_area.longitude or 0.0
    center_x, center_y = map_point(center_lat, center_lon, transform, box)
    radius = report.search_area.radius_km or 0.0
    radius_pixels = max(5, int(round((radius / square_km) * (box[2] - box[0]))))
    draw.ellipse(
        (center_x - radius_pixels, center_y - radius_pixels, center_x + radius_pixels, center_y + radius_pixels),
        outline=(54, 113, 129),
        width=5,
    )
    draw.ellipse((center_x - 8, center_y - 8, center_x + 8, center_y + 8), fill=(180, 65, 70))
    draw.line((center_x - 16, center_y, center_x + 16, center_y), fill=(180, 65, 70), width=3)
    draw.line((center_x, center_y - 16, center_x, center_y + 16), fill=(180, 65, 70), width=3)
    draw_scale_bar(draw, box, square_km, scale_km, small_font)

    draw.text((label_x, label_y + 50), f"{square_km:g} km square", font=body_font, fill=(45, 50, 48))
    draw.text((label_x, label_y + 84), f"Center: {center_lat:.6f}, {center_lon:.6f}", font=small_font, fill=(45, 50, 48))
    draw.text((label_x, label_y + 112), f"Search radius: {radius:g} km", font=small_font, fill=(45, 50, 48))
    draw.text((label_x, label_y + 140), f"Plotted records: {plotted}", font=small_font, fill=(45, 50, 48))


def map_page(report: ChecklistReport, page_number: int, client: PoliteHttpClient | None = None) -> Image.Image:
    page, draw = new_page()
    title_font = load_font(42)
    small_font = load_font(18)
    draw.text((MARGIN, MARGIN), "Search Area Map", font=title_font, fill=(35, 45, 42))
    map_side = 700
    label_x = MARGIN + map_side + 38
    draw_map_panel(
        page,
        draw,
        report,
        client,
        (MARGIN, 150, MARGIN + map_side, 150 + map_side),
        "Local View",
        10,
        2,
        label_x,
        112,
    )
    draw_map_panel(
        page,
        draw,
        report,
        client,
        (MARGIN, 910, MARGIN + map_side, 910 + map_side),
        "Regional View",
        3000,
        500,
        label_x,
        872,
    )
    draw.text((MARGIN, A4_HEIGHT - 118), "Map tiles: OpenStreetMap contributors.", font=small_font, fill=(80, 90, 86))
    draw_page_number(draw, page_number, small_font)
    return page


def draw_species_card(
    page: Image.Image,
    draw: ImageDraw.ImageDraw,
    entry: SpeciesEntry,
    output_dir: Path,
    x: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    title_font = load_font(18)
    card = (x, y, x + CARD_WIDTH, y + CARD_HEIGHT)
    draw.rectangle(card, fill=(255, 255, 255), outline=(210, 214, 210), width=1)
    image_box = (x + 5, y + 5, x + CARD_WIDTH - 5, y + CARD_HEIGHT - 52)
    image_bbox: tuple[int, int, int, int] | None = None
    if entry.local_image_path:
        image_path = Path(entry.local_image_path)
        if not image_path.is_absolute():
            image_path = output_dir / image_path
        image_bbox = paste_contained(page, image_path, image_box)
    if image_bbox is None:
        draw_placeholder(draw, image_box, "No representative image available")
    else:
        voucher = entry.image_voucher()
        if voucher:
            voucher_font = load_font(12)
            voucher_width, voucher_height = text_size(draw, voucher, voucher_font)
            vx2 = image_bbox[2] - 5
            vy2 = image_bbox[3] - 5
            vx1 = vx2 - voucher_width - 12
            vy1 = vy2 - voucher_height - 8
            draw.rectangle((vx1, vy1, vx2, vy2), fill=(255, 255, 255), outline=(215, 215, 215), width=1)
            draw.text((vx1 + 6, vy1 + 3), voucher, font=voucher_font, fill=(45, 50, 48))
    draw_wrapped(
        draw,
        (x + 6, y + CARD_HEIGHT - 42),
        entry.full_scientific_name or entry.canonical_species,
        title_font,
        (30, 40, 38),
        CARD_WIDTH - 12,
        4,
        2,
    )


def checklist_pages(report: ChecklistReport, first_page_number: int) -> list[Image.Image]:
    pages: list[Image.Image] = []
    families = families_in_species_order(report.species)
    species_by_family: dict[str, list[SpeciesEntry]] = {}
    for entry in report.species:
        species_by_family.setdefault(entry.family or "(family unknown)", []).append(entry)

    page_number = first_page_number
    small_font = load_font(20)
    grid_left = (A4_WIDTH - (CARD_WIDTH * 2 + CARD_GAP)) // 2
    total_checklist_pages = sum(math.ceil(len(species_by_family[family]) / 4) for family in families)
    checklist_page_index = 0
    for family in families:
        entries = species_by_family[family]
        color = family_color(family, families)
        for chunk_start in range(0, len(entries), 4):
            page, draw = new_page()
            header_font = fitted_font(
                draw,
                family,
                A4_WIDTH - MARGIN * 2,
                start_size=32,
                min_size=20,
            )
            draw.rectangle((0, 0, A4_WIDTH, 64), fill=color)
            draw_wrapped(draw, (MARGIN, 16), family, header_font, (255, 255, 255), A4_WIDTH - MARGIN * 2, 4, 1)
            chunk = entries[chunk_start : chunk_start + 4]
            positions = [
                (grid_left, GRID_TOP),
                (grid_left + CARD_WIDTH + CARD_GAP, GRID_TOP),
                (grid_left, GRID_TOP + CARD_HEIGHT + CARD_GAP),
                (grid_left + CARD_WIDTH + CARD_GAP, GRID_TOP + CARD_HEIGHT + CARD_GAP),
            ]
            for entry, (x, y) in zip(chunk, positions):
                draw_species_card(page, draw, entry, report.output_dir, x, y, color)
            draw_page_number(draw, page_number, small_font)
            checklist_page_index += 1
            progress_bar(checklist_page_index, total_checklist_pages, "checklist pages")
            pages.append(page)
            page_number += 1
    return pages


def index_pages(
    report: ChecklistReport,
    first_page_number: int,
    species_page_numbers: dict[str, int],
) -> list[Image.Image]:
    pages: list[Image.Image] = []
    title_font = load_font(40)
    header_font = load_font(18)
    body_font = load_font(19)
    small_font = load_font(20)
    rows = sorted(report.species, key=lambda entry: entry.canonical_species.lower())
    per_page = 45
    total_pages = max(1, math.ceil(len(rows) / per_page))
    page_number = first_page_number
    for page_index in range(total_pages):
        page, draw = new_page()
        draw.text((MARGIN, MARGIN), "Scientific Name Index", font=title_font, fill=(35, 45, 42))
        y = MARGIN + 78
        draw.text((MARGIN, y), "Scientific name", font=header_font, fill=(90, 96, 92))
        page_label = "Page"
        label_width, _ = text_size(draw, page_label, header_font)
        draw.text((A4_WIDTH - MARGIN - label_width, y), page_label, font=header_font, fill=(90, 96, 92))
        y += 34
        for entry in rows[page_index * per_page : (page_index + 1) * per_page]:
            name = entry.full_scientific_name or entry.canonical_species
            visual_page = str(species_page_numbers.get(entry.canonical_species, ""))
            page_width, _ = text_size(draw, visual_page, body_font)
            max_name_width = A4_WIDTH - MARGIN * 2 - 80
            lines = wrap_text(draw, name, body_font, max_name_width)
            line = lines[0] if lines else name
            if len(lines) > 1:
                line = line.rstrip(".") + "..."
            draw.text((MARGIN, y), line, font=body_font, fill=(45, 50, 48))
            draw.text((A4_WIDTH - MARGIN - page_width, y), visual_page, font=body_font, fill=(45, 50, 48))
            y += 31
        draw_page_number(draw, page_number, small_font)
        pages.append(page)
        page_number += 1
    return pages


def provenance_page(report: ChecklistReport, page_number: int) -> Image.Image:
    page, draw = new_page()
    title_font = load_font(40)
    body_font = load_font(22)
    small_font = load_font(20)
    draw.text((MARGIN, MARGIN), "Search / Provenance", font=title_font, fill=(35, 45, 42))
    y = MARGIN + 78
    lines = [
        f"Geographic search area: {report.search_area.label()}",
        f"Taxonomic scope: {report.taxon_filter.label()}",
        f"Publication date: {(report.finished_at or report.started_at).date().isoformat()}",
        f"Data retrieval date: {report.started_at.date().isoformat()}",
        "Data sources: GBIF occurrence API; CVH image candidate top-up when used.",
        f"Software: {GENERATED_WITH}",
        f"VASCULUM version: {report.version}",
        f"CheckList version: {report.checklist_version}",
        "Exact command:",
        report.raw_command,
    ]
    for line in lines:
        y = draw_wrapped(draw, (MARGIN, y), line, body_font, (45, 50, 48), A4_WIDTH - MARGIN * 2, 8)
        y += 10
    draw_page_number(draw, page_number, small_font)
    return page


def write_pdf(report: ChecklistReport, client: PoliteHttpClient | None = None) -> Path:
    output = report.output_dir / "checklist.pdf"
    progress("PDF rendering cover page")
    pages: list[Image.Image] = [cover_page(report)]
    progress("PDF rendering search area map page")
    pages.append(map_page(report, 2, client))
    progress("PDF rendering taxonomic species counts page")
    pages.append(summary_page(report, 3))
    progress("PDF rendering species checklist pages")
    checklist = checklist_pages(report, 4)
    pages.extend(checklist)
    species_page_numbers = checklist_page_numbers(report.species, first_page=4, per_page=4)
    index_start = 4 + len(checklist)
    progress("PDF rendering scientific name index")
    index = index_pages(report, index_start, species_page_numbers)
    pages.extend(index)
    progress("PDF rendering provenance page")
    pages.append(provenance_page(report, index_start + len(index)))
    with Heartbeat(f"PDF writing {output}"):
        pages[0].save(
            output,
            "PDF",
            save_all=True,
            append_images=pages[1:],
            resolution=150.0,
        )
    progress(f"PDF written {output}")
    return output
