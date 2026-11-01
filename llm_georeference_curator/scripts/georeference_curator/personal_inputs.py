from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .checkpoints import file_hash, fingerprint
from .inputs import (
    IMAGE_SUFFIXES, associated_media_items, catalog_number, detect_dwc_path,
    display_image_path, find_image_paths, read_table,
    is_miss_image, unmarked_image_name,
    photo_group_code as photo_code,
)
from .manual_review import apply_miss_markers, mark_misidentification


SOURCE_FIELDS = ["vasculumInputType", "vasculumInputSource"]


@dataclass
class InputBundle:
    dwc_path: object = None
    rows: list = field(default_factory=list)
    fieldnames: list = field(default_factory=list)
    files: list = field(default_factory=list)
    personal_dwc_records: int = 0
    photo_only_records: int = 0
    attached_personal_images: int = 0
    warnings: list = field(default_factory=list)


def source_name(input_dir, path):
    return display_image_path(input_dir, path)


def personal_occurrence_id(input_dir, *parts):
    return uuid.uuid5(uuid.NAMESPACE_URL, fingerprint(["VASCULUM-personal", input_dir.name, *parts])).urn


def load_inputs(input_dir, *, input_dwc=None, additional_inputs=(), include_personal=True,
                import_unlinked_images=False, include_images=True):
    bundle = InputBundle()
    sources, image_roots, seen_files = [], [], set()
    base_exists = input_dwc or any((input_dir / name).is_file() for name in ("dwc.tsv", "dwc.csv"))
    if base_exists:
        bundle.dwc_path = detect_dwc_path(input_dir, input_dwc)
        sources.append((bundle.dwc_path, input_dir, "collector_or_edited_dwc"))
    personal = input_dir / "personal"
    directories = [personal] if include_personal and personal.is_dir() else []
    for item in additional_inputs or []:
        path = Path(item).expanduser().resolve()
        if path.is_dir():
            directories.append(path)
        elif path.is_file() and path.suffix.lower() in {".tsv", ".csv", ".tab"}:
            sources.append((path, path.parent, "personal_dwc"))
        else:
            raise ValueError(f"Additional input must be a DwC CSV/TSV file or a directory: {path}")
    for directory in dict.fromkeys(path.resolve() for path in directories):
        if any((directory / name).is_file() for name in ("dwc.tsv", "dwc.csv")):
            sources.append((detect_dwc_path(directory), directory, "personal_dwc"))
        image_roots.append(directory / "images")
    identities = {}
    for path, image_root, origin in sources:
        path = path.resolve()
        if path in seen_files:
            continue
        seen_files.add(path)
        rows, fields = read_table(path)
        name = source_name(input_dir, path)
        bundle.files.append({"path": name, "sha256": file_hash(path), "type": origin})
        bundle.fieldnames.extend(field for field in fields if field not in bundle.fieldnames)
        for line, original in enumerate(rows, 2):
            row = dict(original)
            if origin == "personal_dwc":
                if not row.get("catalogNumber", "").strip():
                    row["catalogNumber"] = "PERSONAL-" + fingerprint([name, original])[:12]
                if not row.get("occurrenceID", "").strip():
                    row["occurrenceID"] = personal_occurrence_id(input_dir, name, row["catalogNumber"])
                bundle.personal_dwc_records += 1
            identity = row.get("occurrenceID", "").strip()
            if identity:
                if identity in identities:
                    raise ValueError(f"Duplicate occurrenceID {identity!r}: {identities[identity]} and {name}:{line}. Edit the original row to update it, or assign a unique occurrenceID to a different specimen.")
                identities[identity] = f"{name}:{line}"
            row["_vasculumImageRoot"] = str(image_root.resolve())
            row["vasculumInputType"] = origin
            row["vasculumInputSource"] = name
            bundle.rows.append(row)

    all_image_roots = [*image_roots, input_dir / "images", *(Path(root) / "images" for _, root, _ in sources)]
    apply_miss_markers(input_dir, bundle.rows, all_image_roots)

    def attach(row, paths, *, source):
        # All relative media in imported tables resolve beside that table, not beside the collector.
        existing = associated_media_items(row)
        root = Path(row.get("_vasculumImageRoot") or input_dir)
        for item in existing:
            if not row.get("_vasculumMissMarkers") and not item.startswith(("http://", "https://")) and not (root / Path(item).expanduser()).is_file():
                bundle.warnings.append(f"Local image not found for {catalog_number(row)} in {row['vasculumInputSource']}: {item}. Available DwC locality text can still be processed.")
        resolved = [item if item.startswith(("http://", "https://")) else display_image_path(input_dir, root / Path(item).expanduser()) for item in existing]
        resolved.extend(display_image_path(input_dir, path) for path in paths)
        row["associatedMedia"] = " | ".join(dict.fromkeys(resolved))
        row.setdefault("_vasculumFallbackImageRoot", str(root))
        row["_vasculumImageRoot"] = str(input_dir)
        if source and source not in row["vasculumInputSource"].split(" | "):
            row["vasculumInputSource"] += " | " + source

    if include_images:
        claimed = set()
        owners = {}
        for index, row in enumerate(bundle.rows):
            paths = [((input_dir / path).resolve()) for path in find_image_paths(input_dir, row)]
            for path in paths:
                if path in owners:
                    raise ValueError(f"Image {path} is linked or name-matched to multiple records ({owners[path]} and {catalog_number(row)}). Use unique associatedMedia links or distinct image names to disambiguate specimens.")
                owners[path] = catalog_number(row) or f"row {index + 1}"
            claimed.update(paths)
            attach(row, paths, source="")
        if import_unlinked_images:
            image_roots.append(input_dir / "images")
        for image_root in dict.fromkeys(path.resolve() for path in image_roots):
            if not image_root.is_dir():
                continue
            groups = {}
            for path in sorted(image_root.rglob("*")):
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and path.resolve() not in claimed:
                    groups.setdefault((path.parent, photo_code(path.with_name(unmarked_image_name(path)))), []).append(path.resolve())
            for (directory, code), paths in groups.items():
                marked_paths = [path for path in paths if is_miss_image(path)]
                normal_paths = [path for path in paths if not is_miss_image(path)]
                matches = [row for row in bundle.rows if row.get("vasculumInputType") != "personal_photo" and catalog_number(row).casefold() == code.casefold()]
                if len(matches) > 1:
                    raise ValueError(f"Personal image group {directory / code} matches multiple catalog numbers. Link images explicitly using associatedMedia; no ambiguous attachment was made.")
                if matches:
                    attach(matches[0], normal_paths, source=source_name(input_dir, directory))
                    if marked_paths:
                        mark_misidentification(matches[0], marked_paths, input_dir)
                    bundle.attached_personal_images += len(normal_paths)
                else:
                    namespace = source_name(input_dir, directory / code)
                    identity = personal_occurrence_id(input_dir, namespace, "photo-only")
                    if identity in identities:
                        raise ValueError(f"Conflicting personal image identity: {namespace}")
                    identities[identity] = namespace
                    row = {"occurrenceID": identity, "catalogNumber": code, "basisOfRecord": "PreservedSpecimen",
                        "vasculumInputType": "personal_photo", "vasculumInputSource": namespace,
                        "_vasculumImageRoot": str(input_dir)}
                    attach(row, normal_paths, source="")
                    if marked_paths:
                        mark_misidentification(row, marked_paths, input_dir)
                    bundle.rows.append(row)
                    bundle.photo_only_records += 1
                claimed.update(paths)
        if not import_unlinked_images and (input_dir / "images").is_dir():
            unlinked = [path for path in (input_dir / "images").rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and not is_miss_image(path) and path.resolve() not in claimed]
            if unlinked:
                bundle.warnings.append(f"{len(unlinked)} unlinked image(s) in collector images/ were not added as specimens. Put personal photographs in personal/images/, add a DwC row, or explicitly use --import-unlinked-images.")
    if not bundle.rows and bundle.dwc_path is None:
        raise FileNotFoundError(f"No DwC records or importable personal images were found in {input_dir}")
    for field in ["occurrenceID", "catalogNumber", "basisOfRecord", "associatedMedia", *SOURCE_FIELDS]:
        if field not in bundle.fieldnames:
            bundle.fieldnames.append(field)
    return bundle
