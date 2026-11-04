"""Regression cases for preserving previous research outputs on reruns."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image
from specimen_collector.images import download_images, resize_existing_image
from specimen_collector.models import SpecimenRecord
from specimen_collector.outputs import SourceReport
from specimen_collector.pipeline import run_pipeline
from specimen_collector.records import image_basename


class SafetyTests(unittest.TestCase):
    def run_collection(self, output: Path, **kwargs):
        return run_pipeline(project_dir=ROOT, contact_email="test@example.org",
                            requested_sources=["gbif"], taxon_names=["Example species"],
                            max_records_per_name=1, skip_images=False,
                            image_resolution="standard", dry_run=False, output_dir=output, **kwargs)

    def test_failed_search_preserves_images_and_previous_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "images").mkdir()
            photo = root / "images" / "old.jpg"
            photo.write_bytes(b"existing research image")
            metadata = root / "dwc.csv"
            metadata.write_bytes(b"existing research table")
            with patch("specimen_collector.pipeline.collect_source_records", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "existing outputs were preserved"):
                    self.run_collection(root, keep_unreferenced_images=False)
            self.assertEqual(photo.read_bytes(), b"existing research image")
            self.assertEqual(metadata.read_bytes(), b"existing research table")

    def test_default_rerun_keeps_unreferenced_photos(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "images").mkdir()
            photo = root / "images" / "old.jpg"
            photo.write_bytes(b"previous photograph")
            record = SpecimenRecord(source="gbif", source_record_id="1")
            with patch("specimen_collector.pipeline.collect_source_records", return_value=([record], "complete")):
                self.run_collection(root)
            self.assertTrue(photo.exists())

    def test_explicit_cleanup_is_blocked_on_partial_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "images").mkdir()
            photo = root / "images" / "old.jpg"
            photo.write_bytes(b"previous photograph")
            metadata = root / "dwc.csv"
            metadata.write_bytes(b"previous complete metadata")
            summary = root / "summary.txt"
            summary.write_bytes(b"previous complete summary")
            record = SpecimenRecord(source="gbif", source_record_id="1")
            with patch("specimen_collector.pipeline.collect_source_records", return_value=([record], "component: failed (offline)")):
                report = self.run_collection(root, keep_unreferenced_images=False)
            self.assertTrue(report.partial_failure)
            self.assertTrue(photo.exists())
            self.assertEqual(metadata.read_bytes(), b"previous complete metadata")
            self.assertEqual(summary.read_bytes(), b"previous complete summary")
            self.assertIn("occurrenceID", (root / "dwc.partial.csv").read_text())
            self.assertIn("Errors: 1", (root / "summary.partial.txt").read_text())

    def test_reuse_io_error_and_failed_redownload_preserve_existing_image(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "images").mkdir()
            record = SpecimenRecord(source="gbif", source_record_id="1", image_url="https://example.org/image.jpg")
            photo = root / "images" / (image_basename(record, "Example species") + ".jpg")
            Image.new("RGB", (30, 40), "green").save(photo, "JPEG")
            original = photo.read_bytes()
            with patch("specimen_collector.images.resize_existing_image", side_effect=OSError("disk full")), \
                 patch("specimen_collector.images.download_and_validate_image", side_effect=RuntimeError("offline")):
                result = download_images(client=Mock(), output_dir=root, records=[record], accepted_name="Example species",
                    source_settings={}, download_settings={"minimum_image_bytes": 1}, skip_images=False,
                    progress=Mock(), source_reports={"gbif": SourceReport("gbif")})
            self.assertEqual(result.failed, 1)
            self.assertEqual(photo.read_bytes(), original)

    def test_image_reuse_does_not_recompress(self):
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "image.jpg"
            Image.new("RGB", (40, 60), "green").save(photo, "JPEG", quality=99)
            original = photo.read_bytes()
            resize_existing_image(photo, 2400, 70)
            self.assertEqual(photo.read_bytes(), original)
            resize_existing_image(photo, 30, 88)
            with Image.open(photo) as image:
                self.assertEqual(image.size, (20, 30))
            resized = photo.read_bytes()
            resize_existing_image(photo, 30, 88)
            self.assertEqual(photo.read_bytes(), resized)


if __name__ == "__main__":
    unittest.main()
