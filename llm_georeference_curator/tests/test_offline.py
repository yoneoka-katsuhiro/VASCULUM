from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from georeference_curator.geocoding import (
    extract_decimal_coordinates,
    is_precise_label_coordinate,
)
from georeference_curator.elevation import parse_elevation_meters, round_elevation
from georeference_curator.geospatial_refinement import (
    EnvironmentalFeature,
    GeospatialContext,
    GeospatialRefinementSettings,
    RoutePoint,
    classify_environment_tags,
    refine_llm_candidates,
)
from georeference_curator.habitat import habitat_vocabulary, parse_habitats
from georeference_curator.labels import detect_languages
from georeference_curator.llm import (
    LlmSettings,
    build_transcription_prompt,
    llm_response_to_candidates,
    model_candidates_for,
    normalize_provider,
    search_policy,
)
from georeference_curator.llm_cache import LlmResponseCache
from georeference_curator.models import LabelRead
from georeference_curator.parallel import RateLimitBackoff, resolve_worker_count
from georeference_curator.pipeline import process_row, run_pipeline
from georeference_curator.progress import TerminalProgress, progress_bar
from georeference_curator.scoring import (
    SelectionOptions,
    original_coordinate_status,
    select_result,
)


def write_rows(path: Path, rows, delimiter: str = "\t") -> None:
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path, delimiter: str = "\t"):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def run_test_pipeline(input_dir: Path, output_dir: Path, curation_mode: str):
    return run_pipeline(
        project_dir=ROOT,
        input_dir=input_dir,
        input_dwc=None,
        output_dir=output_dir,
        label_tsv=input_dir / "labels.tsv",
        gazetteer_tsv=input_dir / "gazetteer.tsv",
        dry_run=False,
        curation_mode=curation_mode,
        original_precision_decimals=4,
        review_distance_km=5.0,
        exclude_insufficient_locality=True,
        llm_mode="off",
        llm_provider="codex-cli",
        llm_model="auto",
        llm_reasoning_effort="",
        llm_web_search="live",
        llm_command="",
        llm_api_key_env="OPENAI_API_KEY",
        llm_timeout_seconds=120,
        llm_rate_limit_retries=0,
        confirm_llm=False,
        georeferenced_by="test",
        prompt_profile="xie-modified",
        use_trails=True,
        use_hydrology=True,
        use_dem=True,
        use_vegetation_prior=True,
        taxon_habitat="fern",
        debug_log=True,
        limit=0,
        workers="1",
        progress=TerminalProgress(enabled=False),
    )


def main() -> None:
    assert progress_bar(0, 149) == "[--------------------]"
    assert progress_bar(13, 149) == "[==------------------]"
    assert progress_bar(149, 149) == "[====================]"
    assert detect_languages("TAIWAN: Formosa: Mt. Arisan 阿里山") == ["zh", "en/la"]
    assert normalize_provider("codex") == "codex-cli"
    assert normalize_provider("opus") == "opus5"
    assert model_candidates_for(LlmSettings(provider="codex-cli", model="auto"))[0] == "gpt-5.5"
    assert resolve_worker_count(
        "auto",
        project_dir=ROOT,
        provider="codex-cli",
        model_label="gpt-5.5",
        web_search_mode="indexed",
        records=10,
    ) >= 2
    assert resolve_worker_count(
        "max",
        project_dir=ROOT,
        provider="codex-cli",
        model_label="gpt-5.5",
        web_search_mode="indexed",
        records=10,
    ) >= 4
    coordinates = extract_decimal_coordinates("TWD 67: N 23° 27′ E 120° 55′ Alt. 3050 m")
    assert len(coordinates) == 1
    assert abs(coordinates[0].latitude - 23.45) < 0.0001
    assert abs(coordinates[0].longitude - 120.9166667) < 0.0001
    assert coordinates[0].datum == "TWD67"
    assert coordinates[0].uncertainty_meters == 2000
    assert coordinates[0].precision_kind == "dms_minutes"
    habitat = parse_habitats(["subalpine forest", "river", "ultramafic"])
    assert habitat.canonical == ("subalpine forest", "river", "ultramafic")
    assert habitat.elevation_floor_meters == 600
    assert "forest" in habitat.expected_classes
    assert "built_up" in habitat.avoided_classes
    assert {"sea", "pond", "desert", "beach", "limestone"}.issubset(
        set(habitat_vocabulary())
    )
    assert "built_up" in classify_environment_tags({"landuse": "residential"})
    assert {"river", "water"}.issubset(
        set(classify_environment_tags({"waterway": "stream"}))
    )
    assert {"desert", "sand"}.issubset(
        set(classify_environment_tags({"natural": "dune"}))
    )
    assert "ultramafic" in classify_environment_tags(
        {"geological": "serpentinite"}
    )
    no_false_seconds = extract_decimal_coordinates(
        "N 23°31′ E 120°48′\n2290 m"
    )
    assert len(no_false_seconds) == 1
    assert abs(no_false_seconds[0].longitude - 120.8) < 0.0001
    seconds_coordinate = extract_decimal_coordinates(
        "N 23°31′22″ E 120°48′30″"
    )
    assert len(seconds_coordinate) == 1
    assert seconds_coordinate[0].uncertainty_meters == 30
    assert is_precise_label_coordinate(seconds_coordinate[0])
    suffix_coordinate = extract_decimal_coordinates(
        "23°31′22″ N, 120°48′30″ E"
    )
    assert len(suffix_coordinate) == 1
    assert abs(suffix_coordinate[0].latitude - 23.5227778) < 0.000001
    assert abs(suffix_coordinate[0].longitude - 120.8083333) < 0.000001
    assert is_precise_label_coordinate(suffix_coordinate[0])
    assert not is_precise_label_coordinate(no_false_seconds[0])
    assert parse_elevation_meters("Alt. 6500 ft") == (1981, 1981)
    assert parse_elevation_meters("6000-6500 feet") == (1829, 1981)
    assert parse_elevation_meters("2040 m") == (2040, 2040)
    assert round_elevation(2285, 10) == 2290

    label = LabelRead(
        catalog_number="P01185529",
        label_transcription="Taiwan JiaYi County ZiZhong, 3050 m, N 23°27′ E 120°55′",
        locality_text="Taiwan JiaYi County ZiZhong",
        label_status="llm_image_transcribed",
    )
    assert search_policy(
        {"country": "Japan", "locality": "長野県川上村"},
        LabelRead(detected_languages=["ja/zh"], label_transcription="長野県川上村"),
    )["chineseFallbackAllowed"] is False
    assert search_policy(
        {"country": "Chinese Taipei", "locality": "阿里山"},
        LabelRead(detected_languages=["zh"], label_transcription="阿里山"),
    )["chineseFallbackAllowed"] is True
    transcription_prompt = build_transcription_prompt(
        {"catalogNumber": "TEST", "country": "Japan"},
        LabelRead(image_quality_status="image_available"),
    )
    assert "transcription_only" in transcription_prompt
    assert "Do not browse the web" in transcription_prompt

    with tempfile.TemporaryDirectory() as cache_tmpdir:
        cache = LlmResponseCache(Path(cache_tmpdir))
        cache_key = cache.key(
            purpose="transcription_only",
            provider="codex-cli",
            model="gpt-test",
            reasoning_effort="medium",
            web_search_mode="disabled",
            prompt=transcription_prompt,
            image_paths=[],
        )
        assert cache.get(cache_key) is None
        cache.put(cache_key, {"status": "ok"})
        assert cache.get(cache_key) == {"status": "ok"}
    direct_label_result = select_result(
        row={
            "catalogNumber": "DIRECTCOORD",
            "decimalLatitude": "",
            "decimalLongitude": "",
        },
        label=LabelRead(
            catalog_number="DIRECTCOORD",
            label_transcription="23°31′22″ N, 120°48′30″ E",
            locality_text="specific collecting locality",
            label_status="transcribed",
        ),
        raw_label_coordinates=suffix_coordinate,
        llm_candidates=[],
        gazetteer_matches=[],
        insufficient_locality="",
        exclude_insufficient_locality=True,
        options=SelectionOptions(curation_mode="robust"),
    )
    assert direct_label_result.row["decimalLatitude"] == "23.522778"
    assert direct_label_result.row["decimalLongitude"] == "120.808333"
    assert direct_label_result.verification_status == "selected_label_coordinate"

    class FakeClient:
        def __init__(self, response):
            self.response = response
            self.calls = 0

        def create_json(self, _prompt, image_paths=None):
            self.calls += 1
            return dict(self.response)

    with tempfile.TemporaryDirectory() as routing_tmpdir:
        routing_dir = Path(routing_tmpdir).resolve()
        image_dir = routing_dir / "images"
        image_dir.mkdir()
        (image_dir / "DIRECT_IMAGE.jpg").write_bytes(b"jpg")
        transcription_client = FakeClient(
            {
                "status": "ok",
                "detectedLanguages": ["en"],
                "labelTranscription": (
                    "Specific mountain trail. 23°31′22″ N, 120°48′30″ E"
                ),
                "localityText": "Specific mountain trail",
                "eventDateText": "",
                "collectorText": "",
                "elevationText": "",
                "localityMentions": ["Specific mountain trail"],
                "coordinateCandidates": [],
                "remarks": "",
            }
        )
        georeference_client = FakeClient({})
        georeference_settings = LlmSettings(
            mode="on",
            provider="codex-cli",
            model="gpt-test",
            reasoning_effort="high",
            timeout_seconds=600,
            web_search_mode="live",
        )
        transcription_settings = LlmSettings(
            mode="on",
            provider="codex-cli",
            model="gpt-test",
            reasoning_effort="medium",
            timeout_seconds=600,
            web_search_mode="disabled",
        )
        routing_outcome = process_row(
            index=1,
            total_rows=1,
            row={
                "catalogNumber": "DIRECT_IMAGE",
                "country": "Chinese Taipei",
                "locality": "Specific mountain trail",
                "verbatimLocality": "Specific mountain trail",
                "decimalLatitude": "",
                "decimalLongitude": "",
                "associatedMedia": "images/DIRECT_IMAGE.jpg",
            },
            source_dir=routing_dir,
            sidecars={},
            gazetteer=[],
            llm_client=georeference_client,
            llm_settings=georeference_settings,
            transcription_llm_client=transcription_client,
            transcription_llm_settings=transcription_settings,
            llm_cache=LlmResponseCache(routing_dir / "cache", enabled=False),
            llm_rate_limit_retries=0,
            llm_backoff=RateLimitBackoff(retries=0),
            refinement_settings=GeospatialRefinementSettings(enabled=False),
            verification_timeout_seconds=600,
            habitat_preference=parse_habitats("subalpine forest"),
            normalized_habitat="subalpine forest",
            original_precision_decimals=4,
            exclude_insufficient_locality=True,
            options=SelectionOptions(curation_mode="robust"),
            prompt_profile="xie-modified",
            use_trails=True,
            use_hydrology=True,
            use_dem=True,
            use_vegetation_prior=True,
            progress=TerminalProgress(enabled=False),
        )
        assert transcription_client.calls == 1
        assert georeference_client.calls == 0
        assert routing_outcome.llm_skipped_precise_label == 1
        assert routing_outcome.result.row["decimalLatitude"] == "23.522778"
    llm_candidates = llm_response_to_candidates(
        {
            "coordinateCandidates": [
                {
                    "latitude": "23.48458",
                    "longitude": "120.8306",
                    "geodeticDatum": "WGS84",
                    "uncertaintyMeters": "800",
                    "elevationMeters": "3050",
                    "candidateType": "refined_georeference",
                    "modernPlaceName": "自忠, Taiwan",
                    "historicalPlaceName": "Eryu (Kodama)",
                    "matchLanguage": "zh",
                    "sourceUrls": ["https://example.org/zizhong"],
                    "evidenceLayers": ["local_language_toponym", "elevation", "trail"],
                    "evidence": "Matched the locality and elevation along the historic trail.",
                    "score": "0.88",
                    "remarks": "Web-researched point estimate.",
                }
            ]
        },
        label,
        "gpt-test",
    )
    assert llm_candidates[0].candidate_latitude == "23.484580"
    assert llm_candidates[0].candidate_longitude == "120.830600"
    robust_selection = select_result(
        row={
            "catalogNumber": "P01185529",
            "decimalLatitude": "23.45",
            "decimalLongitude": "120.91667",
        },
        label=label,
        raw_label_coordinates=coordinates,
        llm_candidates=llm_candidates,
        gazetteer_matches=[],
        insufficient_locality="",
        exclude_insufficient_locality=True,
        options=SelectionOptions(curation_mode="robust"),
    )
    assert robust_selection.selected_candidate is llm_candidates[0]
    assert robust_selection.row["decimalLatitude"] == "23.484580"
    assert robust_selection.row["decimalLongitude"] == "120.830600"
    assert "review_refined_candidate_conflicts_verbatim_coordinate" in robust_selection.verification_status

    web_anchor = llm_response_to_candidates(
        {
            "coordinateCandidates": [
                {
                    "latitude": "23.48408",
                    "longitude": "120.83025",
                    "geodeticDatum": "WGS84",
                    "uncertaintyMeters": "2000",
                    "elevationMeters": "2282",
                    "candidateType": "place_centroid",
                    "modernPlaceName": "自忠 trail entrance",
                    "historicalPlaceName": "兒玉",
                    "matchLanguage": "zh",
                    "sourceUrls": ["https://example.org/trail"],
                    "evidenceLayers": ["historical_toponym", "trail", "elevation"],
                    "evidence": "Official trail entrance for the specific locality.",
                    "score": "0.63",
                    "remarks": "Specific locality anchor.",
                }
            ]
        },
        label,
        "gpt-test",
    )
    anchor_selection = select_result(
        row={
            "catalogNumber": "P01185529",
            "decimalLatitude": "23.45",
            "decimalLongitude": "120.91667",
        },
        label=label,
        raw_label_coordinates=coordinates,
        llm_candidates=web_anchor,
        gazetteer_matches=[],
        insufficient_locality="",
        exclude_insufficient_locality=True,
        options=SelectionOptions(curation_mode="robust"),
    )
    assert anchor_selection.row["decimalLatitude"] == "23.484080"
    assert anchor_selection.row["minimumElevationInMeters"] == "3050"
    assert anchor_selection.verification_status == "review_selected_llm_web_locality_anchor"

    route_points = [
        RoutePoint(
            latitude=23.484123,
            longitude=120.830456,
            way_id=12345,
            tags={"highway": "path", "name": "特富野古道"},
        ),
        RoutePoint(
            latitude=23.490987,
            longitude=120.842345,
            way_id=12346,
            tags={"highway": "track", "name": "林道"},
        ),
    ]

    def fake_context(_latitude, _longitude, _radius, _settings):
        return GeospatialContext(routes=route_points)

    def fake_elevations(points, _settings):
        return [2285.0 if point.way_id == 12345 else 2600.0 for point in points]

    refinement = refine_llm_candidates(
        web_anchor,
        label,
        GeospatialRefinementSettings(enabled=True),
        context_fetcher=fake_context,
        elevation_fetcher=fake_elevations,
    )
    assert refinement.attempted
    assert refinement.candidate is not None
    assert refinement.candidate.candidate_type == "route_dem_refinement"
    assert refinement.candidate.candidate_latitude == "23.484123"
    assert refinement.candidate.candidate_longitude == "120.830456"
    assert refinement.candidate.candidate_elevation_meters == "2290"
    assert refinement.candidate.candidate_uncertainty_meters == "500"
    assert "openstreetmap.org/way/12345" in refinement.candidate.source_urls

    route_selection = select_result(
        row={
            "catalogNumber": "P01185529",
            "decimalLatitude": "23.45",
            "decimalLongitude": "120.91667",
        },
        label=label,
        raw_label_coordinates=coordinates,
        llm_candidates=[*web_anchor, refinement.candidate],
        gazetteer_matches=[],
        insufficient_locality="",
        exclude_insufficient_locality=True,
        options=SelectionOptions(curation_mode="robust"),
    )
    assert route_selection.selected_candidate is refinement.candidate
    assert route_selection.row["decimalLatitude"] == "23.484123"
    assert route_selection.row["minimumElevationInMeters"] == "3050"
    assert route_selection.verification_status.startswith("review_route_dem")

    city_point = RoutePoint(
        latitude=23.484090,
        longitude=120.830260,
        way_id=20001,
        tags={"highway": "path"},
    )
    forest_point = RoutePoint(
        latitude=23.486000,
        longitude=120.832000,
        way_id=20002,
        tags={"highway": "path"},
    )
    city_polygon = EnvironmentalFeature(
        osm_type="way",
        osm_id=30001,
        classes=("built_up",),
        geometry=(
            (23.4838, 120.8300),
            (23.4843, 120.8300),
            (23.4843, 120.8305),
            (23.4838, 120.8305),
            (23.4838, 120.8300),
        ),
    )
    forest_polygon = EnvironmentalFeature(
        osm_type="way",
        osm_id=30002,
        classes=("forest",),
        geometry=(
            (23.4857, 120.8317),
            (23.4863, 120.8317),
            (23.4863, 120.8323),
            (23.4857, 120.8323),
            (23.4857, 120.8317),
        ),
    )

    def habitat_context(_latitude, _longitude, _radius, _settings):
        return GeospatialContext(
            routes=[city_point, forest_point],
            environment=[city_polygon, forest_polygon],
        )

    habitat_refinement = refine_llm_candidates(
        web_anchor,
        label,
        GeospatialRefinementSettings(enabled=True),
        habitat_preference=parse_habitats("subalpine forest"),
        context_fetcher=habitat_context,
        elevation_fetcher=lambda points, _settings: [2282.0 for _point in points],
    )
    assert habitat_refinement.candidate is not None
    assert habitat_refinement.candidate.candidate_latitude == "23.486000"
    assert "habitat_prior" in habitat_refinement.candidate.evidence_layers
    assert "subalpine forest" in habitat_refinement.candidate.evidence

    city_llm_candidate = llm_response_to_candidates(
        {
            "coordinateCandidates": [
                {
                    "latitude": "23.48409",
                    "longitude": "120.83026",
                    "geodeticDatum": "WGS84",
                    "uncertaintyMeters": "500",
                    "elevationMeters": "50",
                    "candidateType": "refined_georeference",
                    "modernPlaceName": "urban locality",
                    "sourceUrls": ["https://example.org/urban"],
                    "evidenceLayers": ["gazetteer"],
                    "evidence": "Source-backed urban point.",
                    "score": "0.80",
                }
            ]
        },
        label,
        "gpt-test",
    )[0]

    city_rejection = refine_llm_candidates(
        [city_llm_candidate],
        label,
        GeospatialRefinementSettings(enabled=True),
        habitat_preference=parse_habitats("subalpine forest"),
        context_fetcher=lambda *_args: GeospatialContext(
            routes=[], environment=[city_polygon]
        ),
        elevation_fetcher=lambda points, _settings: [50.0 for _point in points],
    )
    assert city_rejection.rejected_anchor
    assert city_llm_candidate.candidate_type == "ecological_conflict"
    assert city_llm_candidate.score == "0.00"
    assert "hard ecological contradiction" in city_llm_candidate.evidence

    unread_image_result = select_result(
        row={"catalogNumber": "IMAGEONLY", "country": "China"},
        label=LabelRead(
            catalog_number="IMAGEONLY",
            image_path="images/IMAGEONLY.jpg",
            label_status="image_not_transcribed",
        ),
        raw_label_coordinates=[],
        llm_candidates=[],
        gazetteer_matches=[],
        insufficient_locality="country_only",
        exclude_insufficient_locality=True,
        options=SelectionOptions(curation_mode="robust"),
    )
    assert unread_image_result.include_in_dwc
    evaluated_broad_result = select_result(
        row={"catalogNumber": "IMAGEONLY", "country": "China"},
        label=LabelRead(
            catalog_number="IMAGEONLY",
            image_path="images/IMAGEONLY.jpg",
            label_status="llm_image_transcribed",
            label_transcription="China",
        ),
        raw_label_coordinates=[],
        llm_candidates=[],
        gazetteer_matches=[],
        insufficient_locality="country_only",
        exclude_insufficient_locality=True,
        options=SelectionOptions(curation_mode="robust"),
    )
    assert not evaluated_broad_result.include_in_dwc

    assert original_coordinate_status({"decimalLatitude": "23.5102", "decimalLongitude": "120.8051"}, 4) == "precise"
    assert original_coordinate_status({"decimalLatitude": "23.6", "decimalLongitude": "120.95"}, 4) == "coarse"

    with tempfile.TemporaryDirectory() as tmpdir:
        input_dir = Path(tmpdir) / "collector_output"
        output_dir = Path(tmpdir) / "georef_output"
        image_dir = input_dir / "images"
        image_dir.mkdir(parents=True)
        (image_dir / "TAIF2174_Haplopteris_mediosora_type.jpg").write_bytes(b"jpg")

        dwc_rows = [
            {
                "occurrenceID": "occ-1",
                "catalogNumber": "TAIF2174",
                "country": "Chinese Taipei",
                "stateProvince": "",
                "locality": "Alishan",
                "verbatimLocality": "TAIWAN: Formosa: Mt. Arisan",
                "decimalLatitude": "23.5102",
                "decimalLongitude": "120.8051",
                "verbatimElevation": "2300 m",
                "associatedMedia": "images/TAIF2174_Haplopteris_mediosora_type.jpg",
            },
            {
                "occurrenceID": "occ-2",
                "catalogNumber": "KAG022683",
                "country": "Japan",
                "stateProvince": "Kagoshima",
                "locality": "Amami Islands",
                "verbatimLocality": "Amami Islands",
                "decimalLatitude": "28.0",
                "decimalLongitude": "129.3",
                "verbatimElevation": "",
                "associatedMedia": "",
            },
            {
                "occurrenceID": "occ-3",
                "catalogNumber": "NOCOORD1",
                "country": "Japan",
                "stateProvince": "Nagano",
                "locality": "Kawakami-mura",
                "verbatimLocality": "Nagano Pref., Minamisaku-gun, Kawakami-mura",
                "decimalLatitude": "",
                "decimalLongitude": "",
                "verbatimElevation": "",
                "associatedMedia": "",
            },
            {
                "occurrenceID": "occ-4",
                "catalogNumber": "CHINAONLY",
                "country": "China",
                "stateProvince": "",
                "locality": "China",
                "verbatimLocality": "China",
                "decimalLatitude": "",
                "decimalLongitude": "",
                "verbatimElevation": "",
                "associatedMedia": "",
            },
            {
                "occurrenceID": "occ-5",
                "catalogNumber": "COARSEONLY",
                "country": "Japan",
                "stateProvince": "Kagoshima",
                "locality": "Amami Islands",
                "verbatimLocality": "Amami Islands",
                "decimalLatitude": "28.0",
                "decimalLongitude": "129.3",
                "verbatimElevation": "",
                "associatedMedia": "",
            },
        ]
        write_rows(input_dir / "dwc.tsv", dwc_rows)
        write_rows(
            input_dir / "labels.tsv",
            [
                {
                    "catalogNumber": "KAG022683",
                    "detectedLanguages": "ja|en",
                    "labelTranscription": "Japan Kagoshima Amami Islands. Lon=129.26 Lat=28.01. Alt. 101m.",
                    "localityText": "Amami Islands",
                    "eventDateText": "2019-10-31",
                    "collectorText": "Suzuki Eizi",
                    "elevationText": "Alt. 101m",
                }
            ],
        )
        write_rows(
            input_dir / "gazetteer.tsv",
            [
                {
                    "placeName": "Kawakami-mura",
                    "aliases": "川上村",
                    "decimalLatitude": "35.9754",
                    "decimalLongitude": "138.5791",
                    "uncertaintyMeters": "5000",
                    "country": "Japan",
                    "stateProvince": "Nagano",
                    "language": "ja",
                    "source": "offline_test_gazetteer",
                }
            ],
        )

        dry_report = run_pipeline(
            project_dir=ROOT,
            input_dir=input_dir,
            input_dwc=None,
            output_dir=output_dir,
            label_tsv=input_dir / "labels.tsv",
            gazetteer_tsv=input_dir / "gazetteer.tsv",
            dry_run=True,
            curation_mode="standard",
            original_precision_decimals=4,
            review_distance_km=5.0,
            exclude_insufficient_locality=True,
            llm_mode="off",
            llm_provider="codex-cli",
            llm_model="auto",
            llm_reasoning_effort="",
            llm_web_search="live",
            llm_command="",
            llm_api_key_env="OPENAI_API_KEY",
            llm_timeout_seconds=120,
            llm_rate_limit_retries=0,
            confirm_llm=False,
            georeferenced_by="test",
            prompt_profile="xie-modified",
            use_trails=True,
            use_hydrology=True,
            use_dem=True,
            use_vegetation_prior=True,
            taxon_habitat="fern",
            debug_log=True,
            limit=0,
            workers="1",
            progress=TerminalProgress(enabled=False),
        )
        assert dry_report.records_read == 5
        assert not output_dir.exists()

        report = run_test_pipeline(input_dir, output_dir, "standard")
        assert report.records_written == 4
        assert report.kept_original == 2
        assert report.corrected_existing == 1
        assert report.inferred_missing == 1
        assert report.excluded == 1
        curated = read_rows(output_dir / "modified_dwc.tsv")
        assert curated[0]["decimalLatitude"] == "23.5102"
        assert curated[1]["decimalLatitude"] == "28.01"
        assert curated[1]["minimumElevationInMeters"] == "101"
        assert curated[2]["catalogNumber"] == "NOCOORD1"
        assert curated[2]["decimalLatitude"] == "35.9754"
        assert curated[3]["catalogNumber"] == "COARSEONLY"
        assert curated[3]["decimalLatitude"] == "28.0"

        candidates = read_rows(output_dir / "georeference_candidates.tsv")
        assert all(row["habitatPrior"] == "fern" for row in candidates)
        assert any(row["catalogNumber"] == "KAG022683" and row["selected"] == "true" for row in candidates)
        assert any(row["catalogNumber"] == "CHINAONLY" and row["decision"] == "exclude_insufficient_locality" for row in candidates)
        assert "Habitat prior: fern" in (output_dir / "summary.txt").read_text(
            encoding="utf-8"
        )

        robust_output_dir = Path(tmpdir) / "georef_output_robust"
        robust_report = run_test_pipeline(input_dir, robust_output_dir, "robust")
        assert robust_report.records_written == 4
        assert robust_report.kept_original == 1
        assert robust_report.unresolved == 1
        robust_curated = read_rows(robust_output_dir / "modified_dwc.tsv")
        coarse_row = next(row for row in robust_curated if row["catalogNumber"] == "COARSEONLY")
        assert coarse_row["decimalLatitude"] == ""
        assert "review_coarse_original_not_accepted" in coarse_row["georeferenceRemarks"]


if __name__ == "__main__":
    main()
