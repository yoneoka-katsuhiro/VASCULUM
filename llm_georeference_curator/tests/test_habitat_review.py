from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from functools import partial
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from georeference_curator.geospatial_refinement import (
    EnvironmentalFeature,
    GeospatialContext,
    GeospatialRefinementSettings,
    review_selected_habitat,
)
from georeference_curator.habitat import parse_habitats
from georeference_curator.models import CoordinateCandidate
from georeference_curator.pipeline import run_pipeline
from georeference_curator.progress import TerminalProgress


def city_context(*_args):
    return GeospatialContext(environment=[EnvironmentalFeature(
        osm_type="way", osm_id=1, classes=("built_up",),
        geometry=((34.0, 137.0), (36.0, 137.0), (36.0, 139.0), (34.0, 139.0), (34.0, 137.0)),
    )])


from test_offline import write_label_photos


class HabitatReviewTests(unittest.TestCase):
    def candidate(self):
        return CoordinateCandidate(
            candidate_latitude="35.123456", candidate_longitude="138.123456",
            candidate_geodetic_datum="WGS84", candidate_uncertainty_meters="500",
            candidate_type="refined_georeference", score="0.80",
            candidate_source="llm:test", source_urls="https://example.org/locality",
        )

    def test_conflicting_point_is_immutable(self):
        candidate = self.candidate()
        before = asdict(candidate)
        review = review_selected_habitat(
            candidate, parse_habitats("subalpine forest"), GeospatialRefinementSettings(),
            context_fetcher=city_context,
            elevation_fetcher=lambda points, _settings: [50.0] * len(points),
        )
        self.assertEqual(review.status, "warning")
        self.assertEqual(asdict(candidate), before)
        self.assertIn("current maps may differ", review.remarks)

    def test_missing_map_is_unknown(self):
        review = review_selected_habitat(
            self.candidate(), parse_habitats("forest"), GeospatialRefinementSettings(),
            context_fetcher=lambda *_args: GeospatialContext(),
            elevation_fetcher=lambda *_args: [2000.0],
        )
        self.assertEqual(review.status, "unknown")

    def test_network_failure_is_not_a_conflict(self):
        candidate = self.candidate()
        before = asdict(candidate)
        with patch("builtins.print"):
            review = review_selected_habitat(
                candidate, parse_habitats("forest"), GeospatialRefinementSettings(),
                context_fetcher=lambda *_args: (_ for _ in ()).throw(TimeoutError("unavailable")),
            )
        self.assertEqual(review.status, "unavailable")
        self.assertEqual(asdict(candidate), before)

    def test_disabled_custom_and_other_datum_do_not_query(self):
        def forbidden(*_args):
            self.fail("No network request expected")

        candidate = self.candidate()
        for preference, settings, expected in (
            (parse_habitats(""), GeospatialRefinementSettings(), "not_requested"),
            (parse_habitats("forest"), GeospatialRefinementSettings(enabled=False), "not_checked"),
            (parse_habitats("a custom habitat"), GeospatialRefinementSettings(), "unknown"),
        ):
            review = review_selected_habitat(candidate, preference, settings, context_fetcher=forbidden)
            self.assertEqual(review.status, expected)
        candidate.candidate_geodetic_datum = "TWD67"
        review = review_selected_habitat(candidate, parse_habitats("forest"), GeospatialRefinementSettings(), context_fetcher=forbidden)
        self.assertEqual(review.status, "unknown")

    def test_pipeline_habitat_changes_only_review(self):
        class Client:
            def create_json(self, _prompt, image_paths=None):
                return {
                    "status": "ok", "remarks": "",
                    "coordinateCandidates": [
                        dict(latitude="35.123456", longitude="138.123456", geodeticDatum="WGS84", uncertaintyMeters="500", candidateType="refined_georeference", sourceUrls=["https://example.org/site"], score="0.80"),
                        dict(latitude="35.543210", longitude="138.543210", geodeticDatum="WGS84", uncertaintyMeters="500", candidateType="refined_georeference", sourceUrls=["https://example.org/alternative"], score="0.70"),
                    ],
                }

        advisory_review = partial(
            review_selected_habitat, context_fetcher=city_context,
            elevation_fetcher=lambda points, _settings: [50.0] * len(points),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            fields = ["catalogNumber", "country", "locality", "decimalLatitude", "decimalLongitude", "verbatimCoordinates", "geodeticDatum", "coordinateUncertaintyInMeters", "georeferenceProtocol"]
            records = [
                dict(catalogNumber="ORIGINAL", country="Japan", locality="Specific mountain", decimalLatitude="35.123456", decimalLongitude="138.123456", geodeticDatum="WGS84", coordinateUncertaintyInMeters="30", georeferenceProtocol="GPS field measurement"),
                dict(catalogNumber="LABEL", country="Japan", locality="Specific mountain", verbatimCoordinates="WGS84 35.123456 N, 138.123456 E"),
                dict(catalogNumber="LLM", country="Japan", locality="Specific mountain"),
            ]
            with (source / "dwc.tsv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
                writer.writeheader()
                writer.writerows(records)
            labels = write_label_photos(root, [
                dict(catalogNumber="LABEL", labelTranscription="WGS84 35.123456 N, 138.123456 E", localityText="Specific mountain"),
                dict(catalogNumber="LLM", labelTranscription="Specific mountain", localityText="Specific mountain"),
            ], input_dir=source)
            all_results = []
            with patch.dict("os.environ", {"PATH": os.environ.get("PATH", os.defpath)}, clear=True), patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=Client()), patch("georeference_curator.pipeline.preflight_llm"), patch("georeference_curator.pipeline.review_selected_habitat", side_effect=advisory_review), patch("georeference_curator.pipeline.selected_dem_elevation", return_value=None):
                for habitat in ("", "subalpine forest", "sea"):
                    destination = root / (habitat or "no_habitat")
                    report = run_pipeline(
                        project_dir=root, input_dir=source, input_dwc=None, output_dir=destination,
                        label_tsv=labels, gazetteer_tsv=None, dry_run=False,
                        review_distance_km=5,
                        llm_mode="on", llm_provider="codex-cli", llm_model="gpt-test", llm_reasoning_effort="medium",
                        llm_web_search="live", llm_command="test-cli", llm_api_key_env="OPENAI_API_KEY",
                        llm_rate_limit_retries=0, confirm_llm=False, georeferenced_by="test",
                        use_trails=False, use_hydrology=True, use_dem=True,
                        use_vegetation_prior=True, taxon_habitat=habitat,
                        workers="2", progress=TerminalProgress(False),
                        verify_source_urls=False, label_coordinate_uncertainty_meters=30,
                    )
                    with (destination / "modified_dwc.tsv").open() as handle:
                        rows = list(csv.DictReader(handle, delimiter="\t"))
                    self.assertEqual(report.records_written, 2)
                    all_results.append([(row["decimalLatitude"], row["decimalLongitude"], row["coordinateUncertaintyInMeters"]) for row in rows])
                    log = [json.loads(line) for line in (destination / "georeference.log.jsonl").read_text().splitlines()]
                    if habitat:
                        self.assertTrue(all(entry["habitatReview"]["status"] == "warning" for entry in log))
                        self.assertEqual(report.habitat_review_warnings, 3)
                        self.assertEqual(report.accepted, 2)
                        self.assertEqual(report.llm_georeference_requests, 0)
                    else:
                        self.assertTrue(all(not entry["habitatReview"] for entry in log))
                        self.assertEqual(report.llm_georeference_requests, 1)
                    self.assertEqual([entry["decision"] for entry in log], ["keep_original", "infer_missing", "infer_missing"])
                    selected_scores = [entry["selectedCandidate"]["score"] for entry in log]
                    self.assertEqual(selected_scores, ["", "", "0.80"])
                    self.assertEqual([entry["includeInDwc"] for entry in log], [True, True, False])
            self.assertEqual(all_results[0], all_results[1])
            self.assertEqual(all_results[0], all_results[2])


if __name__ == "__main__":
    unittest.main()
