from __future__ import annotations

import csv
import re
from pathlib import Path


def delimiter_for(path: Path) -> str:
    return "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","


def detect_dwc_path(input_dir: Path, explicit_path=None) -> Path:
    if explicit_path:
        path = explicit_path.expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"DwC input file was not found: {path}")
        return path
    tsv, csv_path = input_dir / "dwc.tsv", input_dir / "dwc.csv"
    if tsv.is_file() and csv_path.is_file():
        tsv_rows, tsv_fields = read_table(tsv)
        csv_rows, csv_fields = read_table(csv_path)
        if set(tsv_fields) != set(csv_fields) or tsv_rows != csv_rows:
            raise ValueError(f"dwc.tsv and dwc.csv differ in {input_dir}. Choose the edited table explicitly with --input-dwc PATH (or keep only one table). No input was overwritten.")
    for name in ("dwc.tsv", "dwc.csv"):
        path = input_dir / name
        if path.exists():
            return path
    raise FileNotFoundError(f"No dwc.tsv or dwc.csv was found in: {input_dir}")


def read_table(path: Path):
    delimiter = delimiter_for(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        fields = [name.strip() for name in (reader.fieldnames or [])]
        if not fields or any(not name for name in fields) or len(set(fields)) != len(fields):
            raise ValueError(f"Table headers must be present, unique and nonempty: {path}")
        reader.fieldnames = fields
        rows = []
        for row in reader:
            if None in row:
                raise ValueError(f"Extra cells at {path}:{reader.line_num}; check the CSV/TSV delimiter and quoting")
            normalized = {key: "" if value is None else value for key, value in row.items()}
            if any(value.strip() for value in normalized.values()):
                rows.append(normalized)
        return rows, fields


def read_label_sidecar(path):
    if not path:
        return {}
    rows, _ = read_table(path)
    result = {}
    for row in rows:
        catalog = first_present(row, "catalogNumber", "catalog_number", "specimenCode", "specimen_code")
        if not catalog:
            raise ValueError(f"Every label sidecar row needs a catalogNumber: {path}")
        if catalog in result:
            raise ValueError(f"Duplicate label sidecar catalogNumber {catalog!r}: {path}. No transcription was selected by guessing.")
        result[catalog] = row
    return result


def first_present(row, *names) -> str:
    for name in names:
        value = row.get(name, "")
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def catalog_number(row) -> str:
    return first_present(row, "catalogNumber", "catalog_number", "occurrenceID", "id")


def associated_media_items(row):
    value = row.get("associatedMedia", "")
    pieces = []
    for part in value.replace(";", "|").split("|"):
        text = part.strip()
        if text:
            pieces.append(text)
    return pieces


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def is_miss_image(path):
    path = Path(path)
    return path.name.casefold().startswith("miss_") and path.suffix.lower() in IMAGE_SUFFIXES


def unmarked_image_name(path):
    name = Path(path).name
    while name.casefold().startswith("miss_"):
        name = name[5:]
    return name


def photo_group_code(path):
    return re.sub(r"__(?:front|back|recto|verso|label(?:\d+)?|image\d+|\d+)$", "", Path(path).stem, flags=re.I)


def image_name_matches(code, stem):
    chars = [re.escape(char) for char in str(code) if char.isalnum()]
    return bool(chars and re.match(r"^" + r"[\W_]*".join(chars) + r"(?=$|[\W_])", stem, re.I))


def display_image_path(input_dir, path):
    path = path.resolve()
    try:
        return str(path.relative_to(input_dir.resolve()))
    except ValueError:
        return str(path)


def find_image_paths(input_dir: Path, row, *, explicit_media=None):
    paths = []
    image_root = Path(row.get("_vasculumImageRoot") or input_dir)
    media = associated_media_items(row) if explicit_media is None else associated_media_items({"associatedMedia": explicit_media})
    for item in media:
        if item.startswith(("http://", "https://")):
            continue
        path = Path(item).expanduser()
        path = (image_root / path).resolve() if not path.is_absolute() else path.resolve()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and not is_miss_image(path):
            paths.append(path)

    # Explicit label-sidecar images are authoritative, including missing-image diagnostics.
    if explicit_media is None and not paths:
        code = catalog_number(row)
        image_dir = Path(row.get("_vasculumFallbackImageRoot") or image_root) / "images"
        if code and image_dir.is_dir():
            for path in sorted(image_dir.rglob("*")):
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and not is_miss_image(path) and image_name_matches(code, path.stem):
                    paths.append(path.resolve())
    return [display_image_path(input_dir, path) for path in dict.fromkeys(paths)]


def find_image_path(input_dir: Path, row) -> str:
    paths = find_image_paths(input_dir, row)
    return paths[0] if paths else ""


def label_image_paths(input_dir, row, sidecars):
    sidecar = sidecars.get(catalog_number(row), {})
    explicit = first_present(sidecar, "imagePath", "image_path")
    return find_image_paths(input_dir, row, explicit_media=explicit or None)
