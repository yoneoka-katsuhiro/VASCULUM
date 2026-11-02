from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_offline import pipeline_options, read_rows, write_rows, write_label_photos, select
from georeference_curator.geocoding import GazetteerEntry, GazetteerMatch
from georeference_curator.geospatial_refinement import GeospatialRefinementSettings, refine_label_elevation
from georeference_curator.llm import (LlmSettings, build_transcription_prompt, cache_prompt, llm_response_to_candidates)
from georeference_curator.llm_cache import LlmResponseCache
from georeference_curator.models import LabelRead
from georeference_curator.pipeline import run_pipeline
from georeference_curator.scoring import SelectionOptions
from georeference_curator.interrupts import run_cli_process


def township_candidate(label):
    # A deterministic fixture, not a claim of independently validated collecting coordinates.
    return llm_response_to_candidates({"coordinateCandidates": [dict(latitude="28.700000", longitude="96.700000",
        geodeticDatum="WGS84", uncertaintyMeters="30000", confidence="low", candidateType="place_centroid",
        supportingBounds=[28.6, 96.6, 28.8, 96.8], modernPlaceName="上察隅镇",
        evidence="Finest legible place is the township; the illegible suffix is not reconstructed; 1973 locality interpretation",
        sourceUrls=["https://example.org/fixture-not-ground-truth"])]}, label, "fixture")[0]


class AutoAcceptanceTests(unittest.TestCase):
    def test_collector_coordinate_evidence_is_accepted_by_curator(self):
        sys.path.insert(0, str(ROOT.parent / "herbarium_specimen_collector" / "scripts"))
        from specimen_collector.models import SpecimenRecord
        from specimen_collector.outputs import dwc_row
        row = dwc_row(SpecimenRecord(source="gbif", catalog_number="HANDOFF", decimal_latitude="35.123456",
            decimal_longitude="138.123456", geodetic_datum="WGS84", coordinate_uncertainty_meters="30",
            georeference_protocol="GPS measurement"), "Example species")
        result = select(row=row)
        self.assertEqual(result.curation_status, "accepted")
        self.assertEqual(result.row["coordinateUncertaintyInMeters"], "30")
        self.assertEqual(result.row["confidence"], "high")

    def test_E01539315_missing_datum_is_accepted_without_LLM(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = dict(catalogNumber="E01539315", country="Myanmar", stateProvince="Kachin", eventDate="2018-05-03",
                decimalLatitude="27.619417", decimalLongitude="96.981778", geodeticDatum="")
            write_rows(root / "input" / "dwc.tsv", [row])
            with patch("georeference_curator.pipeline.make_client", side_effect=AssertionError("Existing point needs no LLM")):
                report = run_pipeline(**pipeline_options(root, llm_mode="on"))
            output = read_rows(root / "out" / "modified_dwc.tsv")[0]
            self.assertEqual(report.accepted, 1)
            self.assertEqual((output["decimalLatitude"], output["decimalLongitude"]), ("27.619417", "96.981778"))
            # 1000 m unstated positional error + 1000 m datum allowance; no 5 km penalty since v0.1.17.
            self.assertEqual(output["coordinateUncertaintyInMeters"], "2000")
            self.assertIn("datum not stated; assumed WGS84", output["georeferenceRemarks"])
            self.assertEqual(output["georeferenceVerificationStatus"], "unverified")
            self.assertEqual(output["confidence"], "medium")

    def test_PE00684280_partial_township_label_is_accepted(self):
        label = LabelRead(catalog_number="PE00684280", label_status="image_unreadable",
            locality_text="西藏 察隅县 上察隅[字迹不清]", label_transcription="西藏 察隅县 上察隅[字迹不清] 1973年7月15日 海拔2100 m", elevation_text="2100 m")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="PE00684280", country="中国", stateProvince="西藏自治区", eventDate="1973-07-15")])
            labels = write_label_photos(root, [dict(catalogNumber="PE00684280", labelTranscription=label.label_transcription,
                localityText=label.locality_text, elevationText=label.elevation_text)])
            class Client:
                def create_json(self, *_args, **_kwargs):
                    candidate = township_candidate(label)
                    return dict(status="accepted", coordinateCandidates=[dict(latitude=candidate.candidate_latitude,
                        longitude=candidate.candidate_longitude, geodeticDatum="WGS84", uncertaintyMeters="30000", confidence="low",
                        supportingBounds=[28.6,96.6,28.8,96.8], candidateType="place_centroid", evidence=candidate.evidence)])
            with patch("georeference_curator.llm.resolve_cli_command", return_value="/test/bin/codex"), patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=Client()), patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(**pipeline_options(root, label_tsv=labels, llm_mode="on"))
            output = read_rows(root / "out" / "modified_dwc.tsv")[0]
            self.assertEqual(report.accepted, 1)
            self.assertEqual(output["curationStatus"], "accepted")
            self.assertGreaterEqual(float(output["coordinateUncertaintyInMeters"]), 30000)
            self.assertEqual(output["minimumElevationInMeters"], "2100")
            self.assertIn("1973", output["georeferenceRemarks"])

    def test_centroid_and_LLM_radius_are_accepted(self):
        result = select(row={"country": "China"}, label=LabelRead(), candidates=[township_candidate(LabelRead())])
        self.assertEqual(result.curation_status, "accepted")
        self.assertEqual(result.row["coordinateUncertaintyInMeters"], "30000")
        self.assertEqual(result.verification_status, "unverified")

    def test_unknown_reference_radius_is_estimated_not_held(self):
        entry = GazetteerEntry("Township", "28.7", "96.7", country="China", bounds="[28.6,96.6,28.8,96.8]")
        result = select(row={"country": "China"}, matches=[GazetteerMatch(entry, 1, "Township name match")])
        self.assertEqual(result.curation_status, "accepted")
        self.assertGreater(float(result.row["coordinateUncertaintyInMeters"]), 10000)

    def test_explicit_uncertainty_filter_does_not_choose_a_false_precise_alternative(self):
        result = select(row={"country": "China"}, candidates=[township_candidate(LabelRead())], options=SelectionOptions(max_uncertainty_meters=1000))
        self.assertEqual(result.curation_status, "excluded")
        self.assertEqual(result.decision, "exclude_uncertainty_limit")

    def test_geographic_contradiction_is_excluded_if_not_repaired(self):
        candidate = llm_response_to_candidates({"coordinateCandidates": [dict(latitude="48.8566", longitude="2.3522", uncertaintyMeters="1000", geodeticDatum="WGS84")]}, LabelRead(), "fixture")[0]
        result = select(row={"country": "Myanmar"}, candidates=[candidate])
        self.assertEqual(result.curation_status, "excluded")

    def test_DEM_selects_inside_locality_and_does_not_shrink_full_extent(self):
        label = LabelRead(elevation_text="2100 m")
        anchor = township_candidate(label)
        def fetch(points, _settings):
            return [2100 if index == 7 else 2700 for index in range(len(points))]
        outcome = refine_label_elevation(anchor, label, GeospatialRefinementSettings(), fetch)
        self.assertIsNotNone(outcome.candidate)
        result = outcome.candidate
        self.assertTrue(28.6 <= float(result.candidate_latitude) <= 28.8)
        self.assertTrue(96.6 <= float(result.candidate_longitude) <= 96.8)
        self.assertEqual(result.candidate_elevation_meters, "2100")
        self.assertGreaterEqual(float(result.candidate_uncertainty_meters), 30000)

    def test_v015_transcription_contract_is_byte_identical(self):
        settings = LlmSettings(model="gpt-6.1-sol", task_mode="transcription_only", web_search_mode="disabled")
        prompt = cache_prompt(settings, build_transcription_prompt({"country":"China", "scientificName":"Haplopteris mediosora", "locality":"中国 西藏自治区"}, LabelRead(image_quality_status="review_compressed_for_label_reading")))
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), "1922950d1aded35d8c9ca9bcdc0a21d53f91900008f3ef866f0cab9ea893a152")

    def test_geographic_failure_reuses_successful_transcription_on_retry(self):
        counts = {"transcription_only": 0, "georeference": 0}
        class Client:
            def __init__(self, settings): self.settings = settings
            def create_json(self, *_args, **_kwargs):
                mode = self.settings.task_mode
                counts[mode] += 1
                if mode == "transcription_only":
                    return dict(status="ok", labelTranscription="Mt Example trail", localityText="Mt Example trail")
                if counts[mode] == 1:
                    raise TimeoutError("simulated technical timeout")
                return dict(status="accepted", coordinateCandidates=[dict(latitude="35.123456", longitude="138.123456", geodeticDatum="WGS84", uncertaintyMeters="2000", confidence="medium")])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan", associatedMedia="images/A.jpg")])
            photo = root / "input" / "images" / "A.jpg"
            photo.parent.mkdir()
            photo.write_bytes(b"whole specimen photo")
            with patch("georeference_curator.llm.resolve_cli_command", return_value="/test/bin/codex"), patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", side_effect=Client), patch("georeference_curator.pipeline.preflight_llm"):
                first = run_pipeline(**pipeline_options(root, llm_mode="on"))
                second = run_pipeline(**pipeline_options(root, llm_mode="on"))
            self.assertEqual(first.failed, 1)
            self.assertEqual(second.accepted, 1)
            self.assertEqual(counts, {"transcription_only": 1, "georeference": 2})
            self.assertEqual(second.llm_cache_hits, 1)

    @unittest.skipUnless(os.name == "posix", "POSIX SIGINT regression")
    def test_provider_reporting_user_interruption_with_exit_one_is_not_record_failure(self):
        with self.assertRaises(KeyboardInterrupt):
            run_cli_process([sys.executable, "-c", "import sys; print('interrupted by user',file=sys.stderr); sys.exit(1)"], timeout=5)

    def test_invalid_empty_geographic_response_cannot_poison_retry_cache(self):
        calls = []
        class Client:
            def create_json(self, *_args, **_kwargs):
                calls.append(1)
                if len(calls) == 1:
                    return dict(status="accepted", coordinateCandidates=[])
                return dict(status="accepted", coordinateCandidates=[dict(latitude="35.123456", longitude="138.123456", uncertaintyMeters="2000", geodeticDatum="WGS84")])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan")])
            labels = write_label_photos(root, [dict(catalogNumber="A", labelTranscription="Mt Example trail", localityText="Mt Example trail")])
            with patch("georeference_curator.llm.resolve_cli_command", return_value="/test/bin/codex"), patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=Client()), patch("georeference_curator.pipeline.preflight_llm"):
                first = run_pipeline(**pipeline_options(root, llm_mode="on", label_tsv=labels))
                second = run_pipeline(**pipeline_options(root, llm_mode="on", label_tsv=labels))
            self.assertEqual(first.failed, 1)
            self.assertEqual(second.accepted, 1)
            self.assertEqual(len(calls), 2)

    @unittest.skipUnless(os.name == "posix", "POSIX SIGINT regression")
    def test_parent_and_provider_SIGINT_stop_entire_pipeline_with_130(self):
        for target in ("parent", "provider"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                rows = [dict(catalogNumber=f"A{i}", country="Japan") for i in range(4)]
                write_rows(root / "input" / "dwc.tsv", rows)
                write_label_photos(root, [dict(catalogNumber=row["catalogNumber"], labelTranscription="Mt Example trail " + row["catalogNumber"], localityText="Mt Example trail " + row["catalogNumber"]) for row in rows])
                process = subprocess.Popen([sys.executable, str(ROOT / "tests" / "sigint_driver.py"), str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                pids = []
                try:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if (root / "children.txt").exists():
                            pids = [int(value) for value in (root / "children.txt").read_text().splitlines()]
                        if len(pids) == 2: break
                        if process.poll() is not None: break
                        time.sleep(0.05)
                    self.assertEqual(len(pids), 2)
                    os.kill(process.pid if target == "parent" else pids[0], signal.SIGINT)
                    _, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 130, stderr)
                    self.assertEqual(len((root / "children.txt").read_text().splitlines()), 2)
                    self.assertFalse((root / "out" / "modified_dwc.tsv").exists())
                    for pid in pids:
                        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()
                    for pid in pids:
                        try: os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError: pass


if __name__ == "__main__":
    unittest.main()
