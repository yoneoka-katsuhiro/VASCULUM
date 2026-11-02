from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .checkpoints import file_hash, fingerprint
from .inputs import associated_media_items, is_miss_image, label_image_paths
from .outputs import atomic_text


def export_final_images(output_dir, source_dir, results, sidecars, cache_dir, *, enabled=False):
    manifest_path = cache_dir / "image_exports" / f"{fingerprint(str(output_dir.resolve()))}.json"
    previous = {}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not enabled and not previous:
        return 0
    old = {item["path"]: item for item in previous.get("images", [])}
    planned, row_paths = {}, {}
    for index, result in enumerate(results):
        if not result.include_in_dwc:
            continue
        row = result.row
        key = fingerprint([row.get("occurrenceID"), row.get("catalogNumber"), row.get("vasculumInputSource")])[:20]
        for image in label_image_paths(source_dir, row, sidecars):
            source = (source_dir / image).resolve()
            if is_miss_image(source):
                continue
            relative = str(Path("images") / key / source.name)
            item = {"path": relative, "sha256": file_hash(source), "source": str(source)}
            if relative in planned and planned[relative]["source"] != item["source"]:
                raise ValueError(f"Images have the same export filename: {relative}. Rename one source image to disambiguate it.")
            planned[relative] = item
            row_paths.setdefault(index, []).append(relative)

    def managed_path(relative):
        path = (output_dir / relative).resolve()
        if not path.is_relative_to((output_dir / "images").resolve()) or path == source_dir:
            raise ValueError("Unsafe managed image path in export manifest")
        return path

    # Never overwrite/delete an unknown or manually edited file in an existing output.
    image_dir = output_dir / "images"
    if image_dir.is_symlink():
        raise ValueError("Output images/ is a symbolic link; choose a fresh --output directory. Source images were not modified.")
    if image_dir.is_dir():
        for path in image_dir.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Symbolic link inside managed output images: {path}. Use a fresh --output directory.")
            if not path.is_file():
                continue
            relative = str(path.relative_to(output_dir))
            expected = old.get(relative) or planned.get(relative)
            if not expected or file_hash(path) != expected["sha256"]:
                raise ValueError(f"Unmanaged or changed output image: {path}. Use a fresh --output directory; source images were not deleted. Apply miss_ to input photographs, not derived output copies.")
    source_paths = {Path(item["source"]).resolve() for item in planned.values()}
    for relative, item in old.items():
        path = managed_path(relative)
        if path in source_paths or path == Path(item["source"]).resolve():
            raise ValueError("Output image export overlaps an input photograph; choose a different --output directory")
        if relative not in planned and path.is_file():
            path.unlink()
    for relative, item in planned.items():
        destination = managed_path(relative)
        source = Path(item["source"])
        if destination == source:
            raise ValueError("Output image path equals its source; choose a separate output directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or file_hash(destination) != item["sha256"]:
            descriptor, temporary = tempfile.mkstemp(prefix=".copy-", dir=destination.parent)
            os.close(descriptor)
            try:
                shutil.copy2(source, temporary)
                if file_hash(temporary) != item["sha256"]:
                    raise ValueError(f"Input photograph changed during export: {source}; rerun after editing finishes")
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    for index, result in enumerate(results):
        exported = row_paths.get(index, [])
        result.provenance["exportedImages"] = [planned[path] for path in exported]
        if exported:
            remote = [item for item in associated_media_items(result.row) if item.startswith(("http://", "https://"))]
            result.row["associatedMedia"] = " | ".join(dict.fromkeys([*exported, *remote]))
    with atomic_text(manifest_path) as handle:
        json.dump({"images": list(planned.values())}, handle, ensure_ascii=False, sort_keys=True)
    return len(planned)
