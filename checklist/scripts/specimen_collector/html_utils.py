from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

IMAGE_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+(?:\.(?:jpe?g|png|tiff?|webp)(?:\?[^\s\"'<>]*)?|/media/[^\s\"'<>]+)",
    re.IGNORECASE,
)
EXCLUDED_IMAGE_TOKENS = (
    "logo", "icon", "favicon", "sprite", "banner", "button", "avatar",
    "facebook", "twitter", "weibo", "weixin", "loading", "blank",
    "creativecommons.org", "/images/ipni", "/images/mnhn", "/images/tropicos",
    "88x31",
)


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def save_raw_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def save_raw_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_links(html: str, base_url: str, patterns: list[re.Pattern[str]]) -> list[str]:
    soup = soup_from_html(html)
    found: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, anchor.get("href", ""))
        if any(pattern.search(url) for pattern in patterns):
            found.add(url.split("#", 1)[0])
    for pattern in patterns:
        for match in pattern.finditer(html):
            found.add(urljoin(base_url, match.group(0)).split("#", 1)[0])
    return sorted(found)


def collect_image_candidates(html: str, base_url: str) -> list[str]:
    soup = soup_from_html(html)
    found: set[str] = set()
    attributes = ("src", "href", "data-src", "data-original", "data-image", "data-large", "content")
    for tag in soup.find_all(True):
        for attribute in attributes:
            raw = tag.get(attribute)
            if not isinstance(raw, str) or not raw.strip():
                continue
            url = urljoin(base_url, raw.strip())
            lower = url.lower()
            if any(token in lower for token in EXCLUDED_IMAGE_TOKENS):
                continue
            if lower.startswith(("http://", "https://")):
                if (
                    re.search(r"\.(?:jpe?g|png|tiff?|webp)(?:$|\?)", lower)
                    or "mediaphoto" in lower
                    or "/media/" in lower
                    or "/iiif/" in lower and "/full/" in lower
                    or "images.ala.org.au/image/" in lower
                    or "fileget?" in lower
                ):
                    found.add(url)
    for match in IMAGE_URL_RE.finditer(html):
        url = match.group(0).replace("\\/", "/")
        if not any(token in url.lower() for token in EXCLUDED_IMAGE_TOKENS):
            found.add(url)
    return sorted(found)


def same_domain(url_a: str, url_b: str) -> bool:
    return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()


def text_value(soup: BeautifulSoup, labels: tuple[str, ...]) -> str:
    normalized_labels = {re.sub(r"\s+", " ", label).strip().lower() for label in labels}
    for element in soup.find_all(["dt", "th", "strong", "b", "div", "span"]):
        label = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip().lower().rstrip(":")
        if label not in normalized_labels:
            continue
        sibling = element.find_next_sibling()
        if sibling:
            value = re.sub(r"\s+", " ", sibling.get_text(" ", strip=True)).strip()
            if value:
                return value
        parent = element.parent
        if parent:
            text = re.sub(r"\s+", " ", parent.get_text(" ", strip=True)).strip()
            for original in labels:
                if text.lower().startswith(original.lower()):
                    value = text[len(original):].lstrip(" :：")
                    if value:
                        return value
    return ""
