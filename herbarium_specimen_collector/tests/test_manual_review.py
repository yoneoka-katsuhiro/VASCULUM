from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from specimen_collector.images import download_images, prune_unreferenced_images
from specimen_collector.models import SpecimenRecord
from specimen_collector.outputs import RunReport, SourceReport, write_dwc_exports, write_summary
from specimen_collector.records import image_basename


class CollectorManualReviewTests(unittest.TestCase):
    def test_collector_does_not_redownload_a_marked_specimen(self):
        for skip_images in (False, True):
            with self.subTest(skip_images=skip_images), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                record = SpecimenRecord(source="test", catalog_number="MY1", image_url="https://example.org/image.jpg", occurrence_id="occ:MY1")
                marked = root / "images" / ("miss_" + image_basename(record, "Example species") + ".jpg")
                marked.parent.mkdir()
                marked.write_bytes(b"human-excluded specimen original")
                client = Mock()
                result = download_images(client=client, output_dir=root, records=[record], accepted_name="Example species",
                    source_settings={}, download_settings={}, skip_images=skip_images,
                    progress=Mock(), source_reports={"test": SourceReport("test")})
                self.assertEqual(result.excluded, 1)
                self.assertEqual(record.download_status, "excluded_manual_misidentification")
                self.assertEqual(result.downloaded, 0)
                self.assertEqual(client.mock_calls, [])
                write_dwc_exports(root, [record], "Example species")
                for name, delimiter in (("dwc.tsv", "\t"), ("dwc.csv", ",")):
                    with (root / name).open() as handle:
                        self.assertEqual(list(csv.DictReader(handle, delimiter=delimiter)), [])
                self.assertEqual(prune_unreferenced_images(root, [record]), 0)
                self.assertEqual(marked.read_bytes(), b"human-excluded specimen original")
                report = RunReport(version="test", accepted_name="Example species", search_names=["Example species"],
                    image_resolution="standard", output_dir=root, started_at=datetime.now(timezone.utc), records=[record])
                self.assertEqual(report.records_written, 0)
                write_summary(root / "summary.txt", report)
                self.assertIn("Specimens excluded by manual miss_ review: 1", (root / "summary.txt").read_text())
                self.assertIn("occ:MY1", (root / "summary.txt").read_text())

    def test_pruning_preserves_marked_files_but_removes_stale_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / "images"
            images.mkdir()
            marked, stale = images / "MISS_MY1.jpg", images / "stale.jpg"
            marked.write_bytes(b"keep marker")
            stale.write_bytes(b"old unreferenced download")
            self.assertEqual(prune_unreferenced_images(root, []), 1)
            self.assertTrue(marked.exists())
            self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
