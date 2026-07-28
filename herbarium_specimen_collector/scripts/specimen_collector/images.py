from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

from .http_client import DownloadedImage, PoliteHttpClient, download_and_validate_image
from .models import SpecimenRecord
from .outputs import SourceReport
from .progress import TerminalProgress
from .records import append_note, image_basename, unique_values


@dataclass
class ImageResult:
    downloaded: int = 0
    existing: int = 0
    failed: int = 0
    skipped: int = 0


def referenced_image_paths(
    output_dir: Path,
    records: list[SpecimenRecord],
) -> set[Path]:
    return {
        (output_dir / record.local_image_path).resolve()
        for record in records
        if record.local_image_path
    }


def count_referenced_images(output_dir: Path, records: list[SpecimenRecord]) -> int:
    image_dir = output_dir / "images"
    referenced = referenced_image_paths(output_dir, records)
    return sum(1 for path in image_dir.glob("*.jpg") if path.resolve() in referenced)


def prune_unreferenced_images(output_dir: Path, records: list[SpecimenRecord]) -> int:
    image_dir = output_dir / "images"
    referenced = referenced_image_paths(output_dir, records)
    removed = 0
    for path in image_dir.glob("*.jpg"):
        if path.resolve() not in referenced:
            path.unlink()
            removed += 1
    return removed


def gbif_image_cache_urls(record: SpecimenRecord) -> list[str]:
    if not record.source_record_id.isdigit() or not record.image_url:
        return []
    if "api.gbif.org/v1/image/cache/" in record.image_url:
        return []
    digest = hashlib.md5(record.image_url.encode("utf-8")).hexdigest()
    return [
        f"https://api.gbif.org/v1/image/cache/1200x/occurrence/{record.source_record_id}/media/{digest}",
        f"https://api.gbif.org/v1/image/cache/occurrence/{record.source_record_id}/media/{digest}",
    ]


def download_urls(record: SpecimenRecord, settings: dict) -> list[str]:
    urls = [record.image_url]
    if bool(settings.get("try_gbif_image_cache", True)):
        urls.extend(gbif_image_cache_urls(record))
    return unique_values(urls)


def image_request_headers(record: SpecimenRecord, url: str) -> dict[str, str]:
    headers = {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    if record.source_record_url.startswith(("http://", "https://")):
        headers["Referer"] = record.source_record_url
    if "iiif.rbge.org.uk" in url and "Referer" not in headers:
        headers["Referer"] = "https://data.rbge.org.uk/"
    return headers


def image_request_verify(record: SpecimenRecord, source_settings: dict) -> bool | None:
    settings = source_settings.get(record.source, {})
    if "image_verify_tls" in settings:
        return bool(settings["image_verify_tls"])
    if "verify_tls" in settings:
        return bool(settings["verify_tls"])
    return None


def image_file_info(path: Path) -> tuple[str, int, int, int]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    with Image.open(path) as image:
        width, height = image.size
    return digest.hexdigest(), width, height, path.stat().st_size


def resize_existing_image(path: Path, max_dimension: int, jpeg_quality: int) -> None:
    with Image.open(path) as image:
        image.load()
        if max_dimension > 0:
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        image.convert("RGB").save(path, "JPEG", quality=jpeg_quality, optimize=True)


def taif_pyramid_levels(width: int, height: int, tile_size: int) -> list[dict[str, int]]:
    levels: list[dict[str, int]] = []
    level_id = 0
    level_width = width
    level_height = height
    minimum_size = (tile_size / 2) + 1
    while level_width > minimum_size or level_height > minimum_size:
        levels.append({"id": level_id, "width": level_width, "height": level_height})
        level_width //= 2
        level_height //= 2
        level_id += 1
    levels.reverse()
    return levels


def download_taif_tiled_image(
    client: PoliteHttpClient,
    url: str,
    destination: Path,
    minimum_width: int,
    minimum_height: int,
    minimum_bytes: int,
    max_image_dimension: int,
    jpeg_quality: int,
    headers: dict[str, str],
    verify: bool | None,
    timeout_seconds: float,
    retry_count: int,
    retry_backoff_seconds: float,
) -> DownloadedImage:
    parsed = urlparse(url)
    params = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
    base = params["base"].rstrip("/")
    prefix = params["prefix"]
    width = int(params["width"])
    height = int(params["height"])
    tile_size = int(params.get("tile", "512"))
    levels = taif_pyramid_levels(width, height, tile_size)
    selected = levels[-1]
    if max_image_dimension > 0:
        for level in levels:
            if max(level["width"], level["height"]) >= max_image_dimension:
                selected = level
                break

    target_width = selected["width"]
    target_height = selected["height"]
    x_tiles = (target_width + tile_size - 1) // tile_size
    y_tiles = (target_height + tile_size - 1) // tile_size
    canvas = Image.new("RGB", (target_width, target_height), "white")
    for y in range(y_tiles):
        for x in range(x_tiles):
            tile_url = f"{base}/{prefix}{selected['id']:03d}_{x:03d}_{y:03d}.jpg"
            response, chunks = client.iter_image_bytes(
                tile_url,
                headers=headers,
                verify=verify,
                timeout_seconds=timeout_seconds,
                retry_count=retry_count,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if not content_type.startswith("image/"):
                raise ValueError(f"TAIF tile did not return an image: {content_type or 'unknown'}")
            with Image.open(BytesIO(b"".join(chunk for chunk in chunks if chunk))) as tile:
                tile.load()
                tile_rgb = tile.convert("RGB")
                remaining_width = min(tile_rgb.width, target_width - x * tile_size)
                remaining_height = min(tile_rgb.height, target_height - y * tile_size)
                canvas.paste(
                    tile_rgb.crop((0, 0, remaining_width, remaining_height)),
                    (x * tile_size, y * tile_size),
                )

    if max_image_dimension > 0 and max(canvas.size) > max_image_dimension:
        canvas.thumbnail((max_image_dimension, max_image_dimension), Image.Resampling.LANCZOS)
    if canvas.width < minimum_width or canvas.height < minimum_height:
        raise ValueError(f"Image dimensions were too small: {canvas.width}x{canvas.height}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".jpg.part")
    canvas.save(temporary, "JPEG", quality=jpeg_quality, optimize=True)
    if temporary.stat().st_size < minimum_bytes:
        size_bytes = temporary.stat().st_size
        temporary.unlink(missing_ok=True)
        raise ValueError(f"Image was too small: {size_bytes} bytes")
    temporary.replace(destination)
    digest, final_width, final_height, size_bytes = image_file_info(destination)
    return DownloadedImage(
        path=destination,
        sha256=digest,
        width=final_width,
        height=final_height,
        size_bytes=size_bytes,
        content_type="image/jpeg",
    )


def _destinations(
    output_dir: Path,
    records: list[SpecimenRecord],
    accepted_name: str,
) -> dict[int, Path]:
    image_dir = output_dir / "images"
    occurrences: Counter[str] = Counter()
    result: dict[int, Path] = {}
    for record in records:
        basename = image_basename(record, accepted_name)
        occurrences[basename] += 1
        suffix = "" if occurrences[basename] == 1 else f"_{occurrences[basename]}"
        result[id(record)] = image_dir / f"{basename}{suffix}.jpg"
    return result


def download_images(
    *,
    client: PoliteHttpClient,
    output_dir: Path,
    records: list[SpecimenRecord],
    accepted_name: str,
    source_settings: dict,
    download_settings: dict,
    skip_images: bool,
    progress: TerminalProgress,
    source_reports: dict[str, SourceReport],
) -> ImageResult:
    result = ImageResult()
    candidates = [
        record
        for record in records
        if record.image_url and record.download_status in {"", "pending"}
    ]
    if skip_images:
        for record in candidates:
            record.download_status = "skipped_by_option"
        result.skipped = len(candidates)
        return result
    (output_dir / "images").mkdir(parents=True, exist_ok=True)

    minimum_width = int(download_settings.get("minimum_image_width", 800))
    minimum_height = int(download_settings.get("minimum_image_height", 800))
    minimum_bytes = int(download_settings.get("minimum_image_bytes", 50000))
    max_dimension = int(download_settings.get("max_image_dimension", 2400))
    jpeg_quality = int(download_settings.get("jpeg_quality", 88))
    timeout = float(download_settings.get("image_timeout_seconds", 20))
    retries = int(download_settings.get("image_retry_count", 2))
    backoff = float(download_settings.get("image_retry_backoff_seconds", 2.0))
    destinations = _destinations(output_dir, candidates, accepted_name)
    totals = Counter(record.source for record in candidates)
    completed: Counter[str] = Counter()
    successful: Counter[str] = Counter()

    for record in candidates:
        source = record.source
        destination = destinations[id(record)]
        label = destination.stem
        progress.set_task(f"{source} - downloading {label}")
        progress.update_source(
            source,
            status="images",
            completed=completed[source],
            total=totals[source],
        )

        if destination.exists() and destination.stat().st_size >= minimum_bytes:
            try:
                resize_existing_image(destination, max_dimension, jpeg_quality)
                digest, width, height, size_bytes = image_file_info(destination)
                record.local_image_path = destination.relative_to(output_dir).as_posix()
                record.image_sha256 = digest
                record.download_url = record.download_url or record.image_url
                record.download_status = "already_downloaded"
                record.notes = append_note(
                    record.notes,
                    f"{width}x{height}; {size_bytes} bytes; image/jpeg",
                )
                result.existing += 1
                successful[source] += 1
            except Exception as exc:
                destination.unlink(missing_ok=True)
                record.notes = append_note(record.notes, f"existing image rejected: {exc}")

        if record.download_status != "already_downloaded":
            failures: list[str] = []
            for candidate_url in download_urls(record, download_settings):
                try:
                    if candidate_url.startswith("taif_tiles://"):
                        downloaded = download_taif_tiled_image(
                            client=client,
                            url=candidate_url,
                            destination=destination,
                            minimum_width=minimum_width,
                            minimum_height=minimum_height,
                            minimum_bytes=minimum_bytes,
                            max_image_dimension=max_dimension,
                            jpeg_quality=jpeg_quality,
                            headers=image_request_headers(record, candidate_url),
                            verify=image_request_verify(record, source_settings),
                            timeout_seconds=timeout,
                            retry_count=retries,
                            retry_backoff_seconds=backoff,
                        )
                    else:
                        downloaded = download_and_validate_image(
                            client=client,
                            url=candidate_url,
                            destination=destination,
                            minimum_width=minimum_width,
                            minimum_height=minimum_height,
                            minimum_bytes=minimum_bytes,
                            headers=image_request_headers(record, candidate_url),
                            verify=image_request_verify(record, source_settings),
                            max_image_dimension=max_dimension,
                            jpeg_quality=jpeg_quality,
                            timeout_seconds=timeout,
                            retry_count=retries,
                            retry_backoff_seconds=backoff,
                        )
                    record.local_image_path = downloaded.path.relative_to(output_dir).as_posix()
                    record.image_sha256 = downloaded.sha256
                    record.download_url = candidate_url
                    record.download_status = "downloaded"
                    record.notes = append_note(
                        record.notes,
                        f"{downloaded.width}x{downloaded.height}; "
                        f"{downloaded.size_bytes} bytes; image/jpeg",
                    )
                    result.downloaded += 1
                    successful[source] += 1
                    break
                except Exception as exc:
                    failures.append(str(exc))
            else:
                record.download_status = "rejected_or_failed"
                record.notes = append_note(record.notes, " | ".join(failures))
                result.failed += 1
                source_reports[source].status = "partial"
                message = f"{source}: {label}: {failures[-1] if failures else 'image unavailable'}"
                progress.add_error(message)

        completed[source] += 1
        source_reports[source].images = successful[source]
        progress.update_source(
            source,
            status="complete" if completed[source] == totals[source] else "images",
            completed=completed[source],
            total=totals[source],
            images=successful[source],
        )
        delay = float(source_settings.get(source, {}).get("image_delay_seconds", 2.0))
        if delay > 0 and completed[source] < totals[source]:
            client.sleep(delay)

    return result
