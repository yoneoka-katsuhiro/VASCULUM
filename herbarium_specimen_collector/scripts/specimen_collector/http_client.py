from __future__ import annotations

import hashlib
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from PIL import Image
from requests import Response
from urllib3.exceptions import InsecureRequestWarning

StatusCallback = Callable[[str], None]
SECRET_QUERY_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "password",
    "client_secret",
}


@dataclass(frozen=True)
class DownloadedImage:
    path: Path
    sha256: str
    width: int
    height: int
    size_bytes: int
    content_type: str


def format_bytes(value: int | float) -> str:
    size = float(value)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return redact_secret_text(value)
    if not parsed.query:
        return value
    redacted = [
        (key, "REDACTED" if key.lower() in SECRET_QUERY_KEYS else val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit(parsed._replace(query=urlencode(redacted, doseq=True)))


def redact_secret_text(value: object) -> str:
    text = str(value)
    pattern = r"([?&](?:api_key|apikey|key|token|access_token|password|client_secret)=)[^&\s)]+"
    return re.sub(pattern, r"\1REDACTED", text, flags=re.IGNORECASE)


class PoliteHttpClient:
    def __init__(
        self,
        contact_email: str,
        timeout_seconds: int,
        retry_count: int,
        retry_backoff_seconds: float,
        retry_callback: Callable[[], None] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.retry_backoff_seconds = retry_backoff_seconds
        self.retry_callback = retry_callback
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "VASCULUM/0.1.9 "
                    f"(academic research; contact: {contact_email})"
                ),
                "Accept-Language": "en,ja;q=0.9,zh;q=0.8",
            }
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        status_callback: StatusCallback | None = None,
        **kwargs: object,
    ) -> Response:
        last_error: Exception | None = None
        timeout_seconds = float(kwargs.pop("timeout_seconds", self.timeout_seconds))
        retry_count = int(kwargs.pop("retry_count", self.retry_count))
        retry_backoff_seconds = float(kwargs.pop("retry_backoff_seconds", self.retry_backoff_seconds))
        for attempt in range(1, retry_count + 1):
            if status_callback is not None:
                status_callback(f"CONNECTING {attempt}/{retry_count}")
            try:
                if kwargs.get("verify") is False:
                    requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
                response = self.session.request(
                    method,
                    url,
                    timeout=timeout_seconds,
                    allow_redirects=True,
                    **kwargs,
                )
                if response.status_code == 429:
                    if self.retry_callback is not None:
                        self.retry_callback()
                    retry_after = response.headers.get("Retry-After")
                    delay = (
                        float(retry_after)
                        if retry_after and retry_after.replace(".", "", 1).isdigit()
                        else retry_backoff_seconds * attempt
                    )
                    if status_callback is not None:
                        status_callback(f"RATE LIMITED; RETRY IN {delay:.1f}s")
                    self.sleep(delay, status_callback=status_callback, prefix="RATE-LIMIT WAIT")
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt == retry_count:
                    break
                if self.retry_callback is not None:
                    self.retry_callback()
                delay = retry_backoff_seconds * attempt
                if status_callback is not None:
                    status_callback(f"RETRY {attempt + 1}/{retry_count} IN {delay:.1f}s")
                self.sleep(delay, status_callback=status_callback, prefix="RETRY WAIT")
        raise RuntimeError(
            f"HTTP request failed after retries: {redact_url(url)}: {redact_secret_text(last_error)}"
        )

    def get_json(
        self,
        url: str,
        params: dict[str, object] | None = None,
        *,
        headers: dict[str, str] | None = None,
        verify: bool | None = None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> dict:
        kwargs: dict[str, object] = {"params": params}
        if headers is not None:
            kwargs["headers"] = headers
        if verify is not None:
            kwargs["verify"] = verify
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if retry_count is not None:
            kwargs["retry_count"] = retry_count
        if retry_backoff_seconds is not None:
            kwargs["retry_backoff_seconds"] = retry_backoff_seconds
        response = self._request("GET", url, **kwargs)
        return response.json()

    def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        verify: bool | None = None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> str:
        text, _ = self.get_text_with_url(
            url,
            headers=headers,
            verify=verify,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        return text

    def get_text_with_url(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        verify: bool | None = None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> tuple[str, str]:
        kwargs: dict[str, object] = {}
        if headers is not None:
            kwargs["headers"] = headers
        if verify is not None:
            kwargs["verify"] = verify
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if retry_count is not None:
            kwargs["retry_count"] = retry_count
        if retry_backoff_seconds is not None:
            kwargs["retry_backoff_seconds"] = retry_backoff_seconds
        response = self._request("GET", url, **kwargs)
        response.encoding = response.apparent_encoding or response.encoding
        return response.text, response.url

    def post_json(
        self,
        url: str,
        data: dict[str, object] | None = None,
        *,
        headers: dict[str, str] | None = None,
        verify: bool | None = None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> dict:
        kwargs: dict[str, object] = {"data": data}
        if headers is not None:
            kwargs["headers"] = headers
        if verify is not None:
            kwargs["verify"] = verify
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if retry_count is not None:
            kwargs["retry_count"] = retry_count
        if retry_backoff_seconds is not None:
            kwargs["retry_backoff_seconds"] = retry_backoff_seconds
        response = self._request("POST", url, **kwargs)
        return response.json()

    def post_text_with_url(
        self,
        url: str,
        data: dict[str, object] | None = None,
        *,
        headers: dict[str, str] | None = None,
        verify: bool | None = None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> tuple[str, str]:
        kwargs: dict[str, object] = {"data": data}
        if headers is not None:
            kwargs["headers"] = headers
        if verify is not None:
            kwargs["verify"] = verify
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if retry_count is not None:
            kwargs["retry_count"] = retry_count
        if retry_backoff_seconds is not None:
            kwargs["retry_backoff_seconds"] = retry_backoff_seconds
        response = self._request("POST", url, **kwargs)
        response.encoding = response.apparent_encoding or response.encoding
        return response.text, response.url

    def post_text(
        self,
        url: str,
        data: dict[str, object] | None = None,
        *,
        headers: dict[str, str] | None = None,
        verify: bool | None = None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> str:
        text, _ = self.post_text_with_url(
            url,
            data,
            headers=headers,
            verify=verify,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        return text

    def get_bytes(
        self,
        url: str,
        params: dict[str, object] | None = None,
        *,
        headers: dict[str, str] | None = None,
        verify: bool | None = None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> bytes:
        kwargs: dict[str, object] = {"params": params}
        if headers is not None:
            kwargs["headers"] = headers
        if verify is not None:
            kwargs["verify"] = verify
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if retry_count is not None:
            kwargs["retry_count"] = retry_count
        if retry_backoff_seconds is not None:
            kwargs["retry_backoff_seconds"] = retry_backoff_seconds
        response = self._request("GET", url, **kwargs)
        return response.content

    def iter_image_bytes(
        self,
        url: str,
        status_callback: StatusCallback | None = None,
        headers: dict[str, str] | None = None,
        verify: bool | None = None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> tuple[Response, Iterator[bytes]]:
        kwargs: dict[str, object] = {"stream": True, "status_callback": status_callback, "headers": headers}
        if verify is not None:
            kwargs["verify"] = verify
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if retry_count is not None:
            kwargs["retry_count"] = retry_count
        if retry_backoff_seconds is not None:
            kwargs["retry_backoff_seconds"] = retry_backoff_seconds
        response = self._request("GET", url, **kwargs)
        return response, response.iter_content(chunk_size=1024 * 256)

    @staticmethod
    def sleep(
        seconds: float,
        status_callback: StatusCallback | None = None,
        prefix: str = "WAITING",
    ) -> None:
        if seconds <= 0:
            return
        total = seconds + random.uniform(0, min(0.5, seconds * 0.1))
        deadline = time.monotonic() + total
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if status_callback is not None:
                status_callback(f"{prefix} {remaining:.1f}s")
            time.sleep(min(0.5, remaining))


def download_and_validate_image(
    client: PoliteHttpClient,
    url: str,
    destination: Path,
    minimum_width: int,
    minimum_height: int,
    minimum_bytes: int,
    status_callback: StatusCallback | None = None,
    headers: dict[str, str] | None = None,
    verify: bool | None = None,
    max_image_dimension: int = 0,
    jpeg_quality: int = 88,
    timeout_seconds: float | None = None,
    retry_count: int | None = None,
    retry_backoff_seconds: float | None = None,
) -> DownloadedImage:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    response, chunks = client.iter_image_bytes(
        url,
        status_callback=status_callback,
        headers=headers,
        verify=verify,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if not content_type.startswith("image/"):
        raise ValueError(f"URL did not return an image: {content_type or 'unknown'}")

    content_length_raw = response.headers.get("Content-Length", "")
    total_bytes = int(content_length_raw) if content_length_raw.isdigit() else 0
    digest = hashlib.sha256()
    size_bytes = 0
    started_at = time.monotonic()
    last_update = 0.0

    if status_callback is not None:
        total_text = f" / {format_bytes(total_bytes)}" if total_bytes else ""
        status_callback(f"DOWNLOADING 0 B{total_text}")

    with temporary.open("wb") as handle:
        for chunk in chunks:
            if not chunk:
                continue
            handle.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)
            now = time.monotonic()
            if status_callback is not None and (now - last_update >= 0.25 or (total_bytes and size_bytes >= total_bytes)):
                elapsed = max(now - started_at, 0.001)
                rate = size_bytes / elapsed
                if total_bytes:
                    percent = min(100.0, size_bytes / total_bytes * 100.0)
                    status_callback(
                        f"DOWNLOADING {percent:5.1f}%  "
                        f"{format_bytes(size_bytes)}/{format_bytes(total_bytes)}  "
                        f"{format_bytes(rate)}/s"
                    )
                else:
                    status_callback(
                        f"DOWNLOADING {format_bytes(size_bytes)}  {format_bytes(rate)}/s"
                    )
                last_update = now

    try:
        if status_callback is not None:
            status_callback(f"VALIDATING {format_bytes(size_bytes)}")
        if size_bytes < minimum_bytes:
            raise ValueError(f"Image was too small: {size_bytes} bytes")
        with Image.open(temporary) as image:
            width, height = image.size
            image_format = image.format
            image.verify()
        resize_needed = max_image_dimension > 0 and max(width, height) > max_image_dimension
        reencode_needed = image_format != "JPEG"
        if resize_needed or reencode_needed:
            if status_callback is not None:
                action = (
                    f"RESIZING {width}x{height} TO MAX {max_image_dimension}"
                    if resize_needed
                    else "CONVERTING TO JPEG"
                )
                status_callback(action)
            with Image.open(temporary) as image:
                image.load()
                if resize_needed:
                    image.thumbnail((max_image_dimension, max_image_dimension), Image.Resampling.LANCZOS)
                resized = image.convert("RGB")
                resized.save(temporary, "JPEG", quality=jpeg_quality, optimize=True)
            size_bytes = temporary.stat().st_size
            with temporary.open("rb") as handle:
                digest = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1024 * 256), b""):
                    digest.update(chunk)
            with Image.open(temporary) as image:
                width, height = image.size
                image.verify()
            content_type = "image/jpeg"
        if width < minimum_width or height < minimum_height:
            raise ValueError(f"Image dimensions were too small: {width}x{height}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return DownloadedImage(
        path=destination,
        sha256=digest.hexdigest(),
        width=width,
        height=height,
        size_bytes=size_bytes,
        content_type=content_type,
    )
