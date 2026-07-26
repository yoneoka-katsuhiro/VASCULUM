from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ImageQuality:
    path: object = None
    width: str = ""
    height: str = ""
    file_size_bytes: str = ""
    status: str = "image_absent"
    remarks: str = ""


def resolve_image_file(input_dir: Path, image_path: str):
    if not image_path or image_path.startswith(("http://", "https://")):
        return None
    path = Path(image_path).expanduser()
    if path.is_absolute():
        return path
    return (input_dir / path).resolve()


def inspect_image(input_dir: Path, image_path: str) -> ImageQuality:
    path = resolve_image_file(input_dir, image_path)
    if not path:
        return ImageQuality(status="image_absent", remarks="No local image path.")
    if not path.exists():
        return ImageQuality(
            path=path,
            status="image_missing",
            remarks=f"Image path was recorded, but the file was not found: {path}",
        )
    size = path.stat().st_size
    width, height = read_image_dimensions(path)
    quality = ImageQuality(
        path=path,
        width=str(width or ""),
        height=str(height or ""),
        file_size_bytes=str(size),
    )
    if not width or not height:
        quality.status = "image_dimensions_unknown"
        quality.remarks = "Image exists, but dimensions could not be read."
        return quality
    short_side = min(width, height)
    megapixels = (width * height) / 1_000_000
    if short_side < 1200 or megapixels < 2.0:
        quality.status = "review_low_resolution_for_label_reading"
        quality.remarks = (
            "Whole-sheet image may be too low-resolution for reliable small-label "
            "reading; verify label text before accepting coordinates derived from it."
        )
    elif size < 250_000:
        quality.status = "review_compressed_for_label_reading"
        quality.remarks = (
            "Image dimensions are acceptable, but compression may affect small-label "
            "reading; verify label text before accepting coordinates derived from it."
        )
    else:
        quality.status = "image_available"
        quality.remarks = "Image exists; label transcription should still be verified."
    return quality


def read_image_dimensions(path: Path):
    with path.open("rb") as handle:
        header = handle.read(32)
        if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
            width = int.from_bytes(header[16:20], "big")
            height = int.from_bytes(header[20:24], "big")
            return width, height
        handle.seek(0)
        if handle.read(2) != b"\xff\xd8":
            return None, None
        return read_jpeg_dimensions(handle)


def read_jpeg_dimensions(handle):
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    standalone_markers = {0x01, *range(0xD0, 0xD8), 0xD8, 0xD9}
    while True:
        prefix = handle.read(1)
        if not prefix:
            return None, None
        if prefix != b"\xff":
            continue
        marker_bytes = handle.read(1)
        while marker_bytes == b"\xff":
            marker_bytes = handle.read(1)
        if not marker_bytes:
            return None, None
        marker = marker_bytes[0]
        if marker in standalone_markers:
            continue
        length_bytes = handle.read(2)
        if len(length_bytes) != 2:
            return None, None
        length = int.from_bytes(length_bytes, "big")
        if length < 2:
            return None, None
        if marker in sof_markers:
            frame = handle.read(5)
            if len(frame) != 5:
                return None, None
            height = int.from_bytes(frame[1:3], "big")
            width = int.from_bytes(frame[3:5], "big")
            return width, height
        handle.seek(length - 2, 1)
