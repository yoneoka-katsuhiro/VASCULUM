from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from georeference_curator.ribbon import ribbon_counts, source_group, prepare_ribbon_renderer, renderer_provenance, write_ribbon_plot
from georeference_curator.pipeline import run_pipeline
from test_offline import pipeline_options, trusted_row, write_rows


def result(eligible, accepted):
    return SimpleNamespace(provenance={"sourceEvidenceEligible": eligible}, include_in_dwc=accepted)


class RibbonTests(unittest.TestCase):
    def test_source_attribution_is_explicit_not_guessed_from_mixed_history(self):
        self.assertEqual(source_group({"vasculumPrimarySource": "cvh", "datasetName": "gbif; cvh"}), "CVH")
        self.assertEqual(source_group({"datasetName": "GBIF"}), "GBIF")
        self.assertEqual(source_group({"datasetName": "taif; tai"}), "Other")
        self.assertEqual(source_group({"vasculumInputType": "personal_photo"}), "Other")
        with self.assertRaisesRegex(ValueError, "no source attribution was guessed"):
            source_group({"datasetName": "gbif; cvh"})

    def test_legacy_inputs_start_after_collection_without_fabricated_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [{"datasetName": "gbif"}, {"datasetName": "cvh"}, {}]
            counts = ribbon_counts(rows, [result(True, True), result(False, False), result(True, False)], root, None, complete_input=True)
            self.assertEqual(counts[0], dict(source="GBIF", input=1, evidence=1, accepted=1))
            self.assertEqual(counts[1], dict(source="CVH", input=1, evidence=0, accepted=0))
            self.assertEqual(counts[2], dict(source="Other", input=1, evidence=1, accepted=0))

    def test_verified_collection_history_includes_pre_curator_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dwc = root / "dwc.tsv"
            dwc.write_text("source\ngbif\n")
            data = dict(version=1, dwcSha256=hashlib.sha256(dwc.read_bytes()).hexdigest(),
                retrieved={"GBIF": 4, "CVH": 2}, unique={"GBIF": 1, "CVH": 1}, excluded={"CVH": 1})
            (root / "summary.txt").write_text("Flow provenance: " + json.dumps(data))
            rows = [{"vasculumPrimarySource": "gbif"}]
            counts = ribbon_counts(rows, [result(True, True)], root, dwc, complete_input=True)
            self.assertEqual(counts[0], dict(source="GBIF", collected=4, unique=1, evidence=1, accepted=1))
            self.assertEqual(counts[1], dict(source="CVH", collected=2, unique=1, evidence=0, accepted=0))
            self.assertIn("input", ribbon_counts(rows, [result(True, True)], root, dwc, complete_input=False)[0])
            dwc.write_text("edited\n")
            self.assertIn("input", ribbon_counts(rows, [result(True, True)], root, dwc, complete_input=True)[0])

    def test_invalid_collection_counts_fall_back_to_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dwc = root / "dwc.tsv"
            dwc.write_text("input")
            data = dict(version=1, dwcSha256=hashlib.sha256(dwc.read_bytes()).hexdigest(),
                retrieved={"GBIF": -1}, unique={"GBIF": 1}, excluded={})
            (root / "summary.txt").write_text("Flow provenance: " + json.dumps(data))
            counts = ribbon_counts([{"datasetName": "gbif"}], [result(True, True)], root, dwc, complete_input=True)
            self.assertIn("input", counts[0])
            with self.assertRaisesRegex(ValueError, "source-evidence"):
                ribbon_counts([{}], [result(False, True)], root, dwc, complete_input=False)

    def test_real_R_renderer_handles_empty_and_all_excluded_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = prepare_ribbon_renderer(ROOT)
            versions = renderer_provenance(ROOT, executable)
            self.assertEqual(set(versions), {"R", "ggplot2", "ggalluvial", "rendererSha256"})
            self.assertEqual(len(versions["rendererSha256"]), 64)
            for size in (0, 1, 15):
                with self.subTest(size=size):
                    counts = [{"source": source, "input": size, "evidence": 0, "accepted": 0}
                              for source in ("GBIF", "CVH", "Other")]
                    output = write_ribbon_plot(ROOT, root, counts, executable)
                    self.assertTrue(output.read_bytes().startswith(b"%PDF"))
                    self.assertGreater(output.stat().st_size, 1000)
                    self.assertEqual(set(path.name for path in root.iterdir()), {"georeference_flow.pdf"})

    def test_missing_R_stops_before_LLM_calls_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row()])
            with patch("georeference_curator.ribbon.shutil.which", return_value=None), patch("georeference_curator.pipeline.preflight_llm") as preflight:
                with self.assertRaisesRegex(RuntimeError, "Rscript"):
                    run_pipeline(**pipeline_options(root))
            preflight.assert_not_called()
            self.assertFalse((root / "out").exists())

    def test_four_stage_R_plot_conserves_sources_with_tiny_final_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            counts = [{"source": source, "collected": 100, "unique": 80, "evidence": 60, "accepted": 1}
                      for source in ("GBIF", "CVH", "Other")]
            output = write_ribbon_plot(ROOT, root, counts, prepare_ribbon_renderer(ROOT))
            self.assertTrue(output.read_bytes().startswith(b"%PDF"))
            self.assertGreater(output.stat().st_size, 1000)

    def test_plot_failure_preserves_tables_and_removes_stale_plot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row()])
            (root / "out").mkdir()
            (root / "out" / "georeference_flow.pdf").write_bytes(b"stale PDF")
            with patch("georeference_curator.pipeline.write_ribbon_plot", side_effect=RuntimeError("test renderer failure")):
                report = run_pipeline(**pipeline_options(root))
            self.assertEqual(report.records_written, 1)
            self.assertTrue((root / "out" / "modified_dwc.tsv").exists())
            self.assertFalse((root / "out" / "georeference_flow.pdf").exists())
            self.assertTrue(any("Ribbon plot" in error for error in report.errors))


if __name__ == "__main__":
    unittest.main()
