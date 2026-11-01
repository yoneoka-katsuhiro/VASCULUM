from __future__ import annotations

import io
import json
import subprocess
import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_offline import pipeline_options, write_rows, read_rows, trusted_row, select
from georeference_curator.checkpoints import RecordJournal
from georeference_curator.evaluation import prepare, evaluate
from georeference_curator.llm import LlmSettings, build_xie_prompt, parse_task_response, response_schema, CodexCliClient, apply_llm_label_fields, remaining_stage_time, is_model_selection_error
from georeference_curator.models import LabelRead
from georeference_curator.parallel import RateLimitBackoff
from georeference_curator.pipeline import run_pipeline
from georeference_curator.policy import to_wgs84, extent_uncertainty, rounding_radius
from georeference_curator.progress import TerminalProgress
from georeference_curator.scoring import SelectionOptions
from georeference_curator.sources import SourceAuditor, public_url


class PolicyEvaluationTests(unittest.TestCase):
    def test_crs_conversion_and_unknown_datum(self):
        with self.assertRaises(ValueError):
            to_wgs84(23.5, 120.5, "")
        wgs = to_wgs84(23.5, 120.5, "EPSG:4326")
        self.assertEqual(wgs.latitude, 23.5)
        tokyo = to_wgs84(35.5, 139.5, "Tokyo datum")
        self.assertNotEqual(tokyo.latitude, 35.5)
        self.assertIsNotNone(tokyo.accuracy_meters)
        with self.assertRaises(ValueError):
            to_wgs84(35.5, 139.5, "EPSG:3857")
        with self.assertRaises(ValueError):
            to_wgs84(0, 0, "Tokyo datum")

    def test_reference_regions_are_checks_not_coordinate_constraints(self):
        options = SelectionOptions(reference_regions=[dict(country="Japan", source="regional-survey", bounds=[30, 130, 34, 134])])
        result = select(row=trusted_row(), options=options)
        self.assertEqual(result.curation_status, "review")
        self.assertEqual(result.row["decimalLatitude"], "35.123456")
        self.assertIn("outside supplied", result.row["georeferenceRemarks"])

    def test_geometry_uncertainty_and_numeric_precision(self):
        geometry = json.dumps(dict(type="bbox", coordinates=[35, 138, 35.1, 138.1]))
        self.assertGreater(extent_uncertainty(35.05, 138.05, geometry), 7000)
        self.assertIsNone(extent_uncertainty(36, 138.05, geometry))
        self.assertIsNone(extent_uncertainty(35, 138, dict(type="bbox", coordinates=[100, 138, 110, 139])))
        self.assertEqual(rounding_radius("3.5123456e1", "1.38123456e2"), 1)

    def test_baseline_contract_and_no_embedded_coordinates(self):
        settings = LlmSettings(task_mode="xie_baseline", web_search_mode="disabled")
        self.assertIsNone(response_schema(settings))
        row = trusted_row(scientificName="SECRET TAXON", associatedMedia="SECRET IMAGE", habitat="SECRET HABITAT")
        prompt = build_xie_prompt(row)
        self.assertNotIn("35.123456", prompt)
        self.assertNotIn("SECRET", prompt)
        self.assertNotIn("GPS", prompt)
        self.assertIn("Example input", prompt)
        parsed = parse_task_response("[35.1,138.1]", settings)
        self.assertEqual(parsed["coordinateCandidates"][0]["geodeticDatum"], "WGS84")
        for invalid in ("[true, 0]", "[100, 0]", "[35]", "{}"):
            with self.assertRaises(ValueError):
                parse_task_response(invalid, settings)
        with self.assertRaises(ValueError):
            build_xie_prompt(dict(locality="WGS84 N 35.1234 E 138.1234"))

    def test_source_snapshot_is_not_fact_verification(self):
        class Response:
            headers = {"Content-Type": "text/html"}
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return b"Unrelated page, not evidence of this collecting event."
            def geturl(self): return "https://example.org/page"
        class Opener:
            calls = 0
            def open(self, *_args, **_kwargs):
                self.calls += 1
                return Response()
        with tempfile.TemporaryDirectory() as directory:
            opener = Opener()
            auditor = SourceAuditor(Path(directory))
            with patch("georeference_curator.sources.public_url", side_effect=lambda value: value), patch("georeference_curator.sources.build_opener", return_value=opener):
                first = auditor.audit("https://example.org/page")[0]
                second = auditor.audit("https://example.org/page")[0]
            self.assertEqual(opener.calls, 1)
            self.assertEqual(first["factualLocalityMatch"], "not_established")
            self.assertTrue(Path(first["snapshotPath"]).exists())
            self.assertTrue(second["snapshotReused"])
        with self.assertRaises(ValueError):
            public_url("file:///etc/passwd")
        with self.assertRaises(ValueError):
            public_url("http://127.0.0.1/")

    def test_parallel_progress_propagates_exceptions(self):
        progress = TerminalProgress(True)
        progress.set_parallel(True)
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "must propagate"):
                with progress.activity("test", stage="Label reading"):
                    raise RuntimeError("must propagate")
        progress.finish_run()

    def test_fresh_attempt_budget(self):
        gate = RateLimitBackoff(max_calls=2)
        gate.reserve_call()
        gate.reserve_call()
        with self.assertRaisesRegex(RuntimeError, "budget reached"):
            gate.reserve_call()
        self.assertEqual(gate.calls, 2)

    def test_availability_fallback_cannot_bypass_budget_or_timeout(self):
        self.assertTrue(is_model_selection_error("model does not exist"))
        self.assertFalse(is_model_selection_error("model access unavailable due to usage limit"))
        self.assertFalse(is_model_selection_error("429 model not available: quota exceeded"))
        gate = RateLimitBackoff(max_calls=1)
        client = CodexCliClient(LlmSettings())
        client.request_gate = gate.reserve_call
        with patch.dict("os.environ", {}, clear=True), patch("georeference_curator.llm.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "model not found")) as runner:
            with self.assertRaisesRegex(RuntimeError, "budget reached"):
                client.create_json("test")
        self.assertEqual(runner.call_count, 1)
        with patch("georeference_curator.llm.time.monotonic", return_value=101):
            with self.assertRaises(TimeoutError):
                remaining_stage_time(LlmSettings(), 100)
        gate._pause_until = time.monotonic() + 1000
        with self.assertRaises(TimeoutError):
            gate.wait(deadline=time.monotonic() - 1)

    def test_unreadable_response_is_not_a_completed_transcription(self):
        label = LabelRead(image_path="whole.jpg", label_status="image_not_transcribed")
        apply_llm_label_fields(label, {"status": "unreadable", "detectedLanguages": ["ja"], "labelTranscription": "", "localityText": ""})
        self.assertEqual(label.label_status, "image_unreadable")
        result = select(row={"country": "Japan"}, label=label, insufficient="country_only")
        self.assertNotEqual(result.curation_status, "excluded")

    def test_baseline_pipeline_ignores_images_originals_and_web(self):
        class Client:
            def __init__(self, settings): self.settings = settings
            def create_json(inner, prompt, image_paths=None):
                self.assertEqual(inner.settings.task_mode, "xie_baseline")
                self.assertEqual(inner.settings.web_search_mode, "disabled")
                self.assertEqual(image_paths, [])
                self.assertNotIn("35.123456", prompt)
                return parse_task_response("[35.2,138.2]", inner.settings)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row(associatedMedia="images/private.jpg")])
            with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", side_effect=Client) as factory, patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(**pipeline_options(root, llm_mode="on", georeference_method="xie-baseline", taxon_habitat="forest", use_dem=True))
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(report.llm_transcription_requests, 0)
            self.assertEqual(report.llm_georeference_requests, 1)
            self.assertEqual(report.image_records, 0)
            self.assertEqual(report.geospatial_refinement_attempted, 0)
            self.assertEqual(report.habitat_checks_attempted, 0)
            self.assertEqual(read_rows(root / "out" / "georeference_candidates.tsv")[0]["candidateLatitude"], "35.200000")

    def test_image_change_invalidates_completed_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "input" / "images" / "specimen.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"original whole image")
            write_rows(root / "input" / "dwc.tsv", [trusted_row(associatedMedia="images/specimen.jpg")])
            args = pipeline_options(root)
            run_pipeline(**args)
            self.assertEqual(run_pipeline(**args).resumed_records, 1)
            image.write_bytes(b"replacement whole image")
            self.assertEqual(run_pipeline(**args).resumed_records, 0)

    def test_resumed_tokens_are_historical_not_new_usage(self):
        class Client:
            calls = 0
            def create_json(self, *_args, **_kwargs):
                self.calls += 1
                return {"status": "ok", "coordinateCandidates": [], "_vasculum_usage": {"input_tokens": 100, "output_tokens": 10}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan", locality="Mt Example trail")])
            client = Client()
            args = pipeline_options(root, llm_mode="on")
            with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=client), patch("georeference_curator.pipeline.preflight_llm"):
                first = run_pipeline(**args)
                second = run_pipeline(**args)
            self.assertEqual(client.calls, 1)
            self.assertEqual(first.llm_input_tokens, 100)
            self.assertEqual(second.llm_input_tokens, 0)
            self.assertEqual(second.resumed_records, 1)

    def test_preserve_trusted_original_beyond_six_decimals(self):
        row = trusted_row(decimalLatitude="35.123456789", decimalLongitude="138.123456789")
        result = select(row=row)
        self.assertEqual(result.row["decimalLatitude"], row["decimalLatitude"])
        self.assertIn("GPS", result.row["georeferenceProtocol"])

    def test_prepare_masks_truth_and_groups_shared_sites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [trusted_row(occurrenceID="a", catalogNumber="A", locality="Shared site"), trusted_row(occurrenceID="b", catalogNumber="B", locality="Shared site"), trusted_row(occurrenceID="c", catalogNumber="C", locality="WGS84 N 35.1234 E 138.1234")]
            write_rows(root / "input.tsv", records)
            manifest = prepare(root / "input.tsv", root / "benchmark")
            self.assertEqual(manifest["retained"], 2)
            self.assertEqual(manifest["excluded"], 1)
            truth = read_rows(root / "benchmark" / "ground_truth.tsv")
            self.assertEqual(truth[0]["split"], truth[1]["split"])
            self.assertEqual(truth[0]["group"], truth[1]["group"])
            masked = read_rows(root / "benchmark" / truth[0]["split"] / "dwc.tsv")
            self.assertEqual(set(masked[0]), {"occurrenceID", "catalogNumber", "country", "stateProvince", "county", "locality"})
            self.assertNotIn("35.123456", json.dumps(masked))

    def test_evaluation_retains_failures_and_outliers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "truth.tsv", [dict(recordID=str(index), catalogNumber=str(index), split="evaluation", group=str(index), latitude="35", longitude="138") for index in range(3)])
            write_rows(root / "candidates.tsv", [dict(occurrenceID="0", catalogNumber="0", selected="true", candidateLatitude="35", candidateLongitude="138", candidateGeodeticDatum="WGS84", candidateUncertaintyMeters="30", uncertaintyBasis="documented_label"), dict(occurrenceID="1", catalogNumber="1", selected="true", candidateLatitude="0", candidateLongitude="0", candidateGeodeticDatum="WGS84", candidateUncertaintyMeters="100", uncertaintyBasis="llm_reported_only")])
            result = evaluate(root / "truth.tsv", {"enhanced": root / "candidates.tsv"})["methods"]["enhanced"]
            self.assertAlmostEqual(result["failureRate"], 1 / 3)
            self.assertGreater(result["maxKm"], 100)
            self.assertEqual(result["defensibleUncertaintyCount"], 1)
            self.assertEqual(result["uncertaintyCoverage"], 1)
            self.assertAlmostEqual(result["withinKmFractionAllReferences"]["10"], 1 / 3)

    def test_retryable_failure_is_not_insufficient_locality(self):
        class Client:
            def create_json(self, *_args, **_kwargs):
                raise TimeoutError("test timeout")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan", locality="Mt Example trail")])
            args = pipeline_options(root, llm_mode="on", llm_command="/bin/true")
            with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=Client()), patch("georeference_curator.pipeline.preflight_llm"):
                first = run_pipeline(**args)
                second = run_pipeline(**args)
            self.assertEqual(first.failed, 1)
            self.assertEqual(first.excluded_insufficient_locality, 0)
            self.assertEqual(second.resumed_records, 0)
            log = json.loads((root / "out" / "georeference.log.jsonl").read_text())
            self.assertEqual(log["curationStatus"], "failed")
            self.assertEqual(log["stageErrors"][0]["stage"], "coordinate_research")

    def test_early_label_coordinate_avoids_research(self):
        class Client:
            calls = 0
            def create_json(self, *_args, **_kwargs):
                self.calls += 1
                raise AssertionError("No LLM call expected")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="LABEL", country="Japan", locality="Mt Example trail", verbatimCoordinates="WGS84 N 35.123456 E 138.123456")])
            client = Client()
            with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=client), patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(**pipeline_options(root, llm_mode="on", llm_command="/bin/true", curation_mode="robust", label_coordinate_uncertainty_meters=30))
            self.assertEqual(client.calls, 0)
            self.assertEqual(report.accepted, 1)
            self.assertEqual(report.llm_skipped_precise_label, 1)

    def test_truncated_checkpoint_and_configuration_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = RecordJournal(root, {"model": "A"})
            journal.put("done", {"result": "safe"})
            with journal.path.open("ab") as handle:
                handle.write(b'{"broken":')
            replay = RecordJournal(root, {"model": "A"})
            self.assertEqual(replay.get("done"), {"result": "safe"})
            replay.put("next", {"result": "also safe"})
            self.assertIsNotNone(RecordJournal(root, {"model": "A"}).get("next"))
            self.assertIsNone(RecordJournal(root, {"model": "B"}).get("done"))


if __name__ == "__main__":
    unittest.main()
