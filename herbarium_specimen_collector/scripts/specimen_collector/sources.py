from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import quote, quote_plus, urlencode, urljoin, urlparse

from .html_utils import collect_image_candidates, collect_links, save_raw_json, save_raw_text, soup_from_html, text_value
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
        or "inaturalist-open-data" in lower
        or "images.ala.org.au/image/" in lower
        or "ids.si.edu/ids/deliveryservice" in lower
        or "data.brin.go.id/api/access/datafile/" in lower
        or "fileget?" in lower
        or "db.kahaku.go.jp/webmuseum/rest/media/" in lower
        or "/iiif/" in lower and "/full/" in lower
        or "api.gbif.org/v1/image/cache/" in lower
    )


def gbif_search_params(query_name: str, settings: dict, limit: int, offset: int) -> dict[str, object]:
    mode = str(settings.get("occurrence_mode", "specimens")).strip().lower()
    coordinate_filter = str(settings.get("coordinate_filter", "any")).strip().lower()
    params: dict[str, object] = {
        "scientificName": query_name,
        "mediaType": "StillImage",
        "limit": limit,
        "offset": offset,
    }
    institution_codes = settings.get("institution_codes") or []
    if isinstance(institution_codes, str):
        institution_codes = [institution_codes]
    if institution_codes:
        params["institutionCode"] = [value_to_str(code).strip() for code in institution_codes if value_to_str(code).strip()]
    extra_params = settings.get("extra_params") or {}
    if isinstance(extra_params, dict):
        for key, value in extra_params.items():
            if value not in (None, "", []):
                params[key] = value

    if mode == "specimens":
        params["basisOfRecord"] = "PRESERVED_SPECIMEN"
    elif mode == "observations":
        params["basisOfRecord"] = "HUMAN_OBSERVATION"
    elif mode in {"specimens-and-observations", "specimens_observations"}:
        params["basisOfRecord"] = ["PRESERVED_SPECIMEN", "HUMAN_OBSERVATION"]
    elif mode in {"all-images", "all"}:
        pass
    else:
        raise ValueError(
            "Unknown GBIF occurrence_mode. Use specimens, observations, "
            "specimens-and-observations, or all-images."
        )

    if coordinate_filter in {"with-coordinates", "with_coordinates", "coordinates"}:
        params["hasCoordinate"] = "true"
        params["hasGeospatialIssue"] = "false"
    elif coordinate_filter in {"without-coordinates", "without_coordinates", "no-coordinates"}:
        params["hasCoordinate"] = "false"
    elif coordinate_filter == "any":
        pass
    else:
        raise ValueError("Unknown GBIF coordinate_filter. Use any, with-coordinates, or without-coordinates.")

    return params


def gbif_cache_segment(settings: dict) -> str:
    mode = str(settings.get("occurrence_mode", "specimens")).strip().lower()
    coordinate_filter = str(settings.get("coordinate_filter", "any")).strip().lower()
    return safe_token(f"{mode}_{coordinate_filter}")


def coordinate_status_from_item(item: dict) -> str:
    has_lat = item.get("decimalLatitude") is not None
    has_lon = item.get("decimalLongitude") is not None
    if has_lat and has_lon:
        return "coordinates_geospatial_issue" if item.get("hasGeospatialIssue") else "coordinates_ok"
    return "missing_coordinates"


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


def gbif_records(
    client: PoliteHttpClient,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
    source_name: str = "gbif",
) -> list[SpecimenRecord]:
    endpoint = "https://api.gbif.org/v1/occurrence/search"
    limit = min(int(settings.get("page_size", 100)), 300)
    max_pages = int(settings.get("max_pages_per_name", 100))
    delay = float(settings.get("request_delay_seconds", 1.0))
    records: list[SpecimenRecord] = []
    offset = record_offset

    for page_number in range(1, max_pages + 1):
        cache_path = raw_dir / safe_token(query_name) / gbif_cache_segment(settings) / f"offset_{offset:07d}.json"
        if cache_path.exists() and not refresh:
            import json
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            data = client.get_json(
                endpoint,
                params=gbif_search_params(query_name, settings, limit, offset),
            )
            save_raw_json(cache_path, data)
        results = data.get("results", [])
        if not isinstance(results, list) or not results:
            break

        for item in results:
            if max_records is not None and len(records) >= max_records:
                return records
            media_items = item.get("media") or []
            image_media = [
                m for m in media_items
                if isinstance(m, dict) and gbif_media_url(m)
            ]
            if image_media and str(settings.get("media_policy", "best-per-occurrence")) != "all-media":
                image_media = [max(image_media, key=gbif_media_score)]
            if not image_media:
                image_media = [{}]
            for media in image_media:
                image_url = gbif_media_url(media)
                gbif_key = value_to_str(item.get("key"))
                records.append(
                    SpecimenRecord(
                        source=source_name,
                        query_name=query_name,
                        source_record_id=gbif_key,
                        source_record_url=value_to_str(
                            item.get("references")
                            or item.get("occurrenceID")
                            or (f"https://www.gbif.org/occurrence/{gbif_key}" if gbif_key else "")
                        ),
                        occurrence_id=value_to_str(item.get("occurrenceID")),
                        institution_code=value_to_str(item.get("institutionCode")),
                        collection_code=value_to_str(item.get("collectionCode")),
                        catalog_number=value_to_str(item.get("catalogNumber")),
                        scientific_name=value_to_str(item.get("scientificName") or item.get("acceptedScientificName")),
                        recorded_by=value_to_str(item.get("recordedBy")),
                        record_number=value_to_str(item.get("recordNumber")),
                        event_date=value_to_str(item.get("eventDate") or item.get("verbatimEventDate")),
                        country=value_to_str(item.get("country")),
                        state_province=value_to_str(item.get("stateProvince")),
                        locality=value_to_str(item.get("locality")),
                        verbatim_locality=value_to_str(item.get("verbatimLocality")),
                        decimal_latitude=value_to_str(item.get("decimalLatitude")),
                        decimal_longitude=value_to_str(item.get("decimalLongitude")),
                        elevation=value_to_str(item.get("verbatimElevation") or item.get("elevation")),
                        identified_by=value_to_str(item.get("identifiedBy")),
                        type_status=value_to_str(item.get("typeStatus")),
                        basis_of_record=value_to_str(item.get("basisOfRecord")),
                        coordinate_status=coordinate_status_from_item(item),
                        image_url=image_url,
                        original_image_url=image_url,
                        image_license=value_to_str(media.get("license") or item.get("license")),
                        rights_holder=value_to_str(media.get("rightsHolder") or item.get("rightsHolder")),
                        accessed_at=now_iso(),
                        download_status="pending" if image_url else "no_image_url",
                    )
                )

        offset += len(results)
        if bool(data.get("endOfRecords")) or len(results) < limit:
            break
        client.sleep(delay)
    return records


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

    for page_number in range(1, max_pages + 1):
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
            import json
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
                import json
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


def table_row_value(html: str, label: str) -> str:
    soup = soup_from_html(html)
    expected = re.sub(r"\s+", " ", label).strip().lower().rstrip(":")
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        left = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip().lower().rstrip(":")
        if left == expected:
            return re.sub(r"\s+", " ", cells[1].get_text(" ", strip=True)).strip()
    return ""


def tns_table_values(html: str) -> dict[str, str]:
    soup = soup_from_html(html)
    values: dict[str, str] = {}
    for row in soup.find_all("tr"):
        header = row.find("th")
        data = row.find("td")
        if not header or not data:
            continue
        key = re.sub(r"\s+", " ", header.get_text(" ", strip=True)).strip()
        value = re.sub(r"\s+", " ", data.get_text(" ", strip=True)).strip()
        if key:
            values[key] = value
    return values


def tns_date(value: str) -> str:
    match = re.fullmatch(r"(\d{4})/(\d{1,2})(?:/(\d{1,2}))?", value.strip())
    if not match:
        return value
    year, month, day = match.groups()
    if day:
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return f"{year}-{month.zfill(2)}"


def tns_detail_urls(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"https://db\.kahaku\.go\.jp/webmuseum/detail\?cls=[^'\"<> ]+", html):
        url = unescape(match.group(0)).split("'", 1)[0].rstrip(");")
        urls.append(url)
    for match in re.finditer(r"/webmuseum/detail\?cls=[^'\"<> ]+", html):
        url = urljoin(base_url, unescape(match.group(0)).split("'", 1)[0].rstrip(");"))
        urls.append(url)
    return list(dict.fromkeys(urls))


def tns_image_url_from_detail_url(detail_url: str, settings: dict) -> str:
    parsed = urlparse(detail_url)
    query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
    cls = query.get("cls", "col_b1_01")
    pkey = query.get("pkey", "")
    if not pkey:
        return ""
    size = str(settings.get("media_size", "S"))
    suffix = str(settings.get("media_suffix", "c2510"))
    return f"https://db.kahaku.go.jp/webmuseum/rest/media/{size}?cls={cls}&pkey={pkey}&{suffix}"


def tns_detail_has_image(html: str) -> bool:
    soup = soup_from_html(html)
    image_flag = soup.find("input", id="image_v")
    if image_flag is not None:
        return value_to_str(image_flag.get("value")).lower() == "true"
    return bool(soup.select(".repImageThumbnail, .imgRep_class"))


def tns_record_from_detail(html: str, url: str, query_name: str, settings: dict) -> SpecimenRecord:
    values = tns_table_values(html)
    catalog = values.get("標本登録番号 (TNS-VS-)", "")
    scientific_name = values.get("学名", "")
    locality_english = " ".join(
        part
        for part in [
            values.get("採集地(国名)[英]", ""),
            values.get("採集地(都道府県名)[英]", ""),
            values.get("採集地(島名)[英]", ""),
            values.get("採集地(郡名)[英]", ""),
            values.get("採集地(市町村名)[英]", ""),
            values.get("採集地(区名)[英]", ""),
            values.get("採集地(詳細地名)[英]", ""),
        ]
        if part
    )
    locality_original = " ".join(
        part
        for part in [
            values.get("採集地(国名)[英]", ""),
            values.get("採集地(都道府県名)[和]", ""),
            values.get("採集地(島名)[和]", ""),
            values.get("採集地(郡名)[和]", ""),
            values.get("採集地(市町村名)[和]", ""),
            values.get("採集地(区名)[和]", ""),
            values.get("採集地(詳細地名)[和]", ""),
        ]
        if part
    )
    permalink = values.get("パーマネントリンク", "")
    image_url = tns_image_url_from_detail_url(url, settings) if tns_detail_has_image(html) else ""
    return SpecimenRecord(
        source="tns",
        query_name=query_name,
        source_record_id=f"TNS-VS-{catalog}" if catalog else safe_token(url),
        source_record_url=permalink or url,
        occurrence_id=f"TNS:VS:{catalog}" if catalog else "",
        institution_code="TNS",
        collection_code="VS",
        catalog_number=f"TNS-VS-{catalog}" if catalog else "",
        scientific_name=scientific_name,
        recorded_by=values.get("採集者名[英]", "") or values.get("採集者名[和]", ""),
        record_number=values.get("採集番号", ""),
        event_date=tns_date(values.get("採集年月日", "")),
        country=values.get("採集地(国名)[英]", ""),
        state_province=values.get("採集地(都道府県名)[英]", "") or values.get("採集地(都道府県名)[和]", ""),
        locality=locality_english,
        verbatim_locality=locality_original or locality_english,
        basis_of_record="PRESERVED_SPECIMEN",
        coordinate_status="missing_coordinates",
        image_url=image_url,
        original_image_url=image_url,
        rights_holder="National Museum of Nature and Science, Tokyo",
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
        notes="metadata_from_tns_webmuseum",
    )


def tns_webmuseum_records(
    client: PoliteHttpClient,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    search_template = str(
        settings.get(
            "search_url",
            "https://db.kahaku.go.jp/CrossSearch/list?secIdx=0&cls=top&pn={page}&chkCls=col_b1_01&c1_f={query}&c1_a=1&c1_l=1&dispnum={page_size}",
        )
    )
    max_pages = int(settings.get("max_pages_per_name", 5))
    page_size = int(settings.get("page_size", 50))
    delay = float(settings.get("request_delay_seconds", 2.0))
    detail_urls: list[str] = []

    for page in range(1, max_pages + 1):
        search_url = search_template.format(query=quote_plus(query_name), page=page, page_size=page_size)
        cache_path = raw_dir / safe_token(query_name) / f"search_{page:04d}.html"
        if cache_path.exists() and not refresh:
            html = cache_path.read_text(encoding="utf-8")
            final_url = search_url
        else:
            html, final_url = client.get_text_with_url(search_url)
            save_raw_text(cache_path, html)
        before = len(detail_urls)
        detail_urls.extend(tns_detail_urls(html, final_url))
        detail_urls = list(dict.fromkeys(detail_urls))
        if len(detail_urls) == before and page > 1:
            break
        client.sleep(delay)

    selected_urls = detail_urls[record_offset:]
    if max_records is not None:
        selected_urls = selected_urls[:max_records]

    records: list[SpecimenRecord] = []
    for index, url in enumerate(selected_urls, start=record_offset + 1):
        cache_path = raw_dir / safe_token(query_name) / "records" / f"record_{index:05d}.html"
        if cache_path.exists() and not refresh:
            html = cache_path.read_text(encoding="utf-8")
        else:
            html = client.get_text(url)
            save_raw_text(cache_path, html)
        record = tns_record_from_detail(html, url, query_name, settings)
        if bool(settings.get("exact_name_filter", True)) and not query_name_matches(
            query_name,
            [record.scientific_name],
        ):
            continue
        records.append(record)
        client.sleep(delay)
    return records


def kag_clean(value: object) -> str:
    return re.sub(r"\s+", " ", unescape(value_to_str(value))).strip()


def kag_search_payload(query_name: str, offset: int, page_size: int) -> dict[str, object]:
    genus, epithet = split_binomial(query_name)
    payload: dict[str, object] = {
        "auth": "",
        "lang": "English",
        "from_list": str(offset),
        "max_list": str(page_size),
    }
    if genus:
        payload["S_genus"] = "ON"
        payload["D_genus"] = genus
    if epithet:
        payload["S_epithet"] = "ON"
        payload["D_epithet"] = epithet
    return payload


def kag_total_records(html: str) -> int:
    match = re.search(r"Total record number\s+(\d+)", html, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def kag_list_rows(html: str) -> list[dict[str, str]]:
    soup = soup_from_html(html)
    rows: list[dict[str, str]] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 9:
            continue
        if not cells[0].find("a", attrs={"name": re.compile(r"^TXNDET", re.IGNORECASE)}):
            continue
        specimen_id = kag_clean(cells[1].get_text(" ", strip=True))
        if not specimen_id.startswith("KAG"):
            continue
        detail_input = row.find("input", attrs={"name": "specimen_id"})
        rows.append(
            {
                "specimen_id": kag_clean(detail_input.get("value") if detail_input else specimen_id),
                "family": kag_clean(cells[2].get_text(" ", strip=True)),
                "genus": kag_clean(cells[3].get_text(" ", strip=True)),
                "epithet": kag_clean(cells[4].get_text(" ", strip=True)),
                "type_kind": kag_clean(cells[5].get_text(" ", strip=True)),
                "collection_site": kag_clean(cells[6].get_text(" ", strip=True)),
                "japanese_name": kag_clean(cells[7].get_text(" ", strip=True)),
                "collector_name": kag_clean(cells[8].get_text(" ", strip=True)),
            }
        )
    return rows


def kag_table_values(html: str) -> dict[str, str]:
    return {key.lower(): value for key, value in tns_table_values(html).items()}


def kag_value(values: dict[str, str], *labels: str) -> str:
    for label in labels:
        value = values.get(label.lower(), "")
        if value:
            return value
    return ""


def kag_coordinates(*texts: object) -> tuple[str, str]:
    text = " ".join(kag_clean(item) for item in texts if kag_clean(item))
    lat = ""
    lon = ""
    lat_match = re.search(r"\b(?:lat|latitude)\s*[=:]?\s*([+-]?\d+(?:\.\d+)?)", text, re.IGNORECASE)
    lon_match = re.search(r"\b(?:lon|lng|longitude)\s*[=:]?\s*([+-]?\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if lat_match:
        lat = lat_match.group(1)
    if lon_match:
        lon = lon_match.group(1)
    return lat, lon


def kag_elevation(*texts: object) -> str:
    text = " ".join(kag_clean(item) for item in texts if kag_clean(item))
    match = re.search(r"\b(?:alt\.?|altitude|elev\.?)\s*[=:]?\s*([+-]?\d+(?:\.\d+)?)\s*(m|meter|meters)?\b", text, re.IGNORECASE)
    if not match:
        return ""
    unit = " m" if match.group(2) else ""
    return f"{match.group(1)}{unit}"


def kag_image_url(html: str, base_url: str, specimen_id: str) -> str:
    soup = soup_from_html(html)
    urls: list[str] = []
    for link in soup.find_all("a", href=True):
        href = value_to_str(link.get("href"))
        if "picture/" not in href.lower() or not re.search(r"\.(?:jpe?g|png)(?:$|\?)", href, re.IGNORECASE):
            continue
        urls.append(encoded_absolute_url(base_url, href))
    if not urls:
        urls = collect_image_candidates(html, base_url)
    preferred = [
        url
        for url in urls
        if not re.search(rf"/{re.escape(specimen_id)}P\.(?:jpe?g|png)(?:$|\?)", url, re.IGNORECASE)
    ]
    return best_image_url(preferred or urls)


def kag_record_from_detail(
    source: str,
    query_name: str,
    list_row: dict[str, str],
    detail_html: str,
    detail_url: str,
    settings: dict,
) -> SpecimenRecord:
    values = kag_table_values(detail_html)
    catalog = kag_value(values, "specimen_id") or list_row.get("specimen_id", "")
    collector_number = kag_value(values, "collector number")
    collector_date = kag_value(values, "collector date")
    collector_name = kag_value(values, "collector name") or list_row.get("collector_name", "")
    country = kag_value(values, "country")
    prefecture = kag_value(values, "prefecture")
    detail_locality = kag_value(values, "locality")
    note = kag_value(values, "note")
    list_locality = list_row.get("collection_site", "")
    genus = list_row.get("genus", "")
    epithet = list_row.get("epithet", "")
    scientific_name = " ".join(part for part in [genus, epithet] if part).strip() or query_name
    latitude, longitude = kag_coordinates(prefecture, detail_locality, note, list_locality)
    locality = " ".join(part for part in [prefecture, detail_locality] if part).strip() or list_locality
    image_url = kag_image_url(detail_html, detail_url, catalog)
    rights_note = str(
        settings.get(
            "rights_note",
            "KAG herbarium page states personal use is permitted; contact Kagoshima University Museum for broad redistribution or commercial use.",
        )
    )
    return SpecimenRecord(
        source=source,
        query_name=query_name,
        source_record_id=catalog,
        source_record_url=detail_url,
        occurrence_id=f"KAG:{catalog}" if catalog else "",
        institution_code="KAG",
        collection_code="KAG",
        catalog_number=catalog,
        scientific_name=scientific_name,
        recorded_by=collector_name,
        record_number=collector_number,
        event_date=tns_date(collector_date),
        country=country,
        state_province=prefecture,
        locality=locality,
        verbatim_locality=locality or list_locality,
        decimal_latitude=latitude,
        decimal_longitude=longitude,
        elevation=kag_elevation(note, detail_locality),
        type_status=list_row.get("type_kind", ""),
        basis_of_record="PRESERVED_SPECIMEN",
        coordinate_status=coordinate_status(latitude, longitude),
        image_url=image_url,
        original_image_url=image_url,
        image_license=str(settings.get("image_license", "rights_reserved")),
        rights_holder=str(settings.get("rights_holder", "The Kagoshima University Museum")),
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
        notes=f"metadata_from_kag_database; {rights_note}",
    )


def kag_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    endpoint = str(settings.get("search_url", "https://dbs.kaum.kagoshima-u.ac.jp/musedb/s_plant/s_plant.php"))
    page_size = min(int(settings.get("page_size", 50)), 100)
    max_pages = int(settings.get("max_pages_per_name", 10))
    delay = float(settings.get("request_delay_seconds", 2.0))
    headers = {"Referer": endpoint}
    records: list[SpecimenRecord] = []
    offset = record_offset

    for page in range(1, max_pages + 1):
        payload = kag_search_payload(query_name, offset, page_size)
        cache_path = raw_dir / safe_token(query_name) / f"offset_{offset:07d}.html"
        from_cache = cache_path.exists() and not refresh
        if from_cache:
            html = cache_path.read_text(encoding="utf-8")
        else:
            html = client.post_text(endpoint, data=payload, headers=headers)
            save_raw_text(cache_path, html)
        rows = kag_list_rows(html)
        if not rows:
            break
        for row in rows:
            if max_records is not None and len(records) >= max_records:
                return records
            scientific_name = " ".join(part for part in [row.get("genus", ""), row.get("epithet", "")] if part).strip()
            if bool(settings.get("exact_name_filter", True)) and not query_name_matches(query_name, [scientific_name]):
                continue
            catalog = row.get("specimen_id", "")
            if not catalog:
                continue
            detail_payload = dict(payload)
            detail_payload.update({"opt": "view_collect", "specimen_id": catalog})
            detail_cache = raw_dir / safe_token(query_name) / "records" / f"{safe_token(catalog)}.html"
            if detail_cache.exists() and not refresh:
                detail_html = detail_cache.read_text(encoding="utf-8")
            else:
                detail_html = client.post_text(endpoint, data=detail_payload, headers=headers)
                save_raw_text(detail_cache, detail_html)
                client.sleep(delay)
            records.append(kag_record_from_detail(source, query_name, row, detail_html, endpoint, settings))
        total = kag_total_records(html)
        offset += len(rows)
        if len(rows) < page_size or (total and offset >= total):
            break
        if not from_cache:
            client.sleep(delay)
    return records


def taif_field_value(html: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        value = table_row_value(html, label)
        if value:
            return value
    soup = soup_from_html(html)
    normalized = {re.sub(r"\s+", " ", label).strip().lower().rstrip(".:") for label in labels}
    for row in soup.find_all(class_="pure-g"):
        cells = row.find_all("div", recursive=False)
        if len(cells) < 2:
            continue
        left = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip().lower().rstrip(".:")
        if left not in normalized:
            continue
        value = re.sub(r"\s+", " ", cells[1].get_text(" ", strip=True)).strip()
        if value and value != "(n/a)":
            return value
    return ""


def taif_tile_url(html: str, base_url: str) -> str:
    match = re.search(
        r"createViewer\(\s*[^,]+,\s*'[^']+'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(\d+)\s*,\s*(\d+)",
        html,
    )
    if not match:
        return ""
    tile_base, prefix, width, height = match.groups()
    return "taif_tiles://image?" + urlencode(
        {
            "base": urljoin(base_url, tile_base),
            "prefix": prefix,
            "width": width,
            "height": height,
            "tile": "512",
        }
    )


def split_binomial(query_name: str) -> tuple[str, str]:
    parts = query_name.split()
    if len(parts) < 2:
        return query_name, ""
    return parts[0], parts[1]


def normalized_words(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value_to_str(value).lower()).strip()


def query_name_matches(query_name: str, candidates: list[object]) -> bool:
    genus, species = split_binomial(query_name)
    if not genus or not species:
        return True
    expected = normalized_words(f"{genus} {species}")
    for candidate in candidates:
        text = normalized_words(candidate)
        if text == expected or text.startswith(expected + " ") or expected in text:
            return True
    return False


def normalized_code(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value_to_str(value).upper())


def institution_code_variants(*values: object) -> set[str]:
    variants: set[str] = set()
    for value in values:
        text = value_to_str(value).strip()
        if not text:
            continue
        variants.add(normalized_code(text))
        for part in re.split(r"[:/;,()\s-]+", text):
            code = normalized_code(part)
            if code:
                variants.add(code)
    return {variant for variant in variants if variant}


def institution_allowed(settings: dict, *values: object) -> bool:
    configured = settings.get("institution_codes") or []
    if isinstance(configured, str):
        configured = [configured]
    allowed: set[str] = set()
    for code in configured:
        allowed.update(institution_code_variants(code))
    if not allowed:
        return True
    observed = institution_code_variants(*values)
    return bool(allowed.intersection(observed))


def join_values(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(value_to_str(item) for item in value if value_to_str(item))
    return value_to_str(value)


def event_date_from_parts(year: object, month: object, day: object) -> str:
    y = value_to_str(year).strip()
    if not y:
        return ""
    m = value_to_str(month).strip()
    d = value_to_str(day).strip()
    if m and d:
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    if m:
        return f"{y}-{m.zfill(2)}"
    return y


def event_date_from_any(primary: object, year: object, month: object, day: object) -> str:
    text = value_to_str(primary).strip()
    if text and not re.fullmatch(r"-?\d{6,}", text):
        return text
    from_parts = event_date_from_parts(year, month, day)
    if from_parts:
        return from_parts
    if re.fullmatch(r"-?\d{6,}", text):
        try:
            return datetime.fromtimestamp(int(text) / 1000, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return text
    return ""


def coordinate_status(latitude: object, longitude: object, issue: object = False) -> str:
    if value_to_str(latitude) and value_to_str(longitude):
        return "coordinates_geospatial_issue" if str(issue).lower() in {"true", "1", "yes"} else "coordinates_ok"
    return "missing_coordinates"


def image_candidate_score(url: str) -> tuple[int, int]:
    lower = url.lower()
    size = 0
    if "/full/full/" in lower or "original" in lower:
        size = 5000
    elif "large" in lower or re.search(r"\bfs\b|_fs\.", lower):
        size = 4000
    elif "!1920" in lower or "2400" in lower:
        size = 3000
    elif "_web." in lower or "medium" in lower:
        size = 2000
    elif "_tn." in lower or "thumbnail" in lower or "small" in lower:
        size = 100
    elif image_like_url(url):
        size = 1000
    return (size, len(url))


def best_image_url(urls: list[str]) -> str:
    image_urls = [url for url in urls if image_like_url(url)]
    if not image_urls:
        return ""
    return max(dict.fromkeys(image_urls), key=image_candidate_score)


def encoded_absolute_url(base_url: str, raw_url: object) -> str:
    raw = value_to_str(raw_url)
    if not raw:
        return ""
    parsed = urlparse(urljoin(base_url, raw))
    return parsed._replace(path=quote(parsed.path, safe="/%")).geturl()


def creative_commons_license(html: str) -> str:
    match = re.search(r"https?://creativecommons\.org/[^\s\"'<>]+", html, re.IGNORECASE)
    return match.group(0).rstrip(".,;") if match else ""


def gbif_family_for_name(client: PoliteHttpClient, query_name: str, raw_dir: Path, refresh: bool) -> str:
    cache_path = raw_dir / safe_token(query_name) / "gbif_species_match.json"
    if cache_path.exists() and not refresh:
        import json
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        data = client.get_json("https://api.gbif.org/v1/species/match", params={"name": query_name})
        save_raw_json(cache_path, data)
    return value_to_str(data.get("family"))


def taif_record_from_detail(
    html: str,
    url: str,
    query_name: str,
    *,
    default_type_status: str = "",
) -> SpecimenRecord:
    soup = soup_from_html(html)
    scientific_name = soup.find(id="spnSPName")
    scientific_name_text = scientific_name.get_text(" ", strip=True) if scientific_name else taif_field_value(
        html,
        ("Scientific Name",),
    )
    catalog = taif_field_value(html, ("Herbarium No.", "Herbarium no."))
    locality = taif_field_value(html, ("Location", "Locality"))
    image_url = taif_tile_url(html, url)
    if not image_url:
        candidates = collect_image_candidates(html, url)
        image_url = candidates[0] if candidates else ""
    type_status = taif_field_value(html, ("Type Status",)) or default_type_status
    return SpecimenRecord(
        source="taif",
        query_name=query_name,
        source_record_id=safe_token(catalog or urlparse(url).query or "taif_record"),
        source_record_url=url,
        institution_code="TAIF",
        catalog_number=catalog,
        scientific_name=scientific_name_text,
        recorded_by=taif_field_value(html, ("Collector(s)",)),
        record_number=taif_field_value(html, ("Collection No.", "Coll. no.")),
        event_date=taif_field_value(html, ("Collection Date", "Collecting Date")),
        locality=locality,
        verbatim_locality=locality,
        elevation=taif_field_value(html, ("Altitude",)),
        type_status=type_status,
        basis_of_record="PRESERVED_SPECIMEN",
        coordinate_status="missing_coordinates",
        image_url=image_url,
        original_image_url=image_url,
        rights_holder="Herbarium of Taiwan Forestry Research Institute",
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
    )


def taif_records(
    client: PoliteHttpClient,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    type_search_template = str(
        settings.get("type_search_url", "https://taif.tfri.gov.tw/search/type/?keyword={query}&qf=keyword&l=Eng")
    )
    normal_search_template = str(
        settings.get("normal_search_url", "https://taif.tfri.gov.tw/search/result.php?genus={genus}&species={species}&l=Eng&ol=1")
    )
    record_pattern = re.compile(str(settings.get("record_link_pattern", r"(?:type/)?specimen\.php\?[^\"'<> ]+")), re.IGNORECASE)
    delay = float(settings.get("request_delay_seconds", 3.0))
    verify_tls = bool(settings.get("verify_tls", False))
    genus, species = split_binomial(query_name)
    search_specs = [
        ("normal", normal_search_template.format(genus=quote_plus(genus), species=quote_plus(species), query=quote_plus(query_name))),
        ("type", type_search_template.format(query=quote_plus(query_name), genus=quote_plus(genus), species=quote_plus(species))),
    ]
    record_urls: list[str] = []
    for search_name, search_url in search_specs:
        cache_path = raw_dir / safe_token(query_name) / f"search_{search_name}.html"
        if cache_path.exists() and not refresh:
            html = cache_path.read_text(encoding="utf-8")
            final_url = search_url
        else:
            html, final_url = client.get_text_with_url(search_url, verify=verify_tls)
            save_raw_text(cache_path, html)
        record_urls.extend(collect_links(html, final_url, [record_pattern]))
        client.sleep(delay)
    record_urls = sorted(dict.fromkeys(record_urls))
    selected_urls = sorted(record_urls)[record_offset:]
    if max_records is not None:
        selected_urls = selected_urls[:max_records]

    records: list[SpecimenRecord] = []
    for index, url in enumerate(selected_urls, start=record_offset + 1):
        record_cache = raw_dir / safe_token(query_name) / "records" / f"record_{index:05d}.html"
        if record_cache.exists() and not refresh:
            record_html = record_cache.read_text(encoding="utf-8")
        else:
            record_html = client.get_text(url, verify=verify_tls)
            save_raw_text(record_cache, record_html)
        records.append(taif_record_from_detail(record_html, url, query_name, default_type_status="Type" if "/type/" in url else ""))
        client.sleep(delay)
    return records


def tai2_date(value: object) -> str:
    text = value_to_str(value).strip()
    match = re.fullmatch(r"(\d{4})/(\d{1,2})(?:/(\d{1,2}))?", text)
    if not match:
        return text
    year, month, day = match.groups()
    if day:
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return f"{year}-{month.zfill(2)}"


def tai2_spcm_rows(html: str) -> list[dict[str, object]]:
    match = re.search(r"\bvar\s+spcm\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not match:
        return []
    data = json.loads(match.group(1))
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def tai2_page_urls(settings: dict, query_name: str) -> list[str]:
    urls: list[str] = []
    pages = settings.get("species_pages") or {}
    if isinstance(pages, dict):
        query_key = normalized_words(query_name)
        for name, configured_urls in pages.items():
            if normalized_words(name) != query_key:
                continue
            if isinstance(configured_urls, str):
                urls.append(configured_urls)
            elif isinstance(configured_urls, list):
                urls.extend(value_to_str(url) for url in configured_urls if value_to_str(url))
    generic_pages = settings.get("search_pages") or []
    if isinstance(generic_pages, str):
        urls.append(generic_pages)
    elif isinstance(generic_pages, list):
        urls.extend(value_to_str(url) for url in generic_pages if value_to_str(url))
    return list(dict.fromkeys(urls))


def tai2_equivalent_names(settings: dict, query_name: str) -> list[str]:
    equivalents = [query_name]
    configured = settings.get("name_equivalents") or {}
    if isinstance(configured, dict):
        query_key = normalized_words(query_name)
        for name, names in configured.items():
            if normalized_words(name) != query_key:
                continue
            if isinstance(names, str):
                equivalents.append(names)
            elif isinstance(names, list):
                equivalents.extend(value_to_str(item) for item in names if value_to_str(item))
    return list(dict.fromkeys(equivalents))


def tai2_row_matches(query_name: str, row: dict[str, object], settings: dict) -> bool:
    if not bool(settings.get("exact_name_filter", True)):
        return True
    candidates: list[object] = [row.get("species")]
    correct_info = row.get("correctinfo")
    if isinstance(correct_info, dict):
        candidates.extend(correct_info.values())
    elif correct_info:
        candidates.append(correct_info)
    return any(query_name_matches(name, candidates) for name in tai2_equivalent_names(settings, query_name))


def tai2_elevation_from_note(note: object) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)\s*m\b", value_to_str(note), re.IGNORECASE)
    return f"{match.group(1)} m" if match else ""


def tai2_record_from_row(source: str, query_name: str, page_url: str, row: dict[str, object]) -> SpecimenRecord:
    tai_id = value_to_str(row.get("TAIID"))
    locinfo = row.get("locinfo") if isinstance(row.get("locinfo"), dict) else {}
    detinfo = row.get("detinfo") if isinstance(row.get("detinfo"), dict) else {}
    raw_image_url = value_to_str(row.get("img") or row.get("label") or row.get("imgsmall"))
    image_url = encoded_absolute_url(page_url, raw_image_url)
    label_url = encoded_absolute_url(page_url, row.get("label"))
    note = value_to_str(row.get("note"))
    notes = note
    if label_url:
        notes = "; ".join(part for part in [notes, f"label_image={label_url}"] if part)
    return SpecimenRecord(
        source=source,
        query_name=query_name,
        source_record_id=tai_id,
        source_record_url=f"{page_url}#TAI-{tai_id}" if tai_id else page_url,
        occurrence_id=f"TAI:{tai_id}" if tai_id else "",
        institution_code="TAI",
        catalog_number=tai_id,
        scientific_name=value_to_str(row.get("species")),
        recorded_by=value_to_str(row.get("collinfo")),
        record_number=value_to_str(row.get("collno")),
        event_date=tai2_date(row.get("date")),
        country=value_to_str(locinfo.get("country")) if isinstance(locinfo, dict) else "",
        state_province=value_to_str(locinfo.get("district")) if isinstance(locinfo, dict) else "",
        locality=value_to_str(locinfo.get("locE") or locinfo.get("loc")) if isinstance(locinfo, dict) else "",
        verbatim_locality=value_to_str(locinfo.get("loc")) if isinstance(locinfo, dict) else "",
        decimal_latitude=value_to_str(locinfo.get("Y")) if isinstance(locinfo, dict) else "",
        decimal_longitude=value_to_str(locinfo.get("X")) if isinstance(locinfo, dict) else "",
        elevation=tai2_elevation_from_note(note),
        identified_by=value_to_str(detinfo.get("collinfo")) if isinstance(detinfo, dict) else "",
        type_status=value_to_str(row.get("type")),
        basis_of_record="PRESERVED_SPECIMEN",
        coordinate_status=coordinate_status(
            locinfo.get("Y") if isinstance(locinfo, dict) else "",
            locinfo.get("X") if isinstance(locinfo, dict) else "",
        ),
        image_url=image_url,
        original_image_url=image_url,
        rights_holder="National Taiwan University Herbarium (TAI)",
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
        notes=notes,
    )


def tai2_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    delay = float(settings.get("request_delay_seconds", 2.0))
    verify_tls = bool(settings.get("verify_tls", True))
    page_urls = tai2_page_urls(settings, query_name)
    records: list[SpecimenRecord] = []
    for page_index, page_url in enumerate(page_urls, start=1):
        cache_path = raw_dir / safe_token(query_name) / f"species_page_{page_index:03d}.html"
        if cache_path.exists() and not refresh:
            html = cache_path.read_text(encoding="utf-8")
        else:
            html = client.get_text(page_url, verify=verify_tls)
            save_raw_text(cache_path, html)
        rows = tai2_spcm_rows(html)
        save_raw_json(raw_dir / safe_token(query_name) / f"species_page_{page_index:03d}_spcm.json", rows)
        for row in rows:
            if not tai2_row_matches(query_name, row, settings):
                continue
            records.append(tai2_record_from_row(source, query_name, page_url, row))
        client.sleep(delay)

    selected = records[record_offset:]
    if max_records is not None:
        selected = selected[:max_records]
    return selected


def symbiota_occurrence_ids(html: str) -> list[str]:
    found: set[str] = set()
    for pattern in (
        r"openIndPU\(\s*(\d+)",
        r"name=[\"']occid\[\][\"'][^>]+value=[\"'](\d+)",
        r"individual/index\.php\?occid=(\d+)",
        r"[?&]occid=(\d+)",
    ):
        for match in re.finditer(pattern, html, re.IGNORECASE):
            found.add(match.group(1))
    return sorted(found, key=lambda value: int(value))


def symbiota_record_from_data(
    source: str,
    query_name: str,
    occurrence_id: str,
    data: dict,
    detail_url: str,
    detail_html: str,
    settings: dict,
) -> SpecimenRecord | None:
    if not symbiota_data_allowed(query_name, data, settings):
        return None
    catalog = value_to_str(data.get("catalogNumber"))
    institution = value_to_str(data.get("institutionCode") or data.get("ownerInstitutionCode"))
    collection = value_to_str(data.get("collectionCode"))
    image_url = best_image_url(collect_image_candidates(detail_html, detail_url))
    locality_parts = [
        value_to_str(data.get("country")),
        value_to_str(data.get("stateProvince")),
        value_to_str(data.get("county")),
        value_to_str(data.get("locality")),
    ]
    locality = " ".join(part for part in locality_parts if part).strip()
    return SpecimenRecord(
        source=source,
        query_name=query_name,
        source_record_id=occurrence_id,
        source_record_url=detail_url,
        occurrence_id=value_to_str(data.get("occurrenceID")),
        institution_code=institution,
        collection_code=collection,
        catalog_number=catalog,
        scientific_name=value_to_str(data.get("sciname") or data.get("scientificName")),
        recorded_by=value_to_str(data.get("recordedBy")),
        record_number=value_to_str(data.get("recordNumber") or data.get("fieldNumber")),
        event_date=value_to_str(data.get("eventDate")) or event_date_from_parts(data.get("year"), data.get("month"), data.get("day")),
        country=value_to_str(data.get("country")),
        state_province=value_to_str(data.get("stateProvince")),
        locality=locality,
        verbatim_locality=locality,
        decimal_latitude=value_to_str(data.get("decimalLatitude")),
        decimal_longitude=value_to_str(data.get("decimalLongitude")),
        elevation=value_to_str(data.get("verbatimElevation") or data.get("minimumElevationInMeters")),
        identified_by=value_to_str(data.get("identifiedBy")),
        type_status=value_to_str(data.get("typeStatus")),
        basis_of_record=value_to_str(data.get("basisOfRecord") or "PRESERVED_SPECIMEN"),
        coordinate_status=coordinate_status(data.get("decimalLatitude"), data.get("decimalLongitude")),
        image_url=image_url,
        original_image_url=image_url,
        image_license=creative_commons_license(detail_html),
        rights_holder=text_value(soup_from_html(detail_html), ("Rights Holder", "Rights holder")),
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
    )


def symbiota_data_allowed(
    query_name: str,
    data: dict,
    settings: dict,
) -> bool:
    if bool(settings.get("specimens_only", True)):
        basis = value_to_str(data.get("basisOfRecord"))
        if basis and "observation" in basis.lower():
            return False
    if bool(settings.get("exact_name_filter", True)) and not query_name_matches(
        query_name,
        [data.get("sciname"), data.get("scientificName"), data.get("taxoncompleto")],
    ):
        return False
    institution = value_to_str(data.get("institutionCode") or data.get("ownerInstitutionCode"))
    collection = value_to_str(data.get("collectionCode"))
    if not institution_allowed(settings, institution, collection, data.get("ownerInstitutionCode")):
        return False
    return True


def symbiota_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    base_url = str(settings["base_url"]).rstrip("/")
    search_template = str(
        settings.get(
            "search_url",
            base_url + "/collections/list.php?db=all&taxa={query}&usethes=1&taxontype=2&comingFrom=newsearch&page={page}",
        )
    )
    api_template = str(settings.get("api_url", base_url + "/api/v2/occurrence/{occid}"))
    detail_template = str(settings.get("detail_url", base_url + "/collections/individual/index.php?occid={occid}"))
    max_pages = int(settings.get("max_pages_per_name", 2))
    delay = float(settings.get("request_delay_seconds", 2.0))
    verify_tls = bool(settings.get("verify_tls", True))
    shared_cache = str(settings.get("_shared_cache_root", "")).strip()
    cache_root = Path(shared_cache) if shared_cache else raw_dir
    use_cache = bool(shared_cache) or not refresh
    host_interval = getattr(client, "host_interval", None)
    host_paced = (
        callable(host_interval)
        and float(host_interval(base_url)) > 0
    )
    occurrence_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        search_url = search_template.format(query=quote_plus(query_name), page=page)
        cache_path = (
            cache_root
            / "search"
            / safe_token(query_name)
            / f"search_{page:04d}.html"
        )
        from_cache = cache_path.exists() and use_cache
        if from_cache:
            html = cache_path.read_text(encoding="utf-8")
        else:
            html = client.get_text(search_url, verify=verify_tls)
            save_raw_text(cache_path, html)
        before = len(occurrence_ids)
        occurrence_ids.update(symbiota_occurrence_ids(html))
        if len(occurrence_ids) == before and page > 1:
            break
        if not from_cache and not host_paced:
            client.sleep(delay)

    selected_ids = sorted(occurrence_ids, key=lambda value: int(value))[record_offset:]

    records: list[SpecimenRecord] = []
    for occurrence_id in selected_ids:
        if max_records is not None and len(records) >= max_records:
            break
        api_url = api_template.format(occid=occurrence_id)
        detail_url = detail_template.format(occid=occurrence_id)
        api_cache = cache_root / "records" / f"{occurrence_id}.json"
        html_cache = cache_root / "records" / f"{occurrence_id}.html"
        api_from_cache = api_cache.exists() and use_cache
        if api_from_cache:
            import json
            api_data = json.loads(api_cache.read_text(encoding="utf-8"))
        else:
            api_data = client.get_json(api_url, verify=verify_tls)
            save_raw_json(api_cache, api_data)
        row = api_data[0] if isinstance(api_data, list) and api_data else {}
        if not isinstance(row, dict) or not symbiota_data_allowed(
            query_name,
            row,
            settings,
        ):
            if not api_from_cache and not host_paced:
                client.sleep(delay)
            continue
        html_from_cache = html_cache.exists() and use_cache
        if html_from_cache:
            detail_html = html_cache.read_text(encoding="utf-8")
        else:
            detail_html = client.get_text(detail_url, verify=verify_tls)
            save_raw_text(html_cache, detail_html)
        record = symbiota_record_from_data(
            source,
            query_name,
            occurrence_id,
            row,
            detail_url,
            detail_html,
            settings,
        )
        if record is not None:
            records.append(record)
        if (not api_from_cache or not html_from_cache) and not host_paced:
            client.sleep(delay)
    return records


def ala_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    endpoint = str(settings.get("api_url", "https://api.ala.org.au/occurrences/occurrences/search"))
    record_url_template = str(settings.get("record_url", "https://avh.ala.org.au/occurrences/{uuid}"))
    limit = min(int(settings.get("page_size", 100)), 100)
    max_pages = int(settings.get("max_pages_per_name", 10))
    delay = float(settings.get("request_delay_seconds", 1.5))
    records: list[SpecimenRecord] = []
    offset = record_offset
    for page_number in range(1, max_pages + 1):
        params: dict[str, object] = {
            "q": f'"{query_name}"',
            "pageSize": limit,
            "startIndex": offset,
        }
        facets = settings.get("fq") or []
        if facets:
            params["fq"] = facets
        cache_path = raw_dir / safe_token(query_name) / f"offset_{offset:07d}.json"
        from_cache = cache_path.exists() and not refresh
        if from_cache:
            import json
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            data = client.get_json(endpoint, params=params)
            save_raw_json(cache_path, data)
        rows = data.get("occurrences", [])
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if max_records is not None and len(records) >= max_records:
                return records
            if not isinstance(row, dict):
                continue
            if bool(settings.get("exact_name_filter", True)) and not query_name_matches(
                query_name,
                [row.get("raw_scientificName"), row.get("scientificName"), row.get("species")],
            ):
                continue
            if bool(settings.get("specimens_only", True)):
                basis = value_to_str(row.get("basisOfRecord") or row.get("raw_basisOfRecord"))
                if basis and "specimen" not in basis.lower() and "preserved" not in basis.lower():
                    continue
            uuid = value_to_str(row.get("uuid"))
            image_urls = row.get("imageUrls") if isinstance(row.get("imageUrls"), list) else []
            image_url = best_image_url(
                [
                    value_to_str(row.get("largeImageUrl")),
                    *[value_to_str(item) for item in image_urls],
                    value_to_str(row.get("imageUrl")),
                    value_to_str(row.get("thumbnailUrl")),
                ]
            )
            records.append(
                SpecimenRecord(
                    source=source,
                    query_name=query_name,
                    source_record_id=uuid,
                    source_record_url=record_url_template.format(uuid=uuid),
                    occurrence_id=value_to_str(row.get("occurrenceID")),
                    institution_code=value_to_str(row.get("raw_institutionCode") or row.get("institutionCode")),
                    collection_code=value_to_str(row.get("raw_collectionCode") or row.get("collectionCode")),
                    catalog_number=value_to_str(row.get("raw_catalogNumber") or row.get("catalogNumber")),
                    scientific_name=value_to_str(row.get("raw_scientificName") or row.get("scientificName")),
                    recorded_by=join_values(row.get("recordedBy") or row.get("collectors") or row.get("collector")),
                    record_number=value_to_str(row.get("recordNumber")),
                    event_date=event_date_from_any(
                        row.get("raw_eventDate") or row.get("eventDate"),
                        row.get("year"),
                        row.get("month"),
                        row.get("day"),
                    ),
                    country=value_to_str(row.get("country")),
                    state_province=value_to_str(row.get("stateProvince")),
                    locality=value_to_str(row.get("locality")),
                    verbatim_locality=value_to_str(row.get("raw_verbatimLocality") or row.get("locality")),
                    decimal_latitude=value_to_str(row.get("decimalLatitude")),
                    decimal_longitude=value_to_str(row.get("decimalLongitude")),
                    elevation=value_to_str(row.get("verbatimElevation") or row.get("raw_verbatimElevation")),
                    type_status=value_to_str(row.get("typeStatus") or row.get("raw_typeStatus")),
                    basis_of_record=value_to_str(row.get("basisOfRecord") or row.get("raw_basisOfRecord")),
                    coordinate_status=coordinate_status(
                        row.get("decimalLatitude"),
                        row.get("decimalLongitude"),
                        str(row.get("geospatialKosher")).lower() == "false",
                    ),
                    image_url=image_url,
                    original_image_url=image_url,
                    image_license=value_to_str(row.get("license")),
                    rights_holder=value_to_str(row.get("rightsHolder") or row.get("institutionName")),
                    accessed_at=now_iso(),
                    download_status="pending" if image_url else "no_image_url",
                )
            )
        offset += len(rows)
        total = int(data.get("totalRecords") or 0)
        if len(rows) < limit or (total and offset >= total):
            break
        if not from_cache:
            client.sleep(delay)
    return records


def jabot_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    genus, species = split_binomial(query_name)
    family = value_to_str(settings.get("family")) or gbif_family_for_name(client, query_name, raw_dir, refresh)
    if not (genus and species and family):
        return []
    endpoint = str(settings.get("api_url", "https://servicos.jbrj.gov.br/v2/jabot/occurrence/{species}/{genus}/{family}"))
    url = endpoint.format(species=quote_plus(species), genus=quote_plus(genus), family=quote_plus(family))
    cache_path = raw_dir / safe_token(query_name) / "occurrences.json"
    if cache_path.exists() and not refresh:
        import json
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        data = client.get_json(url)
        save_raw_json(cache_path, data)
    if not isinstance(data, list):
        return []
    rows = data[record_offset:]
    if max_records is not None:
        rows = rows[:max_records]
    records: list[SpecimenRecord] = []
    record_url_template = str(
        settings.get(
            "record_url",
            "https://reflora.jbrj.gov.br/reflora/listaBrasil/ForwardAction.do?applicationName=reflora&modulo=herbarioVirtual&path=%2FConsultaPublicoHVUC%2FConsultaPublicoHVUC.do%3FidTestemunho%3D{codtestemunho}",
        )
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        codtestemunho = value_to_str(row.get("codtestemunho") or row.get("codespecime"))
        records.append(
            SpecimenRecord(
                source=source,
                query_name=query_name,
                source_record_id=codtestemunho,
                source_record_url=record_url_template.format(codtestemunho=codtestemunho),
                institution_code=value_to_str(row.get("siglacolecao")),
                catalog_number=value_to_str(row.get("numtombo")),
                scientific_name=value_to_str(row.get("taxoncompleto")),
                recorded_by=value_to_str(row.get("coletor")),
                record_number=value_to_str(row.get("numcoleta")),
                event_date=event_date_from_parts(row.get("anocoleta"), row.get("mescoleta"), row.get("diacoleta")),
                country=value_to_str(row.get("pais")),
                state_province=value_to_str(row.get("estado_prov")),
                locality=value_to_str(row.get("descrlocal")),
                verbatim_locality=value_to_str(row.get("descrlocal")),
                decimal_latitude=value_to_str(row.get("latitude")),
                decimal_longitude=value_to_str(row.get("longitude")),
                elevation=" ".join(part for part in [value_to_str(row.get("altitude")), value_to_str(row.get("siglaunidmed"))] if part),
                identified_by=value_to_str(row.get("determinador")),
                type_status=value_to_str(row.get("nat_typus")),
                basis_of_record="PRESERVED_SPECIMEN",
                coordinate_status=coordinate_status(row.get("latitude"), row.get("longitude")),
                accessed_at=now_iso(),
                download_status="no_image_url",
                notes="metadata_from_jabot_api; image_endpoint_not_public_in_api",
            )
        )
    return records


def nhm_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    endpoint = str(settings.get("api_url", "https://data.nhm.ac.uk/api/3/action/datastore_search"))
    resource_id = str(settings.get("resource_id", "05ff2255-c38a-40c9-b657-4ccb55ab2feb"))
    record_url_template = str(settings.get("record_url", "https://data.nhm.ac.uk/object/{id}"))
    limit = min(int(settings.get("page_size", 100)), 100)
    max_pages = int(settings.get("max_pages_per_name", 20))
    delay = float(settings.get("request_delay_seconds", 1.5))
    records: list[SpecimenRecord] = []
    offset = record_offset
    for page_number in range(1, max_pages + 1):
        cache_path = raw_dir / safe_token(query_name) / f"offset_{offset:07d}.json"
        params = {"resource_id": resource_id, "q": query_name, "limit": limit, "offset": offset}
        from_cache = cache_path.exists() and not refresh
        if from_cache:
            import json
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            data = client.get_json(endpoint, params=params)
            save_raw_json(cache_path, data)
        result = data.get("result", {}) if isinstance(data, dict) else {}
        rows = result.get("records", []) if isinstance(result, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if max_records is not None and len(records) >= max_records:
                return records
            if not isinstance(row, dict):
                continue
            if bool(settings.get("exact_name_filter", True)) and not query_name_matches(
                query_name,
                [row.get("scientificName"), row.get("determinationNames")],
            ):
                continue
            media_items = row.get("associatedMedia") if isinstance(row.get("associatedMedia"), list) else []
            media_dicts = [item for item in media_items if isinstance(item, dict)]
            media = max(
                media_dicts,
                key=lambda item: image_candidate_score(value_to_str(item.get("identifier"))),
                default={},
            )
            image_url = value_to_str(media.get("identifier")) if isinstance(media, dict) else ""
            records.append(
                SpecimenRecord(
                    source=source,
                    query_name=query_name,
                    source_record_id=value_to_str(row.get("_id") or row.get("occurrenceID")),
                    source_record_url=record_url_template.format(id=value_to_str(row.get("_id") or row.get("occurrenceID"))),
                    occurrence_id=value_to_str(row.get("occurrenceID")),
                    institution_code=value_to_str(row.get("institutionCode")),
                    collection_code=value_to_str(row.get("collectionCode")),
                    catalog_number=value_to_str(row.get("catalogNumber")),
                    scientific_name=value_to_str(row.get("scientificName")),
                    recorded_by=join_values(row.get("recordedBy")),
                    record_number=value_to_str(row.get("recordNumber") or row.get("fieldNumber")),
                    event_date=value_to_str(row.get("eventDate")),
                    country=value_to_str(row.get("country")),
                    state_province=value_to_str(row.get("stateProvince")),
                    locality=value_to_str(row.get("locality")),
                    verbatim_locality=value_to_str(row.get("locality")),
                    decimal_latitude=value_to_str(row.get("decimalLatitude")),
                    decimal_longitude=value_to_str(row.get("decimalLongitude")),
                    elevation=value_to_str(row.get("verbatimElevation")),
                    identified_by=join_values(row.get("determinationNames")),
                    type_status=value_to_str(row.get("typeStatus")),
                    basis_of_record=value_to_str(row.get("basisOfRecord")),
                    coordinate_status=coordinate_status(row.get("decimalLatitude"), row.get("decimalLongitude")),
                    image_url=image_url,
                    original_image_url=image_url,
                    image_license=value_to_str(media.get("license")) if isinstance(media, dict) else "",
                    rights_holder=value_to_str(media.get("rightsHolder")) if isinstance(media, dict) else "",
                    accessed_at=now_iso(),
                    download_status="pending" if image_url else "no_image_url",
                )
            )
        offset += len(rows)
        total = int(result.get("total") or 0) if isinstance(result, dict) else 0
        if len(rows) < limit or (total and offset >= total):
            break
        if not from_cache:
            client.sleep(delay)
    return records


def dwca_read_table(zf: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    if filename not in zf.namelist():
        return []
    with zf.open(filename) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8", newline="")
        return [
            {key: value for key, value in row.items() if key is not None}
            for row in csv.DictReader(text, delimiter="\t")
        ]


def dwca_media_by_id(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        record_id = value_to_str(row.get("id") or row.get("coreid") or row.get("occurrenceID")).strip()
        if not record_id:
            continue
        grouped.setdefault(record_id, []).append(row)
    return grouped


def dwca_row_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = value_to_str(row.get(key)).strip()
        if value:
            return value
    return ""


def dwca_media_url(row: dict[str, str]) -> str:
    return best_image_url(
        [
            dwca_row_value(row, "identifier", "associatedMedia", "accessURI", "references"),
        ]
    )


def dwca_record_from_row(
    source: str,
    query_name: str,
    row: dict[str, str],
    media_rows: list[dict[str, str]],
    archive: dict[str, object],
    settings: dict,
) -> SpecimenRecord:
    record_id = dwca_row_value(row, "id", "occurrenceID")
    occurrence_id = dwca_row_value(row, "occurrenceID") or record_id
    institution = (
        dwca_row_value(row, "institutionCode")
        or value_to_str(archive.get("institution_code"))
        or value_to_str(settings.get("institution_code"))
    )
    collection = dwca_row_value(row, "collectionCode") or institution
    catalog = dwca_row_value(row, "catalogNumber")
    if not catalog and occurrence_id.startswith(f"{institution}:"):
        catalog = occurrence_id
    catalog = catalog or occurrence_id or record_id
    image_url = best_image_url(
        [
            dwca_row_value(row, "associatedMedia"),
            *[dwca_media_url(media_row) for media_row in media_rows],
        ]
    )
    media = next((media_row for media_row in media_rows if dwca_media_url(media_row) == image_url), media_rows[0] if media_rows else {})
    source_url_template = value_to_str(archive.get("record_url_template") or settings.get("record_url_template"))
    source_url = source_url_template.format(
        id=quote_plus(record_id),
        occurrenceID=quote_plus(occurrence_id),
        catalogNumber=quote_plus(catalog),
    ) if source_url_template else value_to_str(archive.get("dataset_url") or archive.get("archive_url"))
    locality = dwca_row_value(row, "locality")
    verbatim = dwca_row_value(row, "verbatimLocality") or locality
    latitude = dwca_row_value(row, "decimalLatitude")
    longitude = dwca_row_value(row, "decimalLongitude")
    return SpecimenRecord(
        source=source,
        query_name=query_name,
        source_record_id=record_id or catalog,
        source_record_url=source_url,
        occurrence_id=occurrence_id,
        institution_code=institution,
        collection_code=collection,
        catalog_number=catalog,
        scientific_name=dwca_row_value(row, "scientificName"),
        recorded_by=dwca_row_value(row, "recordedBy"),
        record_number=dwca_row_value(row, "recordNumber"),
        event_date=dwca_row_value(row, "eventDate"),
        country=dwca_row_value(row, "country"),
        state_province=dwca_row_value(row, "stateProvince", "island"),
        locality=locality,
        verbatim_locality=verbatim,
        decimal_latitude=latitude,
        decimal_longitude=longitude,
        elevation=dwca_row_value(row, "verbatimElevation", "minimumElevationInMeters"),
        identified_by=dwca_row_value(row, "identifiedBy"),
        type_status=dwca_row_value(row, "type", "typeStatus"),
        basis_of_record=dwca_row_value(row, "basisOfRecord") or "PRESERVED_SPECIMEN",
        coordinate_status=coordinate_status(latitude, longitude),
        image_url=image_url,
        original_image_url=image_url,
        image_license=dwca_row_value(media, "license") or value_to_str(archive.get("image_license") or settings.get("image_license")),
        rights_holder=(
            dwca_row_value(media, "rightsHolder")
            or value_to_str(archive.get("rights_holder") or settings.get("rights_holder"))
        ),
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
        notes=f"metadata_from_dwca; archive={value_to_str(archive.get('name') or archive.get('archive_url'))}",
    )


def dwca_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    archives = settings.get("archives") or []
    if isinstance(archives, dict):
        archives = [archives]
    if not isinstance(archives, list):
        return []
    occurrence_file = str(settings.get("occurrence_file", "occurrence.txt"))
    multimedia_file = str(settings.get("multimedia_file", "multimedia.txt"))
    delay = float(settings.get("request_delay_seconds", 1.5))
    records: list[SpecimenRecord] = []
    seen = 0

    for archive in archives:
        if not isinstance(archive, dict):
            continue
        archive_url = value_to_str(archive.get("archive_url") or archive.get("url"))
        if not archive_url:
            continue
        archive_name = safe_token(archive.get("name") or archive_url)
        cache_path = raw_dir / safe_token(query_name) / archive_name / "archive.zip"
        if cache_path.exists() and not refresh:
            content = cache_path.read_bytes()
        else:
            content = client.get_bytes(archive_url)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(content)
            client.sleep(delay)
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                occurrence_rows = dwca_read_table(zf, value_to_str(archive.get("occurrence_file")) or occurrence_file)
                media_rows = dwca_read_table(zf, value_to_str(archive.get("multimedia_file")) or multimedia_file)
        except zipfile.BadZipFile:
            continue
        media = dwca_media_by_id(media_rows)
        for row in occurrence_rows:
            scientific_name = dwca_row_value(row, "scientificName")
            if bool(settings.get("exact_name_filter", True)) and not query_name_matches(query_name, [scientific_name]):
                continue
            if seen < record_offset:
                seen += 1
                continue
            if max_records is not None and len(records) >= max_records:
                return records
            record_id = dwca_row_value(row, "id", "occurrenceID")
            occurrence_id = dwca_row_value(row, "occurrenceID") or record_id
            media_rows_for_record = media.get(record_id, []) + ([] if occurrence_id == record_id else media.get(occurrence_id, []))
            records.append(dwca_record_from_row(source, query_name, row, media_rows_for_record, archive, settings))
            seen += 1
    return records


def brahms_bol_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    base_url = str(settings.get("base_url", "")).rstrip("/")
    if not base_url:
        return []
    genus, species = split_binomial(query_name)
    explore_template = str(settings.get("explore_url", base_url + "/Explore?genus={genus}&sp1={species}&view=images"))
    delay = float(settings.get("request_delay_seconds", 2.0))
    search_url = explore_template.format(
        query=quote_plus(query_name),
        genus=quote_plus(genus),
        species=quote_plus(species),
    )
    cache_path = raw_dir / safe_token(query_name) / "explore.html"
    final_url = search_url
    if cache_path.exists() and not refresh:
        html = cache_path.read_text(encoding="utf-8")
    else:
        html, final_url = client.get_text_with_url(search_url)
        save_raw_text(cache_path, html)
        client.sleep(delay)
    if not final_url.rstrip("/").lower().startswith(base_url.lower()):
        return []
    if "BRAHMS Online Websites" in html and "/bol/brahms/Websites" in final_url:
        return []
    image_urls = collect_image_candidates(html, final_url)
    pattern = str(settings.get("record_link_pattern", "")).strip()
    record_urls = collect_links(html, final_url, [re.compile(pattern, re.IGNORECASE)]) if pattern else []
    records: list[SpecimenRecord] = []
    selected_images = list(dict.fromkeys(image_urls))[record_offset:]
    if max_records is not None:
        selected_images = selected_images[:max_records]
    for index, image_url in enumerate(selected_images, start=record_offset + 1):
        records.append(
            SpecimenRecord(
                source=source,
                query_name=query_name,
                source_record_id=f"{safe_token(query_name)}_{index}",
                source_record_url=record_urls[index - 1] if index - 1 < len(record_urls) else final_url,
                institution_code=str(settings.get("institution_code", source.upper())),
                collection_code=str(settings.get("collection_code", source.upper())),
                scientific_name=query_name,
                basis_of_record="PRESERVED_SPECIMEN",
                coordinate_status="missing_coordinates",
                image_url=image_url,
                original_image_url=image_url,
                rights_holder=str(settings.get("rights_holder", "")),
                accessed_at=now_iso(),
                download_status="pending",
                notes="metadata_from_brahms_bol_html; sparse_record_because_structured_endpoint_not_available",
            )
        )
    return records


def dict_value(row: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = value_to_str(row.get(key)).strip()
        if value:
            return value
    return ""


def jacq_image_url_from_data(data: dict[str, object], preferred_size: str) -> str:
    download = data.get("download") if isinstance(data.get("download"), dict) else {}
    if isinstance(download, dict):
        for key in [preferred_size, "europeana", "full", "thumb"]:
            urls = download.get(key)
            if isinstance(urls, list):
                image_url = best_image_url([value_to_str(url) for url in urls])
                if image_url:
                    return image_url
            elif value_to_str(urls):
                image_url = best_image_url([value_to_str(urls)])
                if image_url:
                    return image_url
    show = data.get("show")
    if isinstance(show, list):
        return best_image_url([value_to_str(url) for url in show])
    return ""


def jacq_record_from_item(
    source: str,
    query_name: str,
    item: dict[str, object],
    image_url: str,
) -> SpecimenRecord:
    dc = item.get("dc") if isinstance(item.get("dc"), dict) else {}
    dwc = item.get("dwc") if isinstance(item.get("dwc"), dict) else {}
    jacq = item.get("jacq") if isinstance(item.get("jacq"), dict) else {}
    dc_ref = dc.get("dc:IsReferencedBy") if isinstance(dc, dict) else ""
    references = value_to_str(dc_ref)
    specimen_id = dict_value(jacq, "jacq:specimenID", "specimenID")
    catalog = dict_value(dwc, "dwc:catalogNumber", "catalogNumber") or dict_value(jacq, "jacq:HerbNummer")
    institution = dict_value(jacq, "jacq:OwnerOrganizationAbbrev", "OwnerOrganizationAbbrev")
    collection = dict_value(dwc, "dwc:collectionCode", "collectionCode") or institution
    source_url = (
        dict_value(jacq, "jacq:stableIdentifier", "stableIdentifier")
        or dict_value(dwc, "dwc:materialSampleID", "materialSampleID")
        or (f"https://api.jacq.org/v1/objects/specimens/{quote_plus(specimen_id)}" if specimen_id else "")
    )
    latitude = dict_value(dwc, "dwc:decimalLatitude", "decimalLatitude") or dict_value(jacq, "jacq:decimalLatitude")
    longitude = dict_value(dwc, "dwc:decimalLongitude", "decimalLongitude") or dict_value(jacq, "jacq:decimalLongitude")
    return SpecimenRecord(
        source=source,
        query_name=query_name,
        source_record_id=specimen_id or catalog or safe_token(source_url),
        source_record_url=source_url,
        occurrence_id=dict_value(dwc, "dwc:materialSampleID", "materialSampleID") or source_url,
        institution_code=institution or collection,
        collection_code=collection,
        catalog_number=catalog,
        scientific_name=dict_value(dwc, "dwc:scientificName", "scientificName") or dict_value(jacq, "jacq:scientificName"),
        recorded_by=dict_value(dwc, "dwc:recordedBy", "recordedBy") or dict_value(jacq, "jacq:collectorTeam"),
        record_number=(
            dict_value(dwc, "dwc:fieldNumber", "fieldNumber")
            or dict_value(jacq, "jacq:alt_number", "jacq:Nummer", "jacq:CollNummer")
        ),
        event_date=dict_value(dwc, "dwc:eventDate", "eventDate") or dict_value(jacq, "jacq:created"),
        country=dict_value(dwc, "dwc:country", "country") or dict_value(jacq, "jacq:nation_engl"),
        locality=dict_value(dwc, "dwc:locality", "locality") or dict_value(jacq, "jacq:Fundort"),
        verbatim_locality=dict_value(dwc, "dwc:locality", "locality") or dict_value(jacq, "jacq:Fundort"),
        decimal_latitude=latitude,
        decimal_longitude=longitude,
        elevation=dict_value(dwc, "dwc:verbatimElevation", "verbatimElevation"),
        type_status=dict_value(dwc, "dwc:typeStatus", "typeStatus") or dict_value(jacq, "jacq:typeInformation"),
        basis_of_record=dict_value(dwc, "dwc:basisOfRecord", "basisOfRecord") or "PRESERVED_SPECIMEN",
        coordinate_status=coordinate_status(latitude, longitude),
        image_url=image_url,
        original_image_url=image_url,
        image_license=dict_value(jacq, "jacq:LicenseURI", "LicenseURI"),
        rights_holder=institution or "JACQ consortium",
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
        notes="metadata_from_jacq_api" + (f"; references={references}" if references else ""),
    )


def jacq_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    endpoint = str(settings.get("api_url", "https://api.jacq.org/v1/objects/specimens"))
    image_list_template = str(settings.get("image_list_url", "https://api.jacq.org/v1/images/list/{specimen_id}"))
    page_size = min(int(settings.get("page_size", 50)), 100)
    max_pages = int(settings.get("max_pages_per_name", 20))
    delay = float(settings.get("request_delay_seconds", 1.5))
    preferred_image_size = str(settings.get("preferred_image_size", "europeana"))
    institution_codes = settings.get("institution_codes") or [settings.get("institution_code", "")]
    if isinstance(institution_codes, str):
        institution_codes = [institution_codes]
    records: list[SpecimenRecord] = []
    seen = 0

    for institution in [value_to_str(code).strip() for code in institution_codes if value_to_str(code).strip()]:
        for page in range(1, max_pages + 1):
            cache_path = raw_dir / safe_token(query_name) / safe_token(institution) / f"page_{page:04d}.json"
            from_cache = cache_path.exists() and not refresh
            if from_cache:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
            else:
                data = client.get_json(
                    endpoint,
                    params={
                        "list": 0,
                        "term": query_name,
                        "sc": institution,
                        "type": 0,
                        "withImages": 1 if bool(settings.get("with_images_only", True)) else 0,
                        "rpp": page_size,
                        "p": page,
                    },
                )
                save_raw_json(cache_path, data)
            rows = data.get("result", []) if isinstance(data, dict) else []
            if not isinstance(rows, list) or not rows:
                break
            for item in rows:
                if not isinstance(item, dict):
                    continue
                if seen < record_offset:
                    seen += 1
                    continue
                if max_records is not None and len(records) >= max_records:
                    return records
                jacq = item.get("jacq") if isinstance(item.get("jacq"), dict) else {}
                specimen_id = dict_value(jacq, "jacq:specimenID", "specimenID")
                image_url = ""
                if specimen_id:
                    image_cache = raw_dir / safe_token(query_name) / safe_token(institution) / "images" / f"{safe_token(specimen_id)}.json"
                    if image_cache.exists() and not refresh:
                        image_data = json.loads(image_cache.read_text(encoding="utf-8"))
                    else:
                        image_data = client.get_json(image_list_template.format(specimen_id=quote_plus(specimen_id)))
                        save_raw_json(image_cache, image_data)
                        client.sleep(delay)
                    if isinstance(image_data, dict):
                        image_url = jacq_image_url_from_data(image_data, preferred_image_size)
                image_url = image_url or dict_value(jacq, "jacq:downloadImage", "downloadImage")
                record = jacq_record_from_item(source, query_name, item, image_url)
                if bool(settings.get("exact_name_filter", True)) and not query_name_matches(
                    query_name,
                    [record.scientific_name],
                ):
                    seen += 1
                    continue
                records.append(record)
                seen += 1
            total_pages = int(data.get("totalPages") or 0) if isinstance(data, dict) else 0
            if len(rows) < page_size or (total_pages and page >= total_pages):
                break
            if not from_cache:
                client.sleep(delay)
    return records


def ti_clean(value: object) -> str:
    return re.sub(r"\s+", " ", value_to_str(value).replace("\xa0", " ")).strip()


def ti_detail_urls(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"href=[\"']([^\"']*Detail/detail\.php\?No=\d+[^\"']*)[\"']", html, re.IGNORECASE):
        urls.append(urljoin(base_url, unescape(match.group(1))))
    return list(dict.fromkeys(urls))


def ti_image_url(html: str, base_url: str, settings: dict) -> str:
    variant = str(settings.get("image_variant", "2048"))
    match = re.search(r"disp\('([^']+)'\)", html)
    if match:
        image_key = match.group(1)
        return encoded_absolute_url(
            base_url,
            f"/DImages/Shokubutsu/herbarium_ferns/Type/{image_key}_{variant}_.jpg",
        )
    candidates = collect_image_candidates(html, base_url)
    return best_image_url(candidates)


def ti_record_from_detail(html: str, url: str, query_name: str, settings: dict) -> SpecimenRecord:
    values = {
        "TI CODE": table_row_value(html, "TI CODE"),
        "Scientific Name": table_row_value(html, "Scientific Name"),
        "Type Status": table_row_value(html, "Type Status"),
        "Family": table_row_value(html, "Family"),
        "Genus": table_row_value(html, "Genus"),
        "Species": table_row_value(html, "Species"),
        "Author": table_row_value(html, "Author"),
        "Locality": table_row_value(html, "Locality"),
        "Collector": table_row_value(html, "Collector"),
        "Collection Date": table_row_value(html, "Collection Date"),
        "Note": table_row_value(html, "Note"),
    }
    locality = ti_clean(values["Locality"])
    country = ""
    if ":" in locality:
        maybe_country, rest = locality.split(":", 1)
        if maybe_country and maybe_country == maybe_country.upper():
            country = maybe_country.strip()
            locality = rest.strip()
    catalog = ti_clean(values["TI CODE"])
    image_url = ti_image_url(html, url, settings)
    return SpecimenRecord(
        source="ti",
        query_name=query_name,
        source_record_id=catalog or safe_token(url),
        source_record_url=url,
        occurrence_id=f"TI:{catalog}" if catalog else "",
        institution_code="TI",
        collection_code="Type",
        catalog_number=catalog,
        scientific_name=ti_clean(values["Scientific Name"]),
        recorded_by=ti_clean(values["Collector"]),
        event_date=ti_clean(values["Collection Date"]),
        country=country,
        locality=locality,
        verbatim_locality=ti_clean(values["Locality"]),
        elevation=ti_clean(values["Note"]) if re.search(r"\b(?:alt|elev|m\.)\b", values["Note"], re.IGNORECASE) else "",
        type_status=ti_clean(values["Type Status"]),
        basis_of_record="PRESERVED_SPECIMEN",
        coordinate_status="missing_coordinates",
        image_url=image_url,
        original_image_url=image_url,
        image_license="https://creativecommons.org/licenses/by-nc-nd/4.0/",
        rights_holder="The University Museum, The University of Tokyo; TI Herbarium",
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
        notes="metadata_from_ti_type_collection_database",
    )


def ti_type_records(
    client: PoliteHttpClient,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    form_url = str(
        settings.get("search_form_url", "https://umdb.um.u-tokyo.ac.jp/DShokubu/herbarium_ferns/en/Collection/search.php?-langTop=jp")
    )
    result_url = str(
        settings.get("search_result_url", "https://umdb.um.u-tokyo.ac.jp/DShokubu/herbarium_ferns/en/Collection/searchresult.php?-langTop=jp")
    )
    page_size = min(int(settings.get("page_size", 25)), 100)
    max_pages = int(settings.get("max_pages_per_name", 5))
    delay = float(settings.get("request_delay_seconds", 2.0))
    detail_urls: list[str] = []
    form_cache = raw_dir / safe_token(query_name) / "search_form.html"
    if not (form_cache.exists() and not refresh):
        save_raw_text(form_cache, client.get_text(form_url))
        client.sleep(delay)
    payload_base: dict[str, object] = {
        "-action": "find",
        "-max": page_size,
        "-sortfieldone": "TICODE",
        "-sortorderone": "ascend",
        "-sortfieldtwo": "RegisterNoSort",
        "-sortordertwo": "ascend",
        "-sortfieldthree": "ScientificName",
        "-sortorderthree": "ascend",
        "-tokenproject": "search_ad",
        "4": "cn",
        "5": query_name,
    }
    for page in range(max_pages):
        skip = page * page_size
        cache_path = raw_dir / safe_token(query_name) / f"search_{page + 1:04d}.html"
        if cache_path.exists() and not refresh:
            html = cache_path.read_text(encoding="utf-8")
            final_url = result_url
        else:
            payload = dict(payload_base)
            payload["-skip"] = skip
            html, final_url = client.post_text_with_url(
                result_url,
                payload,
                headers={"Referer": form_url},
            )
            save_raw_text(cache_path, html)
        urls = ti_detail_urls(html, final_url)
        before = len(detail_urls)
        detail_urls.extend(urls)
        detail_urls = list(dict.fromkeys(detail_urls))
        if len(detail_urls) == before or len(urls) < page_size:
            break
        client.sleep(delay)

    selected_urls = detail_urls[record_offset:]
    if max_records is not None:
        selected_urls = selected_urls[:max_records]
    records: list[SpecimenRecord] = []
    for index, url in enumerate(selected_urls, start=record_offset + 1):
        cache_path = raw_dir / safe_token(query_name) / "records" / f"record_{index:05d}.html"
        if cache_path.exists() and not refresh:
            html = cache_path.read_text(encoding="utf-8")
        else:
            html = client.get_text(url)
            save_raw_text(cache_path, html)
        record = ti_record_from_detail(html, url, query_name, settings)
        if bool(settings.get("exact_name_filter", True)) and not query_name_matches(query_name, [record.scientific_name]):
            continue
        records.append(record)
        client.sleep(delay)
    return records


def nmnh_ark(uuid_value: object) -> str:
    text = re.sub(r"[^0-9a-f]", "", value_to_str(uuid_value).lower())
    if len(text) != 32:
        return ""
    parts = [text[:8], text[8:12], text[12:16], text[16:20], text[20:]]
    return "http://n2t.net/ark:/65665/3" + "-".join(parts)


def nmnh_media_items(item: dict[str, object]) -> list[dict[str, object]]:
    media = item.get("mulmm")
    if isinstance(media, list):
        return [entry for entry in media if isinstance(entry, dict)]
    if isinstance(media, dict):
        return [media]
    return []


def nmnh_image_url(media: dict[str, object], width: int) -> str:
    if value_to_str(media.get("mulmt")).lower() != "image":
        return ""
    media_uuid = value_to_str(media.get("muluu")).strip()
    media_id = value_to_str(media.get("mulid")).strip()
    if media.get("siids") is True and media_uuid:
        return f"https://ids.si.edu/ids/deliveryService/id/ark:/65665/m3{media_uuid}/{width}"
    if media_id:
        nested = quote(f"https://collections.nmnh.si.edu/media/?irn={media_id}", safe=":/?=&")
        return f"https://ids.si.edu/ids/deliveryService?max={width}&id={nested}"
    return ""


def nmnh_record_from_item(source: str, query_name: str, item: dict[str, object], image_width: int) -> SpecimenRecord:
    identification = item.get("idefa") if isinstance(item.get("idefa"), dict) else {}
    catalog_data = item.get("catnb") if isinstance(item.get("catnb"), dict) else {}
    catalog = value_to_str(item.get("darcx") or catalog_data.get("catnc"))
    latitude = value_to_str(item.get("darlt"))
    longitude = value_to_str(item.get("darln"))
    elevation_data = item.get("darel") if isinstance(item.get("darel"), dict) else {}
    media_candidates = nmnh_media_items(item)
    media = media_candidates[0] if media_candidates else {}
    image_url = nmnh_image_url(media, image_width) if media else ""
    record_id = value_to_str(item.get("_id"))
    return SpecimenRecord(
        source=source,
        query_name=query_name,
        source_record_id=record_id or catalog,
        source_record_url=f"https://collections.nmnh.si.edu/search/botany/?irn={quote_plus(record_id)}" if record_id else "",
        occurrence_id=nmnh_ark(item.get("admuu")) or record_id,
        institution_code=value_to_str(item.get("daric") or "US"),
        collection_code=value_to_str(item.get("catct") or "Botany"),
        catalog_number=ti_clean(catalog),
        scientific_name=value_to_str(identification.get("ideqn") or item.get("darsn")),
        recorded_by=join_values(item.get("biopr")) or value_to_str(item.get("darcr") or item.get("biopc")),
        record_number=value_to_str(item.get("darcn") or item.get("biopn")),
        event_date=value_to_str((item.get("coldv") or {}).get("coled") or (item.get("coldv") or {}).get("colvd")) if isinstance(item.get("coldv"), dict) else "",
        country=value_to_str(item.get("darct")),
        state_province=value_to_str(item.get("darst")),
        locality=value_to_str(item.get("darlc")),
        verbatim_locality=value_to_str(item.get("darhg") or item.get("darlc")),
        decimal_latitude=latitude,
        decimal_longitude=longitude,
        elevation=value_to_str(elevation_data.get("darem") or elevation_data.get("darve")),
        identified_by=value_to_str(identification.get("ideib")) if isinstance(identification, dict) else "",
        type_status=value_to_str(item.get("darts")),
        basis_of_record="PRESERVED_SPECIMEN",
        coordinate_status=coordinate_status(latitude, longitude),
        image_url=image_url,
        original_image_url=image_url,
        image_license=value_to_str(media.get("detrs")) if isinstance(media, dict) else "",
        rights_holder=value_to_str(media.get("detrg")) if isinstance(media, dict) else "National Museum of Natural History, Smithsonian Institution",
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
        notes="metadata_from_nmnh_public_search",
    )


def nmnh_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    search_page_url = str(settings.get("search_page_url", "https://collections.nmnh.si.edu/search/botany/"))
    endpoint = str(settings.get("api_url", "https://collections.nmnh.si.edu/search/botany/search.php"))
    page_size = min(int(settings.get("page_size", 10)), 100)
    max_pages = int(settings.get("max_pages_per_name", 20))
    delay = float(settings.get("request_delay_seconds", 1.5))
    image_width = int(settings.get("image_width", 1600))
    start = record_offset
    form_cache = raw_dir / safe_token(query_name) / "search_page.html"
    if not (form_cache.exists() and not refresh):
        save_raw_text(form_cache, client.get_text(f"{search_page_url}?qn={quote_plus(query_name)}"))
        client.sleep(delay)
    records: list[SpecimenRecord] = []
    for page in range(1, max_pages + 1):
        cache_path = raw_dir / safe_token(query_name) / f"offset_{start:07d}.json"
        from_cache = cache_path.exists() and not refresh
        if from_cache:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            data = client.post_json(
                endpoint,
                data={
                    "action": 1,
                    "qtype": 12,
                    "view": str(settings.get("view", "keyword:sheet")),
                    "start": start,
                    "limit": page_size,
                    "terms": f"qn {query_name}",
                },
                headers={"Referer": f"{search_page_url}?qn={quote_plus(query_name)}"},
            )
            save_raw_json(cache_path, data)
        rows = data.get("records", []) if isinstance(data, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if max_records is not None and len(records) >= max_records:
                return records
            if not isinstance(row, dict):
                continue
            record = nmnh_record_from_item(source, query_name, row, image_width)
            other_names = [
                value_to_str(entry.get("ideoq"))
                for entry in row.get("ideon", [])
                if isinstance(entry, dict)
            ] if isinstance(row.get("ideon"), list) else []
            if bool(settings.get("exact_name_filter", True)) and not query_name_matches(
                query_name,
                [record.scientific_name, row.get("darsn"), *other_names],
            ):
                continue
            if bool(settings.get("with_images_only", False)) and not record.image_url:
                continue
            records.append(record)
        fetched = int(data.get("recordsFetched") or 0) if isinstance(data, dict) else 0
        start += len(rows)
        if len(rows) < page_size or (fetched and start >= fetched):
            break
        if not from_cache:
            client.sleep(delay)
    return records


def first_dict(values: object) -> dict[str, object]:
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict):
                return value
    return {}


def naturalis_event_date(value: object) -> str:
    text = value_to_str(value).strip()
    return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else text


def naturalis_record_from_item(source: str, query_name: str, item: dict[str, object]) -> SpecimenRecord:
    identification = first_dict(item.get("identifications"))
    for candidate in item.get("identifications", []) if isinstance(item.get("identifications"), list) else []:
        if isinstance(candidate, dict) and candidate.get("preferred") is True:
            identification = candidate
            break
    scientific = identification.get("scientificName") if isinstance(identification.get("scientificName"), dict) else {}
    gathering = item.get("gatheringEvent") if isinstance(item.get("gatheringEvent"), dict) else {}
    coordinates = gathering.get("siteCoordinates") if isinstance(gathering.get("siteCoordinates"), dict) else {}
    persons = gathering.get("gatheringPersons") if isinstance(gathering.get("gatheringPersons"), list) else []
    recorded_by = "; ".join(
        value_to_str(person.get("fullName") or person.get("agentText"))
        for person in persons
        if isinstance(person, dict) and value_to_str(person.get("fullName") or person.get("agentText"))
    )
    media = item.get("associatedMultiMediaUris") if isinstance(item.get("associatedMultiMediaUris"), list) else []
    image_url = best_image_url(
        [
            value_to_str(media_item.get("accessUri"))
            for media_item in media
            if isinstance(media_item, dict) and str(media_item.get("format", "")).lower().startswith("image/")
        ]
    )
    unit_id = value_to_str(item.get("unitID") or item.get("sourceSystemId") or item.get("id"))
    source_url = value_to_str(item.get("unitGUID"))
    if not source_url and unit_id:
        source_url = f"https://data.biodiversitydata.nl/naturalis/specimen/{unit_id}"
    return SpecimenRecord(
        source=source,
        query_name=query_name,
        source_record_id=value_to_str(item.get("id") or unit_id),
        source_record_url=source_url,
        occurrence_id=value_to_str(item.get("unitGUID") or item.get("id")),
        institution_code="L",
        catalog_number=unit_id,
        scientific_name=value_to_str(scientific.get("fullScientificName")) if isinstance(scientific, dict) else "",
        recorded_by=recorded_by,
        record_number=value_to_str(item.get("collectorsFieldNumber")),
        event_date=naturalis_event_date(gathering.get("dateTimeBegin") or gathering.get("dateText")) if isinstance(gathering, dict) else "",
        country=value_to_str(gathering.get("country")) if isinstance(gathering, dict) else "",
        state_province=value_to_str(gathering.get("provinceState")) if isinstance(gathering, dict) else "",
        locality=value_to_str(gathering.get("locality")) if isinstance(gathering, dict) else "",
        verbatim_locality=value_to_str(gathering.get("localityText") or gathering.get("locality")) if isinstance(gathering, dict) else "",
        decimal_latitude=value_to_str(coordinates.get("latitudeDecimal")) if isinstance(coordinates, dict) else "",
        decimal_longitude=value_to_str(coordinates.get("longitudeDecimal")) if isinstance(coordinates, dict) else "",
        elevation=" ".join(
            part
            for part in [
                value_to_str(gathering.get("altitude")) if isinstance(gathering, dict) else "",
                value_to_str(gathering.get("altitudeUnifOfMeasurement")) if isinstance(gathering, dict) else "",
            ]
            if part
        ),
        basis_of_record=value_to_str(item.get("recordBasis") or "PRESERVED_SPECIMEN"),
        coordinate_status=coordinate_status(
            coordinates.get("latitudeDecimal") if isinstance(coordinates, dict) else "",
            coordinates.get("longitudeDecimal") if isinstance(coordinates, dict) else "",
        ),
        image_url=image_url,
        original_image_url=image_url,
        image_license=value_to_str(item.get("license")),
        rights_holder=value_to_str(item.get("owner") or item.get("sourceInstitutionID")),
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
    )


def naturalis_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    endpoint = str(settings.get("api_url", "https://api.biodiversitydata.nl/v2/specimen/query/"))
    page_size = min(int(settings.get("page_size", 100)), 500)
    max_pages = int(settings.get("max_pages_per_name", 20))
    delay = float(settings.get("request_delay_seconds", 1.5))
    source_system = str(settings.get("source_system_code", "BRAHMS"))
    records: list[SpecimenRecord] = []
    offset = record_offset
    for page_number in range(1, max_pages + 1):
        conditions: list[dict[str, object]] = [
            {"field": "sourceSystem.code", "operator": "=", "value": source_system},
            {
                "field": "identifications.scientificName.fullScientificName",
                "operator": "MATCHES",
                "value": query_name,
            },
        ]
        query_spec = {"conditions": conditions, "from": offset, "size": page_size}
        cache_path = raw_dir / safe_token(query_name) / f"offset_{offset:07d}.json"
        from_cache = cache_path.exists() and not refresh
        if from_cache:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            data = client.get_json(endpoint, params={"_querySpec": json.dumps(query_spec)})
            save_raw_json(cache_path, data)
        rows = data.get("resultSet", []) if isinstance(data, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if max_records is not None and len(records) >= max_records:
                return records
            item = row.get("item") if isinstance(row, dict) else None
            if not isinstance(item, dict):
                continue
            record = naturalis_record_from_item(source, query_name, item)
            if bool(settings.get("exact_name_filter", True)) and not query_name_matches(
                query_name,
                [record.scientific_name],
            ):
                continue
            records.append(record)
        offset += len(rows)
        total = int(data.get("totalSize") or 0) if isinstance(data, dict) else 0
        if len(rows) < page_size or (total and offset >= total):
            break
        if not from_cache:
            client.sleep(delay)
    return records


def rbge_xml_value(xml: str, tag: str) -> str:
    match = re.search(rf"<(?:[A-Za-z0-9_]+:)?{re.escape(tag)}(?:\s[^>]*)?>(.*?)</(?:[A-Za-z0-9_]+:)?{re.escape(tag)}>", xml, re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def rbge_image_url(xml: str, catalog_number: str, width: int) -> str:
    match = re.search(r"https://iiif\.rbge\.org\.uk/herb/iiif/[^\"'<> ]+/full/[^\"'<> ]+/0/default\.jpg", xml)
    if match:
        url = match.group(0)
        return re.sub(r"/full/[^/]+/", f"/full/{width},/", url)
    if catalog_number:
        return f"https://iiif.rbge.org.uk/herb/iiif/{catalog_number}/full/{width},/0/default.jpg"
    return ""


def rbge_record_from_rdf(source: str, query_name: str, url: str, xml: str, settings: dict) -> SpecimenRecord:
    catalog = rbge_xml_value(xml, "catalogNumber")
    image_width = int(settings.get("iiif_width", settings.get("max_image_width", 2400)))
    image_url = rbge_image_url(xml, catalog, image_width)
    locality = rbge_xml_value(xml, "locality") or rbge_xml_value(xml, "stateProvince")
    return SpecimenRecord(
        source=source,
        query_name=query_name,
        source_record_id=catalog or safe_token(url),
        source_record_url=f"https://data.rbge.org.uk/herb/{catalog}" if catalog else url,
        occurrence_id=rbge_xml_value(xml, "sampleID") or f"E:{catalog}",
        institution_code="E",
        collection_code=rbge_xml_value(xml, "collectionCode") or "E",
        catalog_number=catalog,
        scientific_name=rbge_xml_value(xml, "scientificName"),
        recorded_by=rbge_xml_value(xml, "recordedBy"),
        record_number=rbge_xml_value(xml, "recordNumber"),
        event_date=rbge_xml_value(xml, "eventDate") or rbge_xml_value(xml, "earliestDateCollected"),
        country=rbge_xml_value(xml, "country"),
        state_province=rbge_xml_value(xml, "stateProvince"),
        locality=locality,
        verbatim_locality=rbge_xml_value(xml, "verbatimLocality") or locality,
        decimal_latitude=rbge_xml_value(xml, "decimalLatitude"),
        decimal_longitude=rbge_xml_value(xml, "decimalLongitude"),
        elevation=rbge_xml_value(xml, "verbatimElevation"),
        type_status=rbge_xml_value(xml, "typeStatus"),
        basis_of_record=rbge_xml_value(xml, "basisOfRecord") or "PRESERVED_SPECIMEN",
        coordinate_status=coordinate_status(rbge_xml_value(xml, "decimalLatitude"), rbge_xml_value(xml, "decimalLongitude")),
        image_url=image_url,
        original_image_url=image_url,
        image_license="https://creativecommons.org/licenses/by/4.0/",
        rights_holder="Royal Botanic Garden Edinburgh",
        accessed_at=now_iso(),
        download_status="pending" if image_url else "no_image_url",
    )


def rbge_records(
    client: PoliteHttpClient,
    source: str,
    query_name: str,
    raw_dir: Path,
    settings: dict,
    max_records: int | None,
    record_offset: int,
    refresh: bool,
) -> list[SpecimenRecord]:
    search_template = str(
        settings.get(
            "search_url",
            "https://data.rbge.org.uk/search/herbarium/?barcode=&cfg=vherb.cfg&coll_name=&coll_num=&country_name=&family=&genus={genus}&keywords=&region=&species={species}",
        )
    )
    pattern = re.compile(str(settings.get("record_link_pattern", r"/herb/E\d+")), re.IGNORECASE)
    max_pages = int(settings.get("max_pages_per_name", 1))
    delay = float(settings.get("request_delay_seconds", 1.5))
    genus, species = split_binomial(query_name)
    record_urls: set[str] = set()
    for page in range(1, max_pages + 1):
        search_url = search_template.format(
            query=quote_plus(query_name),
            genus=quote_plus(genus),
            species=quote_plus(species),
            page=page,
        )
        cache_path = raw_dir / safe_token(query_name) / f"search_{page:04d}.html"
        from_cache = cache_path.exists() and not refresh
        if from_cache:
            html = cache_path.read_text(encoding="utf-8")
            final_url = search_url
        else:
            html, final_url = client.get_text_with_url(search_url)
            save_raw_text(cache_path, html)
        record_urls.update(collect_links(html, final_url, [pattern]))
        if not from_cache:
            client.sleep(delay)

    selected_urls = sorted(record_urls)[record_offset:]
    if max_records is not None:
        selected_urls = selected_urls[:max_records]
    records: list[SpecimenRecord] = []
    for index, url in enumerate(selected_urls, start=record_offset + 1):
        cache_path = raw_dir / safe_token(query_name) / "records" / f"record_{index:05d}.rdf"
        if cache_path.exists() and not refresh:
            xml = cache_path.read_text(encoding="utf-8")
        else:
            xml = client.get_text(url)
            save_raw_text(cache_path, xml)
        record = rbge_record_from_rdf(source, query_name, url, xml, settings)
        if bool(settings.get("exact_name_filter", True)) and not query_name_matches(query_name, [record.scientific_name]):
            continue
        records.append(record)
        client.sleep(delay)
    return records
