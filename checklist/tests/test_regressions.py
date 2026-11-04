"""Offline geographic, taxonomy, export and CLI integration regressions."""
from __future__ import annotations

import math
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vasculum_checklist.codex_vision import exec_options
from vasculum_checklist.geo import circle_wkt, distance_km, record_matches_area
from vasculum_checklist.models import ChecklistReport, EvidenceRecord, SearchArea, SpeciesEntry, TaxonFilter
from vasculum_checklist.outputs import write_outputs
from vasculum_checklist.pdf import load_font
from vasculum_checklist.pipeline import run_checklist
from vasculum_checklist.sources import resolve_taxon_key


class RegressionTests(unittest.TestCase):
    def test_nonfinite_or_nonpositive_radius_is_rejected(self):
        for radius in (float("nan"), float("inf"), -1.0, 0.0):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                SearchArea(latitude=35, longitude=139, radius_km=radius).validate()

    def test_nonfinite_and_out_of_range_occurrences_do_not_pass_circle_filter(self):
        area = SearchArea(latitude=0, longitude=0, radius_km=1)
        for lat, lon in (("nan", "0"), ("0", "nan"), ("inf", "0"), ("91", "0"), ("0", "181")):
            with self.subTest(lat=lat, lon=lon):
                self.assertFalse(record_matches_area(EvidenceRecord(source="gbif", decimal_latitude=lat, decimal_longitude=lon), area))
        self.assertTrue(record_matches_area(EvidenceRecord(source="gbif", decimal_latitude="0", decimal_longitude="0"), area))

    def test_antipodes_and_antimeridian(self):
        self.assertAlmostEqual(distance_km(0, 0, 0, 180), math.pi * 6371.0088)
        self.assertLess(distance_km(0, 179.999, 0, -179.999), 1)
        self.assertTrue(circle_wkt(35, 139, 1).startswith("POLYGON"))
        with self.assertRaisesRegex(ValueError, "antimeridian"):
            circle_wkt(0, 179.99, 10)

    def test_gbif_taxon_without_rank_does_not_crash_or_invent_a_rank(self):
        client = Mock()
        client.get_json.return_value = dict(usageKey=42, confidence=100, scientificName="Example species")
        with tempfile.TemporaryDirectory() as folder:
            result = resolve_taxon_key(client, TaxonFilter(taxon="Example species"), Path(folder))
        self.assertEqual(result.key, "42")
        self.assertEqual(result.rank, "")

    def test_dry_run_validates_without_network_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "new"
            kwargs = dict(project_dir=ROOT, contact_email="test@example.org", area=SearchArea(country="Japan"),
                          taxon_filter=TaxonFilter(taxon="Ferns"), requested_sources=["gbif"], title="test",
                          output_dir=output, raw_command="test", max_gbif_records=1, page_size=10,
                          gbif_image_limit=1, image_radius_max=500, cvh_image_limit=0, max_pdf_images=1, dry_run=True)
            with patch("vasculum_checklist.pipeline.PoliteHttpClient") as client:
                run_checklist(**kwargs)
                client.assert_not_called()
                self.assertFalse(output.exists())
                with self.assertRaisesRegex(ValueError, "include gbif"):
                    run_checklist(**dict(kwargs, requested_sources=["cvh"]))
                with self.assertRaisesRegex(ValueError, "image-radius-max"):
                    run_checklist(**dict(kwargs, image_radius_max=float("nan")))

    def test_font_fallback_obeys_requested_size(self):
        with patch("vasculum_checklist.pdf.font_path", return_value=""):
            self.assertGreater(load_font(40).getbbox("Species")[3], load_font(12).getbbox("Species")[3])

    def test_codex_capability_probe_requires_no_model_call(self):
        exec_options.cache_clear()
        with patch("vasculum_checklist.codex_vision.subprocess.run", return_value=Mock(returncode=0, stdout="--sandbox --image --output-schema", stderr="")) as run:
            options = exec_options("example-codex")
            self.assertIn("--sandbox", options)
            self.assertNotIn("--ignore-rules", options)
            self.assertEqual(run.call_args.args[0], ["example-codex", "exec", "--help"])
        exec_options.cache_clear()

    def test_real_pdf_and_tables_render_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            report = ChecklistReport(version="test", checklist_version="test", title="Synthetic offline regression",
                search_area=SearchArea(country="Japan"), taxon_filter=TaxonFilter(taxon="Ferns"),
                output_dir=Path(folder), raw_command="offline-test", started_at=datetime.now(timezone.utc),
                species=[SpeciesEntry(canonical_species="Example species", family="Exampleaceae", genus="Example")])
            write_outputs(report)
            self.assertTrue((Path(folder) / "checklist.pdf").read_bytes().startswith(b"%PDF"))
            self.assertIn("Example species", (Path(folder) / "species_list.csv").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
