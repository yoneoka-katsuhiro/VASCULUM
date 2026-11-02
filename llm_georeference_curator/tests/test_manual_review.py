from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_offline import pipeline_options, read_rows, trusted_row, write_rows
from test_personal_inputs import photo
from georeference_curator.inputs import find_image_paths
from georeference_curator.personal_inputs import load_inputs
from georeference_curator.pipeline import run_pipeline


class ManualReviewTests(unittest.TestCase):
    def test_renamed_collector_image_excludes_record_without_llm_calls(self):
        class ForbiddenClient:
            def create_json(self, *_args, **_kwargs):
                raise AssertionError("Excluded specimens must not reach an LLM")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row(catalogNumber="BAD", associatedMedia="images/BAD_Taxon.jpg")])
            marked = root / "input" / "images" / "miss_BAD_Taxon.jpg"
            photo(marked)
            with patch("georeference_curator.llm.resolve_cli_command", return_value="/test/bin/codex"), patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=ForbiddenClient()), patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(**pipeline_options(root, llm_mode="on", export_images=True))
            self.assertEqual(report.excluded_manual_misidentification, 1)
            self.assertEqual(report.records_written, 0)
            self.assertEqual(report.llm_attempted, 0)
            self.assertEqual(report.unresolved, 0)
            self.assertTrue(marked.exists())
            self.assertEqual(read_rows(root / "out" / "modified_dwc.tsv"), [])
            candidate = read_rows(root / "out" / "georeference_candidates.tsv")[0]
            self.assertEqual(candidate["decision"], "exclude_manual_misidentification")
            self.assertIn(marked.name, candidate["curationReasons"])
            log = json.loads((root / "out" / "georeference.log.jsonl").read_text())
            self.assertFalse(log["includeInDwc"])
            self.assertEqual(log["curationStatus"], "excluded")
            self.assertIn("Excluded by manual miss_ review: 1", (root / "out" / "summary.txt").read_text())

    def test_one_marked_view_excludes_the_entire_personal_specimen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo(root / "personal" / "images" / "MY1__front.jpg")
            photo(root / "personal" / "images" / "miss_MY1__label.jpg")
            bundle = load_inputs(root)
            self.assertEqual(len(bundle.rows), 1)
            self.assertEqual(bundle.rows[0]["catalogNumber"], "MY1")
            self.assertIn("personal/images/miss_MY1__label.jpg", bundle.rows[0]["_vasculumMissMarkers"])
            self.assertEqual(find_image_paths(root, bundle.rows[0]), ["personal/images/MY1__front.jpg"])

    def test_explicit_marked_media_and_external_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            write_rows(input_dir / "dwc.tsv", [trusted_row()])
            external = root / "external"
            write_rows(external / "dwc.tsv", [dict(catalogNumber="MY1", locality="Specific trail", associatedMedia="images/MISS_MY1.jpg")])
            marked = external / "images" / "MISS_MY1.jpg"
            photo(marked)
            bundle = load_inputs(input_dir, additional_inputs=[external / "dwc.tsv"])
            imported = next(row for row in bundle.rows if row["catalogNumber"] == "MY1")
            self.assertEqual(imported["_vasculumMissMarkers"], [str(marked.resolve())])
            self.assertEqual(find_image_paths(input_dir, imported), [])

    def test_marker_does_not_match_a_longer_catalog_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "dwc.tsv", [trusted_row(catalogNumber="AB1"), trusted_row(catalogNumber="AB10", occurrenceID="other")])
            photo(root / "images" / "miss_AB10_Taxon.jpg")
            bundle = load_inputs(root)
            self.assertNotIn("_vasculumMissMarkers", bundle.rows[0])
            self.assertIn("_vasculumMissMarkers", bundle.rows[1])

    def test_manual_exclusion_overrides_trusted_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row(catalogNumber="BAD")])
            photo(root / "input" / "personal" / "images" / "miss_BAD.jpg")
            report = run_pipeline(**pipeline_options(root))
            self.assertEqual(report.records_written, 0)
            self.assertEqual(report.excluded_manual_misidentification, 1)

    def test_adding_removing_marker_updates_resume_and_managed_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            original = source / "images" / "MY1.jpg"
            marked = original.with_name("miss_" + original.name)
            photo(original)
            write_rows(source / "dwc.tsv", [trusted_row(catalogNumber="MY1", associatedMedia="images/MY1.jpg")])
            args = pipeline_options(root, export_images=True)
            first = run_pipeline(**args)
            self.assertEqual(first.images_exported, 1)
            exported = list((root / "out" / "images").rglob("*.jpg"))
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0].read_bytes(), original.read_bytes())
            original.rename(marked)
            second = run_pipeline(**dict(args, export_images=False))
            self.assertEqual(second.resumed_records, 0)
            self.assertEqual(second.records_written, 0)
            self.assertEqual(second.images_exported, 0)
            self.assertEqual(list((root / "out" / "images").rglob("*.jpg")), [])
            self.assertTrue(marked.exists())
            third = run_pipeline(**dict(args, export_images=False))
            self.assertEqual(third.resumed_records, 1)
            self.assertEqual(third.records_written, 0)
            marked.rename(original)
            restored = run_pipeline(**args)
            self.assertEqual(restored.records_written, 1)
            self.assertEqual(restored.images_exported, 1)
            self.assertTrue(original.exists())

    def test_personal_dwc_marker_and_sidecar_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row(catalogNumber="BASE")])
            write_rows(root / "input" / "personal" / "dwc.tsv", [dict(catalogNumber="MY1", locality="Specific trail")])
            photo(root / "input" / "personal" / "images" / "miss_MY1.jpg")
            photo(root / "input" / "photos" / "miss_othername.jpg")
            write_rows(root / "labels.tsv", [dict(catalogNumber="BASE", imagePath="photos/miss_othername.jpg")])
            report = run_pipeline(**pipeline_options(root, label_tsv=root / "labels.tsv"))
            self.assertEqual(report.excluded_manual_misidentification, 2)
            self.assertEqual(report.records_written, 0)

    def test_final_export_copies_only_included_unmarked_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row(catalogNumber="GOOD", occurrenceID="good"), trusted_row(catalogNumber="BAD", occurrenceID="bad")])
            photo(root / "input" / "images" / "GOOD.jpg")
            photo(root / "input" / "images" / "miss_BAD.jpg")
            run_pipeline(**pipeline_options(root, export_images=True))
            files = list((root / "out" / "images").rglob("*"))
            self.assertFalse(any(path.name.startswith("miss_") for path in files))
            rows = read_rows(root / "out" / "modified_dwc.tsv")
            self.assertEqual([row["catalogNumber"] for row in rows], ["GOOD"])
            self.assertTrue((root / "out" / rows[0]["associatedMedia"]).is_file())

    def test_unknown_output_images_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row()])
            unknown = root / "out" / "images" / "my_own_original.jpg"
            photo(unknown, b"user original")
            with self.assertRaisesRegex(ValueError, "fresh --output"):
                run_pipeline(**pipeline_options(root, export_images=True))
            self.assertEqual(unknown.read_bytes(), b"user original")


    def test_managed_output_symlink_cannot_touch_source_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "input" / "images" / "GPS.jpg"
            photo.parent.mkdir(parents=True)
            photo.write_bytes(b"source photograph")
            write_rows(root / "input" / "dwc.tsv", [trusted_row(associatedMedia="images/GPS.jpg")])
            (root / "out").mkdir()
            (root / "out" / "images").symlink_to(photo.parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                run_pipeline(**pipeline_options(root, export_images=True))
            self.assertEqual(photo.read_bytes(), b"source photograph")


if __name__ == "__main__":
    unittest.main()
