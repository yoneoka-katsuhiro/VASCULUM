from __future__ import annotations

from pathlib import Path

from .inputs import (
    associated_media_items, catalog_number, display_image_path, image_name_matches,
    is_miss_image, unmarked_image_name,
    photo_group_code,
)


def mark_misidentification(row, paths, input_dir):
    markers = row.setdefault("_vasculumMissMarkers", [])
    for path in paths:
        value = display_image_path(input_dir, path)
        if value not in markers:
            markers.append(value)
    markers.sort()


def find_miss_markers(input_dir, row, *, known_markers=(), explicit_media=""):
    root = Path(row.get("_vasculumImageRoot") or input_dir)
    fallback = Path(row.get("_vasculumFallbackImageRoot") or root) / "images"
    markers = set()
    media = associated_media_items(row) + associated_media_items({"associatedMedia": explicit_media})
    directories = set()
    references = set()
    for item in media:
        if item.startswith(("http://", "https://")):
            continue
        path = (root / Path(item).expanduser()).resolve()
        directories.add(path.parent)
        references.add(path)
        if is_miss_image(path) and path.is_file():
            markers.add(path)
    available = {Path(path).resolve() for path in known_markers}
    for directory in directories:
        if directory.is_dir():
            available.update(path.resolve() for path in directory.iterdir() if path.is_file() and is_miss_image(path))
    for path in available:
        original = path.with_name(unmarked_image_name(path))
        if original in references or path in references:
            markers.add(path)
            continue
        # A catalog-only match is scoped to its own image directory, not all collections.
        if path.is_relative_to(fallback.resolve()) and image_name_matches(catalog_number(row), original.stem):
            markers.add(path)
    return sorted(markers)


def apply_miss_markers(input_dir, rows, image_roots):
    markers = set()
    for directory in dict.fromkeys(Path(root).resolve() for root in image_roots):
        if directory.is_dir():
            markers.update(path.resolve() for path in directory.rglob("*") if path.is_file() and is_miss_image(path))
    for row in rows:
        found = find_miss_markers(input_dir, row, known_markers=markers)
        if found:
            mark_misidentification(row, found, input_dir)
    for path in markers:
        if any(display_image_path(input_dir, path) in row.get("_vasculumMissMarkers", []) for row in rows):
            continue
        code = photo_group_code(path.with_name(unmarked_image_name(path)))
        matches = [row for row in rows if catalog_number(row).casefold() == code.casefold()]
        if len(matches) == 1:
            mark_misidentification(matches[0], [path], input_dir)
        elif len(matches) > 1:
            raise ValueError(f"miss_ marker {path} matches multiple specimens; disambiguate associatedMedia before curation")
    return markers
