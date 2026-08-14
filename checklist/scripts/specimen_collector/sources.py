from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from .html_utils import save_raw_json
from .http_client import PoliteHttpClient
from .models import SpecimenRecord


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_token(value: object, fallback: str = "unknown") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("_")
    return text[:140] or fallback


def value_to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(value_to_str(item) for item in value if value_to_str(item))
    return str(value)


def image_like_url(url: str) -> bool:
    lower = url.lower()
    return bool(
        re.search(r"\.(?:jpe?g|png|tiff?|webp)(?:$|\?)", lower)
        or "mediaphoto.mnhn.fr/media/" in lower
        or "data.nhm.ac.uk/media/" in lower
        or "medialib.naturalis.nl/file/id/" in lower
        or "images.ala.org.au/image/" in lower
        or "ids.si.edu/ids/deliveryservice" in lower
        or "/iiif/" in lower and "/full/" in lower
        or "api.gbif.org/v1/image/cache/" in lower
    )


def gbif_media_url(media: dict) -> str:
    identifier = value_to_str(media.get("identifier")).strip()
    if identifier and image_like_url(identifier):
        return identifier
    references = value_to_str(media.get("references")).strip()
    if references and image_like_url(references):
        return references
    return ""


def gbif_media_score(media: dict) -> tuple[int, int, str]:
    url = gbif_media_url(media)
    lower = url.lower()
    size_score = 0
    size_match = re.search(r"/full/(\d+),", lower)
    if size_match:
        size_score = int(size_match.group(1))
    elif "original" in lower or "fullsize" in lower or "/originals/" in lower:
        size_score = 10000
    elif "api.gbif.org/v1/image/cache/" in lower:
        size_score = 1200
    elif image_like_url(url):
        size_score = 1000
    type_score = 2 if value_to_str(media.get("type")).lower() == "stillimage" else 0
    format_score = 1 if value_to_str(media.get("format")).lower().startswith("image/") else 0
    return (type_score + format_score, size_score, url)


def cvh_records(
    client: PoliteHttpClient,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    endpoint = str(settings.get("api_url", "https://www.cvh.ac.cn/controller/spms/list.php"))
    detail_endpoint = str(settings.get("detail_api_url", "https://www.cvh.ac.cn/controller/spms/detail.php"))
    public_detail = str(settings.get("detail_url", "https://www.cvh.ac.cn/spms/detail.php?id={id}"))
    image_template = str(settings.get("image_url", "https://image.cvh.ac.cn/files/l/{institutionCode}/{collectionCode}.jpg"))
    limit = min(int(settings.get("page_size", 30)), 100)
    max_pages = int(settings.get("max_pages_per_name", 100))
    delay = float(settings.get("request_delay_seconds", 3.0))
    verify_tls = bool(settings.get("verify_tls", True))
    with_photo = "true" if bool(settings.get("with_photo_only", True)) else ""
    records: list[SpecimenRecord] = []
    offset = record_offset

    for _page_number in range(1, max_pages + 1):
        search_page = f"https://www.cvh.ac.cn/spms/list.php?taxonName={quote_plus(query_name)}"
        if with_photo:
            search_page += "&withPhoto=true"
        headers = {
            "Referer": search_page,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        params: dict[str, object] = {
            "taxonName": query_name,
            "limit": limit,
            "offset": offset,
        }
        if with_photo:
            params["withPhoto"] = with_photo
        cache_path = raw_dir / safe_token(query_name) / f"offset_{offset:07d}.json"
        page_from_cache = cache_path.exists() and not refresh
        if page_from_cache:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            data = client.get_json(endpoint, params=params, headers=headers, verify=verify_tls)
            save_raw_json(cache_path, data)
        rows = data.get("rows", [])
        if not isinstance(rows, list) or not rows:
            break

        for row in rows:
            if max_records is not None and len(records) >= max_records:
                return records
            if not isinstance(row, dict):
                continue
            collection_id = value_to_str(row.get("collectionID"))
            if not collection_id:
                continue
            detail_url = public_detail.format(id=collection_id)
            detail_cache = raw_dir / safe_token(query_name) / "records" / f"{safe_token(collection_id)}.json"
            detail_from_cache = detail_cache.exists() and not refresh
            if detail_from_cache:
                detail_data = json.loads(detail_cache.read_text(encoding="utf-8"))
            else:
                detail_data = client.get_json(
                    detail_endpoint,
                    params={"id": collection_id},
                    headers={
                        "Referer": detail_url,
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                    },
                    verify=verify_tls,
                )
                save_raw_json(detail_cache, detail_data)
            detail = detail_data.get("rows", {}) if isinstance(detail_data, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
            institution = value_to_str(detail.get("institutionCode") or row.get("institutionCode"))
            catalog = value_to_str(detail.get("collectionCode") or row.get("collectionCode"))
            image_url = ""
            if value_to_str(detail.get("withPhoto") or row.get("withPhoto")) in {"1", "true", "True"} and institution and catalog:
                image_url = image_template.format(institutionCode=institution, collectionCode=catalog)
            locality_parts = [
                value_to_str(detail.get("country") or row.get("country")),
                value_to_str(detail.get("stateProvince") or row.get("stateProvince")),
                value_to_str(detail.get("county")),
                value_to_str(detail.get("locality")),
            ]
            locality = " ".join(part for part in locality_parts if part).strip()
            records.append(
                SpecimenRecord(
                    source="cvh",
                    query_name=query_name,
                    source_record_id=collection_id,
                    source_record_url=detail_url,
                    institution_code=institution,
                    collection_code="",
                    catalog_number=catalog,
                    scientific_name=re.sub(r"<[^>]+>", "", value_to_str(detail.get("formattedName") or row.get("formattedName"))).strip()
                    or value_to_str(detail.get("canonicalName") or row.get("canonicalName")),
                    recorded_by=value_to_str(detail.get("recordedBy") or row.get("recordedBy")),
                    record_number=value_to_str(detail.get("recordNumber") or row.get("recordNumber")),
                    event_date=value_to_str(detail.get("verbatimEventDate") or detail.get("year") or row.get("year")),
                    country=value_to_str(detail.get("country") or row.get("country")),
                    state_province=value_to_str(detail.get("stateProvince") or row.get("stateProvince")),
                    locality=locality,
                    verbatim_locality=locality,
                    elevation=value_to_str(detail.get("elevation")),
                    type_status=value_to_str(detail.get("typeStatus") or ("type" if value_to_str(row.get("isType")) == "1" else "")),
                    basis_of_record="PRESERVED_SPECIMEN",
                    coordinate_status="missing_coordinates",
                    image_url=image_url,
                    original_image_url=image_url,
                    image_license=str(settings.get("image_license", "http://creativecommons.org/licenses/by-nc-nd/4.0/")),
                    rights_holder=value_to_str(detail.get("institution") or row.get("institutionCode")),
                    accessed_at=now_iso(),
                    download_status="pending" if image_url else "no_image_url",
                )
            )
            if not detail_from_cache:
                client.sleep(delay)

        offset += len(rows)
        total = int(data.get("total") or 0)
        if len(rows) < limit or (total and offset >= total):
            break
        if not page_from_cache:
            client.sleep(delay)
    return records
