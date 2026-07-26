from __future__ import annotations

import csv
from pathlib import Path


def delimiter_for(path: Path) -> str:
    return "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","


def detect_dwc_path(input_dir: Path, explicit_path=None) -> Path:
    if explicit_path:
        path = explicit_path.expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"DwC input file was not found: {path}")
        return path
    for name in ("dwc.tsv", "dwc.csv"):
        path = input_dir / name
        if path.exists():
            return path
    raise FileNotFoundError(f"No dwc.tsv or dwc.csv was found in: {input_dir}")


def read_table(path: Path):
    delimiter = delimiter_for(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        rows = [
            {key: "" if value is None else value for key, value in row.items()}
            for row in reader
        ]
        return rows, list(reader.fieldnames or [])


def read_label_sidecar(path):
    if not path:
        return {}
    delimiter = delimiter_for(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        result = {}
        for row in reader:
            normalized = {
                key: "" if value is None else value for key, value in row.items()
            }
            catalog = first_present(
                normalized,
                "catalogNumber",
                "catalog_number",
                "specimenCode",
                "specimen_code",
            )
            if catalog and catalog not in result:
                result[catalog] = normalized
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


def find_image_path(input_dir: Path, row) -> str:
    for item in associated_media_items(row):
        if item.startswith(("http://", "https://")):
            continue
        path = (input_dir / item).resolve()
        if path.exists():
            return str(path.relative_to(input_dir))

    code = catalog_number(row)
    image_dir = input_dir / "images"
    if not code or not image_dir.exists():
        return ""
    compact = "".join(char for char in code if char.isalnum()).lower()
    for path in sorted(image_dir.iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        stem = "".join(char for char in path.stem if char.isalnum()).lower()
        if stem.startswith(compact):
            return str(path.relative_to(input_dir))
    return ""
