from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from specimen_collector.html_utils import save_raw_json
from specimen_collector.http_client import PoliteHttpClient
from specimen_collector.sources import cvh_records, gbif_media_score, gbif_media_url, value_to_str

from .geo import canonical_country_code, circle_wkt, country_code_for, distance_km, normalized_text, record_matches_area
from .models import (
    EvidenceRecord,
    SearchArea,
    SpeciesEntry,
    TaxonFilter,
    authorship_from_name,
    canonical_species_name,
    clean_text,
    is_hybrid_name,
    safe_token,
)
from .progress import Heartbeat, progress, progress_bar


GBIF_OCCURRENCE_SEARCH = "https://api.gbif.org/v1/occurrence/search"
GBIF_SPECIES_MATCH = "https://api.gbif.org/v1/species/match"
PLANTAE_KEY = 6
SPECIES_LEVEL_RANKS = {"SPECIES", "SUBSPECIES", "VARIETY", "FORM"}
MAX_IMAGE_CANDIDATES = 10

CVH_COUNTRY_CODE_ALIASES = {
    "阿富汗": "AF",
    "阿尔巴尼亚": "AL",
    "阿爾巴尼亞": "AL",
    "阿尔及利亚": "DZ",
    "阿爾及利亞": "DZ",
    "安哥拉": "AO",
    "阿根廷": "AR",
    "亚美尼亚": "AM",
    "亞美尼亞": "AM",
    "澳大利亚": "AU",
    "澳大利亞": "AU",
    "澳洲": "AU",
    "奥地利": "AT",
    "奧地利": "AT",
    "阿塞拜疆": "AZ",
    "孟加拉国": "BD",
    "孟加拉國": "BD",
    "白俄罗斯": "BY",
    "白俄羅斯": "BY",
    "比利时": "BE",
    "比利時": "BE",
    "不丹": "BT",
    "玻利维亚": "BO",
    "玻利維亞": "BO",
    "巴西": "BR",
    "文莱": "BN",
    "文萊": "BN",
    "保加利亚": "BG",
    "保加利亞": "BG",
    "柬埔寨": "KH",
    "喀麦隆": "CM",
    "喀麥隆": "CM",
    "加拿大": "CA",
    "智利": "CL",
    "中国": "CN",
    "中華人民共和国": "CN",
    "中华人民共和国": "CN",
    "哥伦比亚": "CO",
    "哥倫比亞": "CO",
    "刚果": "CG",
    "剛果": "CG",
    "哥斯达黎加": "CR",
    "哥斯達黎加": "CR",
    "古巴": "CU",
    "捷克": "CZ",
    "丹麦": "DK",
    "丹麥": "DK",
    "厄瓜多尔": "EC",
    "厄瓜多爾": "EC",
    "埃及": "EG",
    "埃塞俄比亚": "ET",
    "埃塞俄比亞": "ET",
    "斐济": "FJ",
    "斐濟": "FJ",
    "芬兰": "FI",
    "芬蘭": "FI",
    "法国": "FR",
    "法國": "FR",
    "德国": "DE",
    "德國": "DE",
    "加纳": "GH",
    "加納": "GH",
    "希腊": "GR",
    "希臘": "GR",
    "危地马拉": "GT",
    "危地馬拉": "GT",
    "洪都拉斯": "HN",
    "匈牙利": "HU",
    "冰岛": "IS",
    "冰島": "IS",
    "印度": "IN",
    "印度尼西亚": "ID",
    "印度尼西亞": "ID",
    "印尼": "ID",
    "伊朗": "IR",
    "伊拉克": "IQ",
    "爱尔兰": "IE",
    "愛爾蘭": "IE",
    "以色列": "IL",
    "意大利": "IT",
    "義大利": "IT",
    "日本": "JP",
    "日本国": "JP",
    "日本國": "JP",
    "哈萨克斯坦": "KZ",
    "哈薩克斯坦": "KZ",
    "肯尼亚": "KE",
    "肯尼亞": "KE",
    "吉尔吉斯斯坦": "KG",
    "吉爾吉斯斯坦": "KG",
    "老挝": "LA",
    "老撾": "LA",
    "老挝人民民主共和国": "LA",
    "老撾人民民主共和國": "LA",
    "马来西亚": "MY",
    "馬來西亞": "MY",
    "墨西哥": "MX",
    "蒙古": "MN",
    "缅甸": "MM",
    "緬甸": "MM",
    "尼泊尔": "NP",
    "尼泊爾": "NP",
    "荷兰": "NL",
    "荷蘭": "NL",
    "新西兰": "NZ",
    "新西蘭": "NZ",
    "尼日利亚": "NG",
    "尼日利亞": "NG",
    "朝鲜": "KP",
    "朝鮮": "KP",
    "挪威": "NO",
    "巴基斯坦": "PK",
    "巴拿马": "PA",
    "巴拿馬": "PA",
    "巴布亚新几内亚": "PG",
    "巴布亞新幾內亞": "PG",
    "巴拉圭": "PY",
    "秘鲁": "PE",
    "秘魯": "PE",
    "菲律宾": "PH",
    "菲律賓": "PH",
    "波兰": "PL",
    "波蘭": "PL",
    "葡萄牙": "PT",
    "罗马尼亚": "RO",
    "羅馬尼亞": "RO",
    "俄罗斯": "RU",
    "俄羅斯": "RU",
    "沙特阿拉伯": "SA",
    "新加坡": "SG",
    "斯洛伐克": "SK",
    "斯洛文尼亚": "SI",
    "斯洛文尼亞": "SI",
    "南非": "ZA",
    "韩国": "KR",
    "台灣": "TW",
    "台湾": "TW",
    "大韓民国": "KR",
    "韓国": "KR",
    "西班牙": "ES",
    "斯里兰卡": "LK",
    "斯里蘭卡": "LK",
    "瑞典": "SE",
    "瑞士": "CH",
    "塔吉克斯坦": "TJ",
    "泰国": "TH",
    "泰國": "TH",
    "土耳其": "TR",
    "土耳其共和国": "TR",
    "土耳其共和國": "TR",
    "乌克兰": "UA",
    "烏克蘭": "UA",
    "英国": "GB",
    "英國": "GB",
    "美国": "US",
    "美國": "US",
    "乌兹别克斯坦": "UZ",
    "烏茲別克斯坦": "UZ",
    "越南": "VN",
    "越南社会主义共和国": "VN",
    "越南社會主義共和國": "VN",
    "越南": "VN",
    "也门": "YE",
    "也門": "YE",
}

MAJOR_GROUP_QUERIES = {
    "angiosperms": ("Angiospermae", "Magnoliophyta", "Magnoliopsida"),
    "gymnosperms": ("Acrogymnospermae", "Pinopsida"),
    "lycophytes": ("Lycopodiopsida",),
    "ferns": ("Polypodiopsida",),
    "bryophytes": ("Bryophyta",),
}


@dataclass(frozen=True)
class ResolvedTaxon:
    key: str = ""
    name: str = ""
    rank: str = ""
    message: str = ""


@dataclass
class GbifSearchResult:
    records: list[EvidenceRecord]
    message: str
    total_seen: int = 0
    total_reported: int | None = None
    limit_reached: bool = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def taxon_candidates(taxon_filter: TaxonFilter) -> tuple[str, ...]:
    if taxon_filter.genus:
        return (taxon_filter.genus,)
    if taxon_filter.family:
        return (taxon_filter.family,)
    key = clean_text(taxon_filter.taxon).lower()
    if key in MAJOR_GROUP_QUERIES:
        return MAJOR_GROUP_QUERIES[key]
    return (taxon_filter.taxon,) if taxon_filter.taxon else ()


def desired_rank(taxon_filter: TaxonFilter) -> str:
    if taxon_filter.genus:
        return "GENUS"
    if taxon_filter.family:
        return "FAMILY"
    return ""


def cache_or_fetch_json(
    client: PoliteHttpClient,
    cache_path: Path,
    url: str,
    params: dict[str, object] | None = None,
) -> dict:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    data = client.get_json(url, params=params)
    save_raw_json(cache_path, data)
    return data


def resolve_taxon_key(
    client: PoliteHttpClient,
    taxon_filter: TaxonFilter,
    raw_dir: Path,
) -> ResolvedTaxon:
    candidates = taxon_candidates(taxon_filter)
    if not candidates:
        return ResolvedTaxon(message="GBIF kingdomKey=6 (Plantae)")

    rank = desired_rank(taxon_filter)
    errors: list[str] = []
    for candidate in candidates:
        progress(f"resolving GBIF taxon key for {candidate}")
        params: dict[str, object] = {"name": candidate, "kingdom": "Plantae"}
        if rank:
            params["rank"] = rank
        cache_path = raw_dir / "taxon_match" / f"{safe_token(candidate)}.json"
        data = cache_or_fetch_json(client, cache_path, GBIF_SPECIES_MATCH, params)
        key = value_to_str(data.get("usageKey"))
        status = value_to_str(data.get("status"))
        confidence = int(data.get("confidence") or 0)
        if key and status.upper() != "DOUBTFUL" and confidence >= 80:
            scientific_name = value_to_str(data.get("scientificName") or data.get("canonicalName") or candidate)
            resolved_rank = value_to_str(data.get("rank") or rank)
            return ResolvedTaxon(
                key=key,
                name=scientific_name,
                rank=resolved_rank,
                message=f"GBIF taxonKey={key} ({scientific_name}; confidence={confidence})",
            )
        errors.append(f"{candidate}: no confident GBIF match")

    raise ValueError("; ".join(errors))


def gbif_area_params(area: SearchArea) -> dict[str, object]:
    params: dict[str, object] = {}
    if area.country:
        params["country"] = country_code_for(area.country)
    elif area.state_or_prefecture:
        params["stateProvince"] = area.state_or_prefecture
    if area.latitude is not None and area.longitude is not None and area.radius_km is not None:
        params["geometry"] = circle_wkt(area.latitude, area.longitude, area.radius_km)
        params["hasCoordinate"] = "true"
        params["hasGeospatialIssue"] = "false"
    return params


def gbif_search_params(
    area: SearchArea,
    resolved_taxon: ResolvedTaxon,
    limit: int,
    offset: int,
) -> dict[str, object]:
    params: dict[str, object] = {
        "basisOfRecord": "PRESERVED_SPECIMEN",
        "limit": limit,
        "offset": offset,
    }
    params.update(gbif_area_params(area))
    if resolved_taxon.key:
        params["taxonKey"] = resolved_taxon.key
    else:
        params["kingdomKey"] = PLANTAE_KEY
    return params


def best_gbif_image_media(item: dict) -> dict:
    media_items = item.get("media") or []
    image_media = [
        media
        for media in media_items
        if isinstance(media, dict) and gbif_media_url(media)
    ]
    if not image_media:
        return {}
    return max(image_media, key=gbif_media_score)


def gbif_image_cache_url(item: dict, media: dict, width: int = 800) -> str:
    image_url = gbif_media_url(media)
    key = value_to_str(item.get("key"))
    if not image_url or not key.isdigit():
        return image_url
    if "api.gbif.org/v1/image/cache/" in image_url:
        return image_url
    digest = hashlib.md5(image_url.encode("utf-8")).hexdigest()
    return f"https://api.gbif.org/v1/image/cache/{width}x/occurrence/{key}/media/{digest}"


def best_gbif_image_url(item: dict) -> str:
    media = best_gbif_image_media(item)
    if not media:
        return ""
    return gbif_image_cache_url(item, media)


def cvh_country_code(value: str) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    candidates = (
        CVH_COUNTRY_CODE_ALIASES.get(raw),
        CVH_COUNTRY_CODE_ALIASES.get(normalized_text(raw)),
        canonical_country_code(raw),
        country_code_for(raw),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and re.fullmatch(r"[A-Z]{2}", candidate):
            return candidate
    return ""


def gbif_record_from_item(item: dict) -> EvidenceRecord | None:
    taxon_rank = value_to_str(item.get("taxonRank")).upper()
    if taxon_rank and taxon_rank not in SPECIES_LEVEL_RANKS:
        return None
    accepted_name = value_to_str(
        item.get("acceptedScientificName")
        or item.get("scientificName")
        or item.get("species")
    )
    if is_hybrid_name(accepted_name) or is_hybrid_name(value_to_str(item.get("scientificName"))):
        return None
    canonical = canonical_species_name(accepted_name)
    if not canonical:
        canonical = canonical_species_name(value_to_str(item.get("scientificName") or item.get("species")))
    if not canonical:
        return None
    full_name = accepted_name or canonical
    image_media = best_gbif_image_media(item)
    image_original_url = gbif_media_url(image_media) if image_media else ""
    image_url = gbif_image_cache_url(item, image_media) if image_media else ""
    raw_country = value_to_str(item.get("country"))
    country_code = canonical_country_code(raw_country, value_to_str(item.get("countryCode")))
    return EvidenceRecord(
        source="gbif",
        source_record_id=value_to_str(item.get("key")),
        source_record_url=value_to_str(
            item.get("references")
            or (f"https://www.gbif.org/occurrence/{item.get('key')}" if item.get("key") else "")
            or item.get("occurrenceID")
        ),
        occurrence_id=value_to_str(item.get("occurrenceID")),
        dataset_key=value_to_str(item.get("datasetKey")),
        institution_code=value_to_str(item.get("institutionCode")),
        catalog_number=value_to_str(item.get("catalogNumber")),
        family=value_to_str(item.get("family")),
        genus=canonical.split(" ", 1)[0],
        canonical_species=canonical,
        full_scientific_name=full_name,
        authorship=authorship_from_name(full_name, canonical),
        taxon_rank=taxon_rank,
        taxon_key=value_to_str(item.get("taxonKey")),
        accepted_taxon_key=value_to_str(
            item.get("acceptedTaxonKey") or item.get("speciesKey") or item.get("taxonKey")
        ),
        basis_of_record=value_to_str(item.get("basisOfRecord")),
        country=country_code or raw_country,
        raw_country=raw_country,
        state_province=value_to_str(item.get("stateProvince")),
        county=value_to_str(item.get("county")),
        municipality=value_to_str(item.get("municipality")),
        locality=value_to_str(item.get("locality") or item.get("verbatimLocality")),
        decimal_latitude=value_to_str(item.get("decimalLatitude")),
        decimal_longitude=value_to_str(item.get("decimalLongitude")),
        image_url=image_url,
        image_original_url=image_original_url,
        image_license=value_to_str(image_media.get("license") or item.get("license")),
        rights_holder=value_to_str(image_media.get("rightsHolder") or item.get("rightsHolder")),
        accessed_at=now_iso(),
    )


def gbif_region_records(
    client: PoliteHttpClient,
    area: SearchArea,
    taxon_filter: TaxonFilter,
    raw_dir: Path,
    max_records: int | None,
    page_size: int,
    request_delay_seconds: float,
) -> GbifSearchResult:
    resolved_taxon = resolve_taxon_key(client, taxon_filter, raw_dir)
    limit = max(1, min(page_size, 300))
    records: list[EvidenceRecord] = []
    seen_keys: set[str] = set()
    offset = 0
    total_reported: int | None = None
    limit_reached = False

    while True:
        if max_records is not None and offset >= max_records:
            limit_reached = True
            break
        page_limit = limit
        if max_records is not None:
            page_limit = min(page_limit, max_records - offset)
        cache_path = raw_dir / "gbif_occurrence" / f"offset_{offset:07d}.json"
        with Heartbeat(f"GBIF regional occurrence search offset {offset}"):
            data = cache_or_fetch_json(
                client,
                cache_path,
                GBIF_OCCURRENCE_SEARCH,
                gbif_search_params(area, resolved_taxon, page_limit, offset),
            )
        results = data.get("results", [])
        if total_reported is None and isinstance(data.get("count"), int):
            total_reported = int(data["count"])
        if not isinstance(results, list) or not results:
            break

        for item in results:
            if not isinstance(item, dict):
                continue
            record = gbif_record_from_item(item)
            if record is None or not record_matches_area(record, area):
                continue
            identity = record.identity_key()
            if identity in seen_keys:
                continue
            seen_keys.add(identity)
            records.append(record)

        offset += len(results)
        progress(f"GBIF regional occurrence search retained {len(records)} records after scanning {offset}")
        if bool(data.get("endOfRecords")) or len(results) < page_limit:
            break
        if max_records is not None and offset >= max_records:
            limit_reached = True
            break
        client.sleep(request_delay_seconds)

    message = resolved_taxon.message
    if limit_reached:
        message += f"; stopped after scanning --limit {max_records} GBIF records"
    return GbifSearchResult(
        records=records,
        message=message,
        total_seen=offset,
        total_reported=total_reported,
        limit_reached=limit_reached,
    )


def build_species_entries(records: list[EvidenceRecord]) -> list[SpeciesEntry]:
    entries: dict[str, SpeciesEntry] = {}
    total_records = len(records)
    progress(f"building species checklist from {total_records} GBIF specimen records")
    for index, record in enumerate(records, start=1):
        if not record.canonical_species:
            continue
        entry = entries.setdefault(
            record.canonical_species,
            SpeciesEntry(canonical_species=record.canonical_species),
        )
        entry.add_evidence(record)
        if index == 1 or index == total_records or index % 100 == 0:
            progress_bar(index, total_records, "records")
    species = sorted(entries.values(), key=lambda entry: entry.sort_key())
    for index, _entry in enumerate(species, start=1):
        progress_bar(index, len(species), "species")
    return species


def gbif_bulk_image_area_params(area: SearchArea, max_radius_km: float) -> dict[str, object]:
    params: dict[str, object] = {}
    if (
        max_radius_km > 0
        and area.latitude is not None
        and area.longitude is not None
        and area.radius_km is not None
    ):
        radius = max(area.radius_km, max_radius_km)
        params["geometry"] = circle_wkt(area.latitude, area.longitude, radius)
        params["hasCoordinate"] = "true"
        params["hasGeospatialIssue"] = "false"
        return params
    params.update(gbif_area_params(area))
    return params


def gbif_candidate_pool_target(limit_per_species: int) -> int:
    if limit_per_species <= 0:
        return 0
    return min(limit_per_species, MAX_IMAGE_CANDIDATES * 3)


def gbif_bulk_scan_cap(species_count: int, target_per_species: int, limit_per_species: int) -> int:
    if species_count <= 0 or target_per_species <= 0 or limit_per_species <= 0:
        return 0
    return min(50_000, max(3_000, species_count * limit_per_species))


def add_gbif_image_record_to_entry(
    entry: SpeciesEntry,
    record: EvidenceRecord,
    ranking_area: SearchArea,
    limit_per_species: int,
) -> bool:
    if image_candidate_count(entry, source="gbif") >= limit_per_species:
        return False
    before = image_candidate_count(entry, source="gbif")
    entry.add_image_candidate(
        "gbif",
        record.image_url,
        record.image_license,
        record.rights_holder,
        record.image_original_url,
        record.institution_code,
        record.catalog_number,
        record.country,
        record.state_province,
        record.decimal_latitude,
        record.decimal_longitude,
        image_candidate_distance_text(record, ranking_area),
        record.source_record_id,
        record.basis_of_record,
        record.county,
        record.municipality,
        raw_country=record.raw_country,
    )
    return image_candidate_count(entry, source="gbif") > before


def gbif_entries_reaching_target(
    species_entries: list[SpeciesEntry],
    target_per_species: int,
) -> bool:
    if target_per_species <= 0:
        return True
    return all(image_candidate_count(entry, source="gbif") >= target_per_species for entry in species_entries)


def gbif_underfilled_summary(
    species_entries: list[SpeciesEntry],
    target_per_species: int,
    *,
    source: str = "gbif",
    max_items: int = 8,
) -> str:
    underfilled = [
        f"{entry.canonical_species}={image_candidate_count(entry, source=source)}/{target_per_species}"
        for entry in species_entries
        if image_candidate_count(entry, source=source) < target_per_species
    ]
    if not underfilled:
        return ""
    visible = ", ".join(underfilled[:max_items])
    if len(underfilled) > max_items:
        visible += f", ... +{len(underfilled) - max_items} more"
    return visible


def add_gbif_bulk_image_candidates(
    client: PoliteHttpClient,
    species_entries: list[SpeciesEntry],
    area: SearchArea,
    taxon_filter: TaxonFilter,
    raw_dir: Path,
    limit_per_species: int,
    max_radius_km: float,
    request_delay_seconds: float,
    distance_area: SearchArea | None = None,
) -> tuple[int, str]:
    resolved_taxon = resolve_taxon_key(client, taxon_filter, raw_dir / "taxon_match")
    species_by_name = {entry.canonical_species: entry for entry in species_entries}
    ranking_area = distance_area or area
    limit = 300
    target_per_species = gbif_candidate_pool_target(limit_per_species)
    scan_cap = gbif_bulk_scan_cap(len(species_entries), target_per_species, limit_per_species)
    offset = 0
    total_reported: int | None = None
    retained_records = 0
    limit_reached = False
    target_reached = False
    seen: set[str] = set()
    progress(f"GBIF bulk image search preferred radius: {max_radius_km:g} km")
    while True:
        if scan_cap and offset >= scan_cap:
            limit_reached = True
            break
        page_limit = limit if not scan_cap else min(limit, scan_cap - offset)
        params: dict[str, object] = {
            "basisOfRecord": "PRESERVED_SPECIMEN",
            "mediaType": "StillImage",
            "limit": page_limit,
            "offset": offset,
        }
        params.update(gbif_bulk_image_area_params(area, max_radius_km))
        if resolved_taxon.key:
            params["taxonKey"] = resolved_taxon.key
        else:
            params["kingdomKey"] = PLANTAE_KEY
        cache_path = raw_dir / "bulk_images" / f"offset_{offset:07d}.json"
        with Heartbeat(f"GBIF bulk image search offset {offset}", announce_immediately=False):
            data = cache_or_fetch_json(client, cache_path, GBIF_OCCURRENCE_SEARCH, params)
        rows = data.get("results", [])
        if total_reported is None and isinstance(data.get("count"), int):
            total_reported = int(data["count"])
            progress(f"GBIF bulk image search reported {total_reported} image records")
        if not isinstance(rows, list) or not rows:
            break
        for item in rows:
            if not isinstance(item, dict):
                continue
            record = gbif_record_from_item(item)
            if record is None or not record.image_url:
                continue
            entry = species_by_name.get(record.canonical_species)
            if entry is None:
                continue
            identity = record.identity_key()
            if identity in seen:
                continue
            seen.add(identity)
            if add_gbif_image_record_to_entry(entry, record, ranking_area, limit_per_species):
                retained_records += 1
        offset += len(rows)
        progress(
            f"GBIF bulk image search retained {retained_records} candidate records after scanning {offset}"
        )
        if gbif_entries_reaching_target(species_entries, target_per_species):
            target_reached = True
            break
        if bool(data.get("endOfRecords")) or len(rows) < page_limit:
            break
        client.sleep(request_delay_seconds)
    species_with_candidates = sum(
        1
        for entry in species_entries
        if any(candidate.get("source", "").lower() == "gbif" for candidate in entry.image_candidates)
    )
    message = (
        f"complete; bulk image search scanned {offset}"
        f" of {total_reported if total_reported is not None else 'unknown'} GBIF image records"
    )
    if target_reached:
        message += f"; early stop after all species reached {target_per_species} GBIF candidate records"
    if limit_reached and total_reported is not None and total_reported > offset:
        message = (
            f"partial: bulk image search stopped after scan cap {scan_cap} records"
            f" of {total_reported}; underfilled: "
            f"{gbif_underfilled_summary(species_entries, target_per_species)}"
        )
    return species_with_candidates, message


def image_candidate_count(entry: SpeciesEntry, source: str = "") -> int:
    seen: set[tuple[str, str, str]] = set()
    for candidate in entry.image_candidates:
        if source and candidate.get("source", "").lower() != source.lower():
            continue
        image_url = candidate.get("image_url", "")
        if not image_url:
            continue
        seen.add(
            (
                candidate.get("source", "").lower(),
                candidate.get("source_record_id", ""),
                image_url,
            )
        )
    return len(seen)


def image_candidate_distance_text(record: EvidenceRecord, area: SearchArea) -> str:
    if area.latitude is None or area.longitude is None:
        return ""
    try:
        lat = float(record.decimal_latitude)
        lon = float(record.decimal_longitude)
    except ValueError:
        return ""
    return f"{distance_km(area.latitude, area.longitude, lat, lon):.3f}"


def gbif_species_taxon_key(
    client: PoliteHttpClient,
    entry: SpeciesEntry,
    raw_dir: Path,
) -> str:
    key = entry.accepted_taxon_key or entry.taxon_key
    if key and key.isdigit():
        return key
    cache_path = raw_dir / "species_taxon_match" / f"{safe_token(entry.canonical_species)}.json"
    data = cache_or_fetch_json(
        client,
        cache_path,
        GBIF_SPECIES_MATCH,
        {"name": entry.canonical_species, "kingdom": "Plantae", "rank": "SPECIES"},
    )
    usage_key = value_to_str(data.get("usageKey"))
    confidence = int(data.get("confidence") or 0)
    status = value_to_str(data.get("status")).upper()
    return usage_key if usage_key.isdigit() and status != "DOUBTFUL" and confidence >= 80 else ""


def add_gbif_species_image_top_up(
    client: PoliteHttpClient,
    species_entries: list[SpeciesEntry],
    area: SearchArea,
    raw_dir: Path,
    limit_per_species: int,
    request_delay_seconds: float,
) -> tuple[int, str]:
    if limit_per_species <= 0:
        return 0, "skipped"
    target_per_species = min(MAX_IMAGE_CANDIDATES, limit_per_species)
    targets = [
        entry
        for entry in species_entries
        if image_candidate_count(entry, source="gbif") < target_per_species
    ]
    if not targets:
        return 0, "not needed"

    scopes: list[tuple[str, dict[str, object]]] = []
    if area.country:
        code = country_code_for(area.country)
        scopes.append(("country", {"country": code}))
    scopes.append(("global", {}))

    found = 0
    summaries: list[str] = []
    page_size = 300
    for index, entry in enumerate(targets, start=1):
        before_entry = image_candidate_count(entry, source="gbif")
        progress(
            f"GBIF exact-taxonomic image top-up {index}/{len(targets)}: {entry.canonical_species}"
        )
        taxon_key = gbif_species_taxon_key(client, entry, raw_dir)
        if not taxon_key:
            summaries.append(f"{entry.canonical_species}: no confident GBIF species key")
            continue
        for scope_name, scope_params in scopes:
            offset = 0
            scope_scanned = 0
            while image_candidate_count(entry, source="gbif") < target_per_species:
                remaining_scan = max(0, limit_per_species - scope_scanned)
                if remaining_scan <= 0:
                    break
                page_limit = min(page_size, remaining_scan)
                params: dict[str, object] = {
                    "basisOfRecord": "PRESERVED_SPECIMEN",
                    "mediaType": "StillImage",
                    "taxonKey": taxon_key,
                    "limit": page_limit,
                    "offset": offset,
                }
                params.update(scope_params)
                cache_path = raw_dir / safe_token(entry.canonical_species) / scope_name / f"offset_{offset:07d}.json"
                label = (
                    f"GBIF exact-taxonomic image top-up {index}/{len(targets)} "
                    f"{scope_name} offset {offset}: {entry.canonical_species}"
                )
                with Heartbeat(label, announce_immediately=False):
                    data = cache_or_fetch_json(client, cache_path, GBIF_OCCURRENCE_SEARCH, params)
                rows = data.get("results", [])
                if not isinstance(rows, list) or not rows:
                    break
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    record = gbif_record_from_item(item)
                    if record is None or not record.image_url:
                        continue
                    if record.canonical_species != entry.canonical_species:
                        continue
                    if add_gbif_image_record_to_entry(entry, record, area, limit_per_species):
                        found += 1
                    if image_candidate_count(entry, source="gbif") >= target_per_species:
                        break
                scope_scanned += len(rows)
                offset += len(rows)
                progress(
                    f"GBIF exact-taxonomic image top-up retained "
                    f"{image_candidate_count(entry, source='gbif')} candidates for {entry.canonical_species}"
                )
                if bool(data.get("endOfRecords")) or len(rows) < page_limit:
                    break
                client.sleep(request_delay_seconds)
            if image_candidate_count(entry, source="gbif") >= target_per_species:
                break
        after_entry = image_candidate_count(entry, source="gbif")
        if after_entry < target_per_species:
            summaries.append(
                f"{entry.canonical_species}: {after_entry}/{target_per_species} exact-match GBIF image candidates"
            )
        elif after_entry > before_entry:
            summaries.append(f"{entry.canonical_species}: +{after_entry - before_entry}")

    message = f"attempted for {len(targets)} underfilled species"
    if summaries:
        message += "; " + "; ".join(summaries[:12])
        if len(summaries) > 12:
            message += f"; ... +{len(summaries) - 12} more"
    return found, message


def add_gbif_image_candidates(
    client: PoliteHttpClient,
    species_entries: list[SpeciesEntry],
    area: SearchArea,
    taxon_filter: TaxonFilter,
    raw_dir: Path,
    limit_per_species: int,
    max_radius_km: float,
    request_delay_seconds: float,
) -> tuple[int, str]:
    if limit_per_species <= 0:
        return 0, "skipped: --gbif-image-limit is 0"
    messages: list[str] = []
    found_images, message = add_gbif_bulk_image_candidates(
        client=client,
        species_entries=species_entries,
        area=area,
        taxon_filter=taxon_filter,
        raw_dir=raw_dir / "preferred_area",
        limit_per_species=limit_per_species,
        max_radius_km=max_radius_km,
        request_delay_seconds=request_delay_seconds,
        distance_area=area,
    )
    messages.append(message)
    if (
        area.latitude is not None
        and area.longitude is not None
        and area.radius_km is not None
        and area.country
        and any(image_candidate_count(entry) < MAX_IMAGE_CANDIDATES for entry in species_entries)
    ):
        country_area = SearchArea(country=area.country)
        country_found, country_message = add_gbif_bulk_image_candidates(
            client=client,
            species_entries=species_entries,
            area=country_area,
            taxon_filter=taxon_filter,
            raw_dir=raw_dir / "country_area",
            limit_per_species=limit_per_species,
            max_radius_km=0,
            request_delay_seconds=request_delay_seconds,
            distance_area=area,
        )
        found_images = max(found_images, country_found)
        messages.append(f"country fallback: {country_message}")
    if any(image_candidate_count(entry, source="gbif") < min(MAX_IMAGE_CANDIDATES, limit_per_species) for entry in species_entries):
        top_up_found, top_up_message = add_gbif_species_image_top_up(
            client=client,
            species_entries=species_entries,
            area=area,
            raw_dir=raw_dir / "species_top_up",
            limit_per_species=limit_per_species,
            request_delay_seconds=request_delay_seconds,
        )
        _ = top_up_found
        messages.append(f"exact-taxonomic top-up: {top_up_message}")
    found_images = sum(
        1
        for entry in species_entries
        if any(candidate.get("source", "").lower() == "gbif" for candidate in entry.image_candidates)
    )
    status = "partial" if any(message.startswith("partial:") for message in messages) else "complete"
    return found_images, f"{status}; " + " | ".join(messages)


def add_cvh_image_candidates_for_entry(
    *,
    client: PoliteHttpClient,
    entry: SpeciesEntry,
    raw_dir: Path,
    cvh_settings: dict,
    max_records: int,
    record_offset: int,
    progress_label: str = "",
) -> tuple[int, int]:
    if max_records <= 0:
        return 0, 0
    label = progress_label or f"CVH image top-up: {entry.canonical_species}"
    with Heartbeat(label, announce_immediately=False):
        records = cvh_records(
            client=client,
            query_name=entry.canonical_species,
            raw_dir=raw_dir / safe_token(entry.canonical_species),
            settings=cvh_settings,
            max_records=max_records,
            record_offset=record_offset,
            refresh=True,
        )
    added = 0
    for record in records:
        if canonical_species_name(record.scientific_name) != entry.canonical_species:
            continue
        if not record.image_url:
            continue
        raw_country = record.country
        country = cvh_country_code(raw_country)
        before = image_candidate_count(entry, source="cvh")
        entry.add_image_candidate(
            "cvh",
            record.image_url,
            record.image_license,
            record.rights_holder,
            record.original_image_url,
            record.institution_code,
            record.catalog_number,
            country,
            record.state_province,
            record.decimal_latitude,
            record.decimal_longitude,
            "",
            record.source_record_id,
            record.basis_of_record,
            raw_country=raw_country,
        )
        if image_candidate_count(entry, source="cvh") > before:
            added += 1
    return added, len(records)


def add_cvh_image_candidates(
    client: PoliteHttpClient,
    species_entries: list[SpeciesEntry],
    area: SearchArea,
    raw_dir: Path,
    cvh_settings: dict,
    max_records_per_species: int,
    target_candidates_per_species: int = MAX_IMAGE_CANDIDATES,
) -> tuple[int, str]:
    if max_records_per_species <= 0:
        return 0, "skipped: --cvh-image-limit is 0"
    found_images = 0
    for index, entry in enumerate(species_entries, start=1):
        remaining = max(0, target_candidates_per_species - image_candidate_count(entry))
        if remaining <= 0:
            continue
        progress_bar(index, len(species_entries), "species")
        progress(f"CVH image top-up {index}/{len(species_entries)}: {entry.canonical_species}")
        try:
            added, _seen_records = add_cvh_image_candidates_for_entry(
                client=client,
                entry=entry,
                raw_dir=raw_dir,
                cvh_settings=cvh_settings,
                max_records=min(max_records_per_species, remaining),
                record_offset=0,
                progress_label=f"CVH image top-up {index}/{len(species_entries)}: {entry.canonical_species}",
            )
        except Exception as exc:
            return found_images, f"partial: CVH image search failed for {entry.canonical_species}: {exc}"
        found_images += added
    return found_images, "complete"
