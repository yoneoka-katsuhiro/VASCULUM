from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stderr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from georeference_curator.elevation import parse_elevation_meters, round_elevation, specimen_elevation
from georeference_curator.geocoding import extract_decimal_coordinates, is_precise_label_coordinate, match_gazetteer, GazetteerEntry
from georeference_curator.geospatial_refinement import (
    GeospatialContext, GeospatialRefinementSettings, RoutePoint,
    classify_environment_tags, refine_llm_candidates,
)
from georeference_curator.habitat import habitat_vocabulary, parse_habitats
from georeference_curator.labels import detect_languages
from georeference_curator.llm import LlmSettings, llm_response_to_candidates, model_candidates_for, normalize_provider, search_policy
from georeference_curator.models import LabelRead
from georeference_curator.parallel import resolve_worker_count
from georeference_curator.pipeline import run_pipeline
from georeference_curator.progress import TerminalProgress, progress_bar
from georeference_curator.scoring import SelectionOptions, original_coordinate_status, select_result
from curate_georeferences import parse_args


def write_rows(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_label_photos(root, labels, input_dir=None):
    labels = [dict(row) for row in labels]
    for row in labels:
        relative = "images/" + row["catalogNumber"] + ".jpg"
        photo = (input_dir or root / "input") / relative
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"whole specimen photograph")
        row["imagePath"] = relative
    path = root / "labels.tsv"
    write_rows(path, labels)
    return path


def pipeline_options(root, **overrides):
    values = dict(
        project_dir=root, input_dir=root / "input", input_dwc=None, output_dir=root / "out",
        label_tsv=None, gazetteer_tsv=None, dry_run=False,
        review_distance_km=5,
        llm_mode="off", llm_provider="codex-cli", llm_model="gpt-test", llm_reasoning_effort="medium",
        llm_web_search="live", llm_command="", llm_api_key_env="OPENAI_API_KEY",
        llm_rate_limit_retries=0, confirm_llm=False, georeferenced_by="test",
        use_trails=True, use_hydrology=True, use_dem=False, use_vegetation_prior=True,
        taxon_habitat="", workers="1", progress=TerminalProgress(False), verify_source_urls=False, geospatial_refinement=False,
    )
    values.update(overrides)
    return values


def select(row=None, label=None, coordinates=None, candidates=None, options=None, insufficient="", matches=None):
    return select_result(row or {}, label or LabelRead(), coordinates or [], candidates or [], matches or [], insufficient, options or SelectionOptions())


def trusted_row(**overrides):
    row = dict(catalogNumber="GPS", country="Japan", locality="Mt Example trail",
        decimalLatitude="35.123456", decimalLongitude="138.123456", geodeticDatum="WGS84",
        coordinateUncertaintyInMeters="30", georeferenceProtocol="GPS measurement")
    row.update(overrides)
    return row


class OfflineTests(unittest.TestCase):
    def test_curation_mode_switches_are_removed(self):
        with patch.object(sys, "argv", ["curate", "--input", "input"]):
            args = parse_args()
        self.assertFalse(hasattr(args, "curation_mode"))
        for flags in (["--robust"], ["--standard"], ["--curation-mode", "robust"]):
            with self.subTest(flags=flags), patch.object(sys, "argv", ["curate", "--input", "input", *flags]), redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as error:
                    parse_args()
                self.assertEqual(error.exception.code, 2)
                self.assertIn("unrecognized arguments", stderr.getvalue())

    def test_record_processing_failure_is_audited_not_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row()])
            with patch("georeference_curator.pipeline.process_row", side_effect=RuntimeError("test record error")):
                report = run_pipeline(**pipeline_options(root, export_images=True))
            self.assertEqual(report.records_written, 0)
            self.assertEqual(report.images_exported, 0)
            self.assertEqual(report.failed, 1)
            self.assertEqual(read_rows(root / "out" / "modified_dwc.tsv"), [])
            log = json.loads((root / "out" / "georeference.log.jsonl").read_text())
            self.assertEqual(log["curationStatus"], "failed")
            self.assertFalse(log["includeInDwc"])
            self.assertEqual(log["stageErrors"][0]["stage"], "record_processing")

    def test_progress_models_languages_and_workers(self):
        self.assertEqual(progress_bar(0, 149), "[--------------------]")
        self.assertEqual(progress_bar(13, 149), "[==------------------]")
        self.assertEqual(progress_bar(149, 149), "[====================]")
        self.assertEqual(detect_languages("Formosa 阿里山"), ["zh", "en/la"])
        self.assertEqual(normalize_provider("codex"), "codex-cli")
        self.assertEqual(normalize_provider("opus"), "opus5")
        self.assertEqual(model_candidates_for(LlmSettings())[0], "gpt-6.1-sol")
        self.assertGreaterEqual(resolve_worker_count("auto", project_dir=ROOT, provider="codex-cli", model_label="gpt-test", web_search_mode="indexed", records=10), 1)

    def test_label_distances_are_not_elevations(self):
        self.assertEqual(specimen_elevation(LabelRead(label_transcription="200 m from village; 50 m from river")), (None, None))
        self.assertEqual(specimen_elevation(LabelRead(label_transcription="200 m from village. Alt. 6500 ft")), (1981, 1981))
        self.assertEqual(specimen_elevation(LabelRead(label_transcription="Forest, 2000-2100 m a.s.l.")), (2000, 2100))
        self.assertEqual(specimen_elevation(LabelRead(elevation_text="2040 m")), (2040, 2040))
        result = select(row=trusted_row(verbatimElevation="2040 m"), label=LabelRead(label_transcription="200 m from village"))
        self.assertEqual(result.row["minimumElevationInMeters"], "2040")
        self.assertIn("elevation_source=original_dwc", result.row["georeferenceRemarks"])

    def test_dms_and_decimal_hemispheres(self):
        for text, lat, lon, kind in (
            ("TWD 67: N 23° 27′ E 120° 55′ Alt. 3050 m", 23.45, 120.9166667, "dms_minutes"),
            ("WGS84 N 23°31′22″ E 120°48′30″", 23.5227778, 120.8083333, "dms_seconds"),
            ("WGS84 23°31′22″ N, 120°48′30″ E", 23.5227778, 120.8083333, "dms_seconds"),
            ("WGS84 S 23.1234 W 120.1234", -23.1234, -120.1234, "decimal_4dp"),
            ("WGS84 23.1234 S, 120.1234 W", -23.1234, -120.1234, "decimal_4dp"),
        ):
            with self.subTest(text=text):
                values = extract_decimal_coordinates(text)
                self.assertEqual(len(values), 1)
                self.assertAlmostEqual(values[0].latitude, lat, places=6)
                self.assertAlmostEqual(values[0].longitude, lon, places=6)
                self.assertEqual(values[0].precision_kind, kind)
        self.assertEqual(extract_decimal_coordinates("N 23°61′ E 120°48′"), [])
        self.assertEqual(extract_decimal_coordinates("N 23°31′70″ E 120°48′30″"), [])
        self.assertEqual(extract_decimal_coordinates("N 93°31′ E 120°48′"), [])
        self.assertEqual(len(extract_decimal_coordinates("N 23°31′ E 120°48′\n2290 m")), 1)

    def test_label_precision_not_accuracy(self):
        raw = extract_decimal_coordinates("WGS84 23°31′22″ N, 120°48′30″ E")
        self.assertTrue(is_precise_label_coordinate(raw[0]))
        self.assertFalse(is_precise_label_coordinate(extract_decimal_coordinates("23°31′22″ N, 120°48′30″ E")[0]))
        review = select(coordinates=raw)
        self.assertEqual(review.row["decimalLatitude"], "23.522778")
        self.assertEqual(review.row["coordinateUncertaintyInMeters"], "")
        self.assertFalse(review.include_in_dwc)
        accepted = select(coordinates=raw, options=SelectionOptions(label_coordinate_uncertainty_meters=30))
        self.assertEqual(accepted.curation_status, "accepted")
        self.assertTrue(accepted.include_in_dwc)
        self.assertEqual(accepted.row["coordinateUncertaintyInMeters"], "60")

    def test_original_requires_provenance_datum_uncertainty(self):
        self.assertEqual(original_coordinate_status(trusted_row()), "trusted")
        for changes, expected in (
            ({"geodeticDatum": ""}, "datum_unresolved"),
            ({"coordinateUncertaintyInMeters": ""}, "uncertainty_unknown"),
            ({"decimalLatitude": "35.1", "decimalLongitude": "138.1"}, "uncertainty_below_rounding"),
            ({"georeferenceProtocol": "not GPS"}, "unverified"),
            ({"georeferenceProtocol": ""}, "unverified"),
            ({"decimalLatitude": "0", "decimalLongitude": "0"}, "zero_placeholder"),
            ({"decimalLatitude": "nan"}, "missing"),
        ):
            self.assertEqual(original_coordinate_status(trusted_row(**changes)), expected)
        result = select(row=trusted_row())
        self.assertEqual(result.decision, "keep_original")
        self.assertEqual(result.curation_status, "accepted")

    def test_elevation_units_and_label_priority(self):
        self.assertEqual(parse_elevation_meters("Alt. 6500 ft"), (1981, 1981))
        self.assertEqual(parse_elevation_meters("6000-6500 feet"), (1829, 1981))
        self.assertEqual(parse_elevation_meters("2040 m"), (2040, 2040))
        self.assertEqual(parse_elevation_meters("2040.5 m"), (2040.5, 2040.5))
        self.assertEqual(parse_elevation_meters("5 miles"), (None, None))
        self.assertEqual(round_elevation(2285, 10), 2290)
        candidate = llm_response_to_candidates({"coordinateCandidates": [{"latitude": "35.1", "longitude": "138.1", "geodeticDatum": "WGS84", "elevationMeters": "2285"}]}, LabelRead(), "test")[0]
        result = select(label=LabelRead(elevation_text="6500 ft"), candidates=[candidate])
        self.assertEqual(result.row["minimumElevationInMeters"], "1981")
        result = select(candidates=[candidate])
        self.assertEqual(result.row["minimumElevationInMeters"], "2290")
        self.assertIn("vertical_accuracy_not_guaranteed", result.row["georeferenceRemarks"])

    def test_llm_confidence_is_not_acceptance(self):
        candidates = llm_response_to_candidates({"coordinateCandidates": [
            {"latitude": "35.123456", "longitude": "138.123456", "geodeticDatum": "WGS84", "uncertaintyMeters": "5", "score": "1.0", "candidateType": "refined_georeference"},
        ]}, LabelRead(), "test")
        result = select(candidates=candidates)
        self.assertEqual(result.curation_status, "review")
        self.assertFalse(result.include_in_dwc)
        self.assertEqual(result.row["coordinateUncertaintyInMeters"], "")
        self.assertEqual(candidates[0].reported_uncertainty_meters, "5")

    def test_uncertainty_limit_and_documented_conflicts(self):
        result = select(row=trusted_row(), options=SelectionOptions(max_uncertainty_meters=20))
        self.assertFalse(result.include_in_dwc)
        raw = extract_decimal_coordinates("WGS84 N 25.123456 E 121.123456")
        result = select(row=trusted_row(), coordinates=raw, options=SelectionOptions(label_coordinate_uncertainty_meters=30))
        self.assertEqual(result.curation_status, "review")
        self.assertTrue(any("Conflicting" in note for note in result.notes))

    def test_insufficient_locality_and_unread_images_are_distinct(self):
        unknown = select(row={"country": "China"}, label=LabelRead(image_path="not-read.jpg", label_status="image_not_transcribed"), insufficient="country_only")
        self.assertEqual(unknown.curation_status, "unresolved")
        self.assertFalse(unknown.include_in_dwc)
        broad = select(row={"country": "China"}, label=LabelRead(label_status="llm_image_transcribed"), insufficient="country_only")
        self.assertEqual(broad.curation_status, "excluded")

    def test_verified_historical_gazetteer(self):
        row = dict(country="Japan", stateProvince="Nagano", locality="Old Example Mountain")
        entry = GazetteerEntry("New Example Mountain", "35.123456", "138.123456", country="Japan", state_province="Nagano", historical_place_name="Old Example Mountain", uncertainty_meters="300", geodetic_datum="WGS84", verified=True, source="survey-2020")
        matches = match_gazetteer(row, "", [entry])
        result = select(row=row, matches=matches)
        self.assertTrue(result.include_in_dwc)
        self.assertEqual(result.selected_candidate.historical_place_name, "Old Example Mountain")
        self.assertEqual(result.selected_candidate.support_status, "verified_reference")

    def test_habitat_language_policy(self):
        habitat = parse_habitats(["subalpine forest", "river", "ultramafic"])
        self.assertIn("forest", habitat.expected_classes)
        self.assertTrue({"sea", "pond", "desert", "beach", "limestone"}.issubset(habitat_vocabulary()))
        self.assertIn("built_up", classify_environment_tags({"landuse": "residential"}))
        self.assertIn("ultramafic", classify_environment_tags({"geological": "serpentinite"}))
        self.assertFalse(search_policy({"country": "Japan"}, LabelRead(label_transcription="長野県"))["chineseFallbackAllowed"])
        self.assertTrue(search_policy({"country": "Chinese Taipei"}, LabelRead())["chineseFallbackAllowed"])

    def test_named_route_only_and_uncertainty_never_shrinks(self):
        label = LabelRead(locality_text="Example mountain trail", elevation_text="2282 m")
        anchor = llm_response_to_candidates({"coordinateCandidates": [{"latitude": "23.484080", "longitude": "120.830250", "geodeticDatum": "WGS84", "uncertaintyMeters": "2000", "candidateType": "place_centroid", "sourceUrls": ["https://example.org/site"], "evidence": "Example mountain trail", "modernPlaceName": "Example mountain trail"}]}, label, "test")[0]
        points = [RoutePoint(23.484123, 120.830456, 12345, {"highway": "path", "name": "Example mountain trail"})]
        settings = GeospatialRefinementSettings()
        refinement = refine_llm_candidates([anchor], label, settings,
            context_fetcher=lambda *_: GeospatialContext(routes=points),
            elevation_fetcher=lambda points, _: [2285.0] * len(points))
        self.assertIsNotNone(refinement.candidate)
        self.assertGreaterEqual(float(refinement.candidate.candidate_uncertainty_meters), 2000)
        self.assertEqual(refinement.candidate.candidate_elevation_meters, "2290")
        self.assertEqual(refinement.candidate.route_support, "named_route_in_collection_text")
        points[0].tags["name"] = "Unrelated road"
        no_snap = refine_llm_candidates([anchor], label, settings,
            context_fetcher=lambda *_: GeospatialContext(routes=points),
            elevation_fetcher=lambda *_: self.fail("Unrelated roads must not be sampled"))
        self.assertIsNone(no_snap.candidate)

    def test_full_pipeline_outputs_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [trusted_row(), dict(catalogNumber="REVIEW", country="Japan", locality="Mt Example trail", decimalLatitude="35.1", decimalLongitude="138.1"), dict(catalogNumber="BROAD", country="China", locality="China")]
            write_rows(root / "input" / "dwc.tsv", rows)
            args = pipeline_options(root)
            dry = run_pipeline(**dict(args, dry_run=True))
            self.assertEqual(dry.records_read, 3)
            self.assertFalse((root / "out").exists())
            first = run_pipeline(**args)
            self.assertEqual(first.records_written, 1)
            self.assertEqual([row["catalogNumber"] for row in read_rows(root / "out" / "modified_dwc.tsv")], ["GPS"])
            self.assertEqual(first.accepted, 1)
            self.assertEqual(first.excluded_missing_source_image, 2)
            self.assertEqual(set(path.name for path in (root / "out").iterdir()), {"modified_dwc.csv", "modified_dwc.tsv", "georeference_candidates.tsv", "summary.txt", "georeference.log.jsonl", "georeference_flow.pdf"})
            second = run_pipeline(**args)
            self.assertEqual(second.resumed_records, 3)
            self.assertEqual(second.records_written, 1)
            log = [json.loads(line) for line in (root / "out" / "georeference.log.jsonl").read_text().splitlines()]
            self.assertIn("originalCoordinates", log[0])
            self.assertTrue(log[0]["provenance"]["checkpointReused"])
            self.assertEqual(len(log), 3)
            self.assertEqual([entry["includeInDwc"] for entry in log], [True, False, False])
            self.assertIn("Final DwC policy: accepted records only", (root / "out" / "summary.txt").read_text())


if __name__ == "__main__":
    unittest.main()
