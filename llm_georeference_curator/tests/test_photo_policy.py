from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from curate_georeferences import parse_args
from georeference_curator.pipeline import run_pipeline
from test_offline import pipeline_options, read_rows, trusted_row, write_label_photos, write_rows


class PhotoPolicyTests(unittest.TestCase):
    def test_trusted_original_needs_no_photo_provider_or_research(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = trusted_row(decimalLatitude="35.123456789", decimalLongitude="138.123456789")
            write_rows(root / "input" / "dwc.tsv", [row])
            with patch("georeference_curator.pipeline.make_client", side_effect=AssertionError("No provider needed")), patch("georeference_curator.pipeline.preflight_llm", side_effect=AssertionError("No LLM confirmation needed")):
                report = run_pipeline(**pipeline_options(root, llm_mode="on"))
            result = read_rows(root / "out" / "modified_dwc.tsv")[0]
            self.assertEqual(result["decimalLatitude"], row["decimalLatitude"])
            self.assertEqual(result["decimalLongitude"], row["decimalLongitude"])
            self.assertEqual(report.llm_provider_attempts, 0)

    def test_no_photo_cannot_use_detailed_dwc_or_verbatim_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(
                catalogNumber="NOIMAGE", country="Japan", locality="Very specific mountain trail, camp 2",
                decimalLatitude="35.123456", decimalLongitude="138.123456",
                verbatimCoordinates="WGS84 N 35.654321 E 138.654321",
                associatedMedia="https://example.org/remote.jpg",
            )])
            with patch("georeference_curator.pipeline.make_client", side_effect=AssertionError("No provider needed")):
                report = run_pipeline(**pipeline_options(root, llm_mode="on"))
            self.assertEqual(report.excluded_missing_source_image, 1)
            self.assertEqual(report.records_written, 0)
            row = read_rows(root / "out" / "georeference_candidates.tsv")[0]
            self.assertEqual(row["decision"], "exclude_missing_source_image")
            self.assertEqual(row["labelTranscription"], "")

    def test_transcription_without_source_image_cannot_bypass_screening(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan")])
            write_rows(root / "labels.tsv", [dict(catalogNumber="A", imagePath="images/missing.jpg",
                labelTranscription="WGS84 N 35.123456 E 138.123456", localityText="Specific trail")])
            report = run_pipeline(**pipeline_options(root, label_tsv=root / "labels.tsv", label_coordinate_uncertainty_meters=30))
            self.assertEqual(report.records_written, 0)
            self.assertEqual(report.excluded_missing_source_image, 1)

    def test_printed_coordinates_need_only_image_reading_not_research(self):
        calls = []
        class Client:
            def __init__(self, settings): self.settings = settings
            def create_json(self, _prompt, image_paths=None):
                calls.append((self.settings.task_mode, list(image_paths or [])))
                if self.settings.task_mode != "transcription_only":
                    raise AssertionError("Printed coordinates must not require geographic research")
                return dict(status="ok", labelTranscription="WGS84 N 35.123456 E 138.123456, 6500 ft",
                    localityText="Specific mountain trail", elevationText="6500 ft")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan")])
            image = root / "input" / "images" / "A.jpg"
            image.parent.mkdir()
            image.write_bytes(b"whole sheet")
            with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", side_effect=Client), patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(**pipeline_options(root, llm_mode="on", label_coordinate_uncertainty_meters=30))
            self.assertEqual([task for task, _ in calls], ["transcription_only"])
            self.assertEqual(len(calls[0][1]), 1)
            self.assertEqual(report.records_written, 1)
            result = read_rows(root / "out" / "modified_dwc.tsv")[0]
            self.assertEqual(result["decimalLatitude"], "35.123456")
            self.assertEqual(result["minimumElevationInMeters"], "1981")

    def test_label_locality_reaches_research_with_dwc_as_context(self):
        calls = []
        class Client:
            def __init__(self, settings): self.settings = settings
            def create_json(self, prompt, image_paths=None):
                calls.append((self.settings.task_mode, prompt, list(image_paths or [])))
                if self.settings.task_mode == "transcription_only":
                    return dict(status="ok", labelTranscription="Specific old trail, camp 2",
                        localityText="Specific old trail, camp 2")
                return dict(status="unresolved", coordinateCandidates=[], remarks="No verified site")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan", locality="Abbreviated")])
            image = root / "input" / "images" / "A.jpg"
            image.parent.mkdir()
            image.write_bytes(b"whole sheet")
            with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", side_effect=Client), patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(**pipeline_options(root, llm_mode="on"))
            self.assertEqual([task for task, _, _ in calls], ["transcription_only", "georeference"])
            self.assertIn("Specific old trail, camp 2", calls[1][1])
            self.assertEqual(calls[1][2], [])
            self.assertEqual(report.llm_georeference_requests, 1)

    def test_unreadable_broad_or_missing_label_never_uses_dwc_fallback(self):
        cases = [
            (dict(status="unreadable", labelTranscription="", localityText=""), "review"),
            (dict(status="ok", labelTranscription="Japan", localityText="Japan"), "excluded"),
            (dict(status="ok", labelTranscription="Nagano Prefecture", localityText="Nagano Prefecture"), "excluded"),
            (dict(status="ok", labelTranscription="Collector Smith [locality illegible]", localityText=""), "review"),
        ]
        for response, status in cases:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan",
                    stateProvince="Nagano Prefecture", locality="A detailed trail only in DwC",
                    verbatimCoordinates="WGS84 N 35.123456 E 138.123456")])
                image = root / "input" / "images" / "A.jpg"
                image.parent.mkdir()
                image.write_bytes(b"whole sheet")
                calls = []
                class Client:
                    def __init__(self, settings): self.settings = settings
                    def create_json(self, _prompt, image_paths=None):
                        calls.append(self.settings.task_mode)
                        if self.settings.task_mode != "transcription_only":
                            raise AssertionError("DwC locality must not rescue an unreadable or broad label")
                        return response
                with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", side_effect=Client), patch("georeference_curator.pipeline.preflight_llm"):
                    report = run_pipeline(**pipeline_options(root, llm_mode="on", label_coordinate_uncertainty_meters=30))
                self.assertEqual(calls, ["transcription_only"])
                self.assertEqual(report.records_written, 0)
                result = json.loads((root / "out" / "georeference.log.jsonl").read_text())
                self.assertEqual(result["curationStatus"], status)

    def test_unread_photo_with_llm_off_does_not_parse_dwc_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [dict(catalogNumber="A", country="Japan",
                locality="Specific trail", verbatimCoordinates="WGS84 N 35.123456 E 138.123456")])
            image = root / "input" / "images" / "A.jpg"
            image.parent.mkdir()
            image.write_bytes(b"whole sheet")
            report = run_pipeline(**pipeline_options(root, label_coordinate_uncertainty_meters=30))
            self.assertEqual(report.records_written, 0)
            self.assertEqual(report.llm_attempted, 0)
            self.assertEqual(report.excluded_insufficient_locality, 0)

    def test_experimental_and_no_effect_flags_are_removed(self):
        for flags in (["--georeference-method", "xie-baseline"], ["--prompt-profile", "xie-modified"],
                      ["--original-precision-decimals", "4"], ["--no-exclude-insufficient-locality"],
                      ["--taxon-habitat", "forest"], ["--debug-log"], ["--llm-timeout-seconds", "600"]):
            with self.subTest(flags=flags), patch.object(sys, "argv", ["curate", "--input", "input", *flags]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parse_args()
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
