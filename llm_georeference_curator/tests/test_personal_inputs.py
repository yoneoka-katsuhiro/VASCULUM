from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_offline import pipeline_options, read_rows, trusted_row, write_rows
from georeference_curator.inputs import detect_dwc_path, find_image_paths, image_name_matches, read_table
from georeference_curator.personal_inputs import load_inputs
from georeference_curator.pipeline import run_pipeline


def photo(path, data=b"whole specimen photograph"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class PersonalInputsTests(unittest.TestCase):
    def test_auto_personal_dwc_and_photos_preserve_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "dwc.tsv", [trusted_row()])
            personal = root / "personal" / "dwc.tsv"
            write_rows(personal, [dict(catalogNumber="MY001", country="Japan", locality="Mt Example trail")])
            photo(root / "personal" / "images" / "MY001__front.jpg")
            photo(root / "personal" / "images" / "MY001__label.jpg")
            photo(root / "personal" / "images" / "MY002.jpg")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            bundle = load_inputs(root)
            self.assertEqual(len(bundle.rows), 3)
            self.assertEqual(bundle.personal_dwc_records, 1)
            self.assertEqual(bundle.photo_only_records, 1)
            imported = next(row for row in bundle.rows if row["catalogNumber"] == "MY001")
            self.assertEqual(len(find_image_paths(root, imported)), 2)
            self.assertTrue(imported["occurrenceID"].startswith("urn:uuid:"))
            second = load_inputs(root)
            self.assertEqual([row.get("occurrenceID") for row in bundle.rows], [row.get("occurrenceID") for row in second.rows])
            self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))

    def test_personal_photo_can_attach_to_existing_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "dwc.tsv", [trusted_row(catalogNumber="MY001")])
            photo(root / "personal" / "images" / "MY001__label.jpg")
            bundle = load_inputs(root)
            self.assertEqual(len(bundle.rows), 1)
            self.assertEqual(bundle.photo_only_records, 0)
            self.assertEqual(bundle.attached_personal_images, 1)
            self.assertIn("personal/images/MY001__label.jpg", bundle.rows[0]["associatedMedia"])

    def test_unlinked_collector_images_require_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "dwc.tsv", [trusted_row()])
            photo(root / "images" / "EXTRA.jpg")
            default = load_inputs(root)
            self.assertEqual(len(default.rows), 1)
            self.assertEqual(len(default.warnings), 1)
            explicit = load_inputs(root, import_unlinked_images=True)
            self.assertEqual(explicit.photo_only_records, 1)

    def test_name_matching_does_not_attach_AB10_to_AB1(self):
        self.assertTrue(image_name_matches("AB1", "AB1_Taxon"))
        self.assertTrue(image_name_matches("AB_1", "AB1__label"))
        self.assertFalse(image_name_matches("AB1", "AB10_Taxon"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo(root / "images" / "AB10_Taxon.jpg")
            self.assertEqual(find_image_paths(root, dict(catalogNumber="AB1")), [])

    def test_conflicting_csv_tsv_are_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "dwc.tsv", [dict(catalogNumber="A", locality="old")])
            (root / "dwc.csv").write_text("catalogNumber,locality\nA,edited\n")
            with self.assertRaisesRegex(ValueError, "--input-dwc"):
                load_inputs(root)
            bundle = load_inputs(root, input_dwc=root / "dwc.csv")
            self.assertEqual(bundle.rows[0]["locality"], "edited")

    def test_equivalent_csv_tsv_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dwc.tsv").write_text("catalogNumber\tlocality\nA\ttrail\n\n\t\n")
            (root / "dwc.csv").write_text("catalogNumber,locality\nA,trail\n")
            self.assertEqual(detect_dwc_path(root), root / "dwc.tsv")
            self.assertEqual(len(read_table(root / "dwc.tsv")[0]), 1)

    def test_duplicate_ids_and_ambiguous_photographs_fail_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "dwc.tsv", [dict(occurrenceID="same", catalogNumber="A")])
            write_rows(root / "personal" / "dwc.tsv", [dict(occurrenceID="same", catalogNumber="B")])
            with self.assertRaisesRegex(ValueError, "Duplicate occurrenceID"):
                load_inputs(root)
            write_rows(root / "dwc.tsv", [dict(occurrenceID="one", catalogNumber="A"), dict(occurrenceID="two", catalogNumber="A")])
            write_rows(root / "personal" / "dwc.tsv", [dict(occurrenceID="different", catalogNumber="B")])
            photo(root / "personal" / "images" / "A.jpg")
            with self.assertRaisesRegex(ValueError, "multiple"):
                load_inputs(root)

    def test_ambiguous_imported_table_images_are_not_attached_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "dwc.tsv", [trusted_row()])
            write_rows(root / "personal" / "dwc.tsv", [dict(occurrenceID="one", catalogNumber="A"), dict(occurrenceID="two", catalogNumber="A")])
            photo(root / "personal" / "images" / "A.jpg")
            with self.assertRaisesRegex(ValueError, "multiple records"):
                load_inputs(root)

    def test_external_csv_resolves_images_beside_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "collector"
            write_rows(source / "dwc.tsv", [trusted_row()])
            extra = root / "outside" / "my.csv"
            extra.parent.mkdir()
            extra.write_text("catalogNumber,country,locality,associatedMedia\nMY1,Japan,Example trail,images/MY1.jpg\n")
            photo(extra.parent / "images" / "MY1.jpg")
            bundle = load_inputs(source, additional_inputs=[extra])
            row = next(row for row in bundle.rows if row["catalogNumber"] == "MY1")
            self.assertEqual(find_image_paths(source, row), [str((extra.parent / "images" / "MY1.jpg").resolve())])

    def test_personal_row_does_not_inherit_collector_image_by_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "dwc.tsv", [trusted_row(catalogNumber="A", occurrenceID="portal:A")])
            photo(root / "images" / "A.jpg")
            write_rows(root / "personal" / "dwc.tsv", [dict(catalogNumber="A", occurrenceID="my:A", locality="Another trail")])
            bundle = load_inputs(root)
            personal = next(row for row in bundle.rows if row["occurrenceID"] == "my:A")
            self.assertEqual(find_image_paths(root, personal), [])

    def test_photo_only_input_and_disable_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo(root / "personal" / "images" / "MY1.jpg")
            self.assertEqual(load_inputs(root).photo_only_records, 1)
            with self.assertRaises(FileNotFoundError):
                load_inputs(root, include_personal=False)
            write_rows(root / "dwc.tsv", [trusted_row()])

    def test_same_photo_stem_in_different_subdirectories_stays_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo(root / "personal" / "images" / "trip_a" / "MY1.jpg")
            photo(root / "personal" / "images" / "trip_b" / "MY1.jpg")
            bundle = load_inputs(root)
            self.assertEqual(bundle.photo_only_records, 2)
            self.assertEqual(len({row["occurrenceID"] for row in bundle.rows}), 2)

    def test_added_rows_resume_unchanged_records_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row()])
            args = pipeline_options(root)
            run_pipeline(**args)
            personal = root / "input" / "personal" / "dwc.tsv"
            write_rows(personal, [dict(catalogNumber="MY1", country="Japan", locality="First trail")])
            added = run_pipeline(**args)
            self.assertEqual(added.records_read, 2)
            self.assertEqual(added.resumed_records, 1)
            write_rows(personal, [dict(catalogNumber="MY1", country="Japan", locality="Edited trail")])
            edited = run_pipeline(**args)
            self.assertEqual(edited.resumed_records, 1)
            exported = read_rows(root / "out" / "modified_dwc.tsv")
            self.assertEqual([row["catalogNumber"] for row in exported], ["GPS"])
            log = [json.loads(line) for line in (root / "out" / "georeference.log.jsonl").read_text().splitlines()]
            personal_result = next(entry for entry in log if entry["catalogNumber"] == "MY1")
            self.assertFalse(personal_result["includeInDwc"])
            candidates = read_rows(root / "out" / "georeference_candidates.tsv")
            self.assertEqual(personal_result["decision"], "exclude_missing_source_image")
            self.assertEqual(len(candidates), 2)

    def test_both_whole_images_reach_label_stage_and_candidate_audit(self):
        class Client:
            def __init__(self, settings): self.settings, self.calls = settings, []
            def create_json(inner, _prompt, image_paths=None):
                inner.calls.append(list(image_paths or []))
                self.assertEqual(inner.settings.task_mode, "transcription_only")
                self.assertEqual(len(image_paths), 2)
                return {"status": "ok", "labelTranscription": "WGS84 N 35.123456 E 138.123456. Example mountain trail, 2040 m", "localityText": "Example mountain trail", "elevationText": "2040 m", "detectedLanguages": ["en"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row()])
            for part in ("front", "label"):
                photo(root / "input" / "personal" / "images" / f"MY1__{part}.jpg")
            clients = []
            def factory(settings):
                client = Client(settings)
                clients.append(client)
                return client
            with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", side_effect=factory), patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(**pipeline_options(root, llm_mode="on", label_coordinate_uncertainty_meters=30))
            self.assertEqual(len(clients[0].calls), 0)
            self.assertEqual(len(clients[1].calls), 1)
            self.assertEqual(report.accepted, 2)
            candidate = next(row for row in read_rows(root / "out" / "georeference_candidates.tsv") if row["catalogNumber"] == "MY1")
            self.assertEqual(candidate["inputType"], "personal_photo")
            self.assertIn("MY1__front.jpg", candidate["imagePaths"])
            self.assertIn("MY1__label.jpg", candidate["imagePaths"])
            log = [json.loads(line) for line in (root / "out" / "georeference.log.jsonl").read_text().splitlines()]
            self.assertEqual(len(log[1]["provenance"]["images"]), 2)

    def test_dwc_only_untrusted_records_do_not_reach_research(self):
        class Client:
            calls = []
            def create_json(self, prompt, image_paths=None):
                self.calls.append((prompt, image_paths))
                return {"status": "ok", "coordinateCandidates": [], "remarks": "unresolved"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row()])
            write_rows(root / "input" / "personal" / "dwc.tsv", [dict(catalogNumber="MY1", country="Japan", locality="Specific mountain trail")])
            client = Client()
            with patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=client), patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(**pipeline_options(root, llm_mode="on"))
            self.assertEqual(report.personal_dwc_records, 1)
            self.assertEqual(len(client.calls), 0)
            self.assertEqual(report.excluded_missing_source_image, 1)
            self.assertEqual(report.records_written, 1)

    def test_cli_exposes_manual_input_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", [trusted_row()])
            write_rows(root / "my.tsv", [dict(catalogNumber="MY1", country="Japan", locality="Specific trail")])
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "curate_georeferences.py"), "--input", str(root / "input"), "--additional-input", str(root / "my.tsv"), "--no-include-personal", "--llm-mode", "off", "--no-geospatial-refinement", "--dry-run"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("2", result.stdout)

    def test_handoff_pause_honors_yes_no_even_with_unattended_option(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "run_collect_and_georeference.sh"
            shutil.copyfile(ROOT.parent / wrapper.name, wrapper)
            collector = root / "herbarium_specimen_collector" / "run_collect_specimens.sh"
            curator = root / "llm_georeference_curator" / "run_llm_georeference_curator.sh"
            collector.parent.mkdir()
            curator.parent.mkdir()
            collector.write_text('#!/usr/bin/env bash\nprintf "Output: %s\\n" "$STUB_OUTPUT"\n')
            curator.write_text('#!/usr/bin/env bash\nprintf "CURATOR_RAN: %s\\n" "$*"\n')
            env = dict(os.environ, STUB_OUTPUT=str(root / "collector-output"))
            args = ["bash", str(wrapper), "--pause-before-curation", "--", "--yes", "--additional-input", "my.tsv"]
            stopped = subprocess.run(args, input="n\n", capture_output=True, text=True, env=env)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertNotIn("CURATOR_RAN", stopped.stdout)
            continued = subprocess.run(args, input="y\n", capture_output=True, text=True, env=env)
            self.assertEqual(continued.returncode, 0, continued.stderr)
            self.assertIn("CURATOR_RAN", continued.stdout)
            self.assertIn("--additional-input my.tsv", continued.stdout)


if __name__ == "__main__":
    unittest.main()
