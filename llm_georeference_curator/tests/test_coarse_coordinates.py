from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_offline import pipeline_options, read_rows, write_rows, select
from georeference_curator.geocoding import extract_decimal_coordinates
from georeference_curator.models import LabelRead
from georeference_curator.pipeline import run_pipeline
from georeference_curator.policy import rounding_radius
from georeference_curator.progress import TerminalProgress, RecordLine
from georeference_curator.scoring import original_coordinate_status


# MNHN Taiwan records as delivered by GBIF: one degree-minute point reused across
# collecting events, no locality text, and a whole-sheet photograph.
MNHN_ROWS = [
    dict(catalogNumber="P02439185", institutionCode="MNHN", country="Chinese Taipei", eventDate="2014-08-02",
         decimalLatitude="23.51667", decimalLongitude="120.8", verbatimElevation="2290.0", associatedMedia="images/P02439185.jpg"),
    dict(catalogNumber="P01187634", institutionCode="MNHN", country="Chinese Taipei", eventDate="2010-06-26",
         decimalLatitude="23.51667", decimalLongitude="120.8", verbatimElevation="2290.0", associatedMedia="images/P01187634.jpg"),
]


def write_photos(root, rows):
    for row in rows:
        photo = root / "input" / row["associatedMedia"]
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"whole specimen photograph " + row["catalogNumber"].encode())


class LabelClient:
    """Reads a label, then returns a refined point inside the coarse minute cell."""

    calls = {"transcription_only": 0, "georeference": 0}

    def __init__(self, settings):
        self.settings = settings

    def create_json(self, *_args, **_kwargs):
        mode = self.settings.task_mode
        LabelClient.calls[mode] += 1
        if mode == "transcription_only":
            return dict(status="ok", labelTranscription="Taiwan. Chiayi Co. Alishan, Tataka trail, 2290 m",
                        localityText="Chiayi Co., Alishan, Tataka trail", elevationText="2290 m")
        return dict(status="accepted", coordinateCandidates=[dict(latitude="23.472913", longitude="120.888714",
            geodeticDatum="WGS84", uncertaintyMeters="1500", confidence="medium", candidateType="refined_georeference",
            evidence="Tataka trail section near 2290 m")])


def run_with_label_client(root):
    LabelClient.calls = {"transcription_only": 0, "georeference": 0}
    with patch("georeference_curator.llm.resolve_cli_command", return_value="/test/bin/codex"), \
            patch("georeference_curator.llm.command_available", return_value=True), \
            patch("georeference_curator.pipeline.make_client", side_effect=LabelClient), \
            patch("georeference_curator.pipeline.preflight_llm"):
        return run_pipeline(**pipeline_options(root, llm_mode="on"))


class CoarseCoordinateTests(unittest.TestCase):
    def test_degree_minute_pairs_get_minute_rounding_not_tenth_degree(self):
        # 23.51667/120.8 is 23°31′/120°48′: about 1.3 km, not the 5 km of a 0.1° step.
        self.assertLess(rounding_radius("23.51667", "120.8"), 1500)
        self.assertGreater(rounding_radius("23.51667", "120.8"), 1000)
        self.assertLess(rounding_radius("23.45", "120.91667"), 1500)
        # Without a ≥3-decimal minute signature the pair is ambiguous; keep the conservative decimal step.
        self.assertGreater(rounding_radius("23.6", "120.95"), 5000)
        self.assertGreater(rounding_radius("35", "138.5"), 50000)
        self.assertEqual(rounding_radius("27.619417", "96.981778"), 1)

    def test_minute_precision_and_shared_points_are_coarse(self):
        self.assertEqual(original_coordinate_status(dict(MNHN_ROWS[0])), "coarse")
        shared = dict(MNHN_ROWS[0], decimalLatitude="23.512345", decimalLongitude="120.812345", _vasculumSharedCoordinate="2")
        self.assertEqual(original_coordinate_status(shared), "coarse")
        precise = dict(MNHN_ROWS[0], decimalLatitude="23.512345", decimalLongitude="120.812345")
        self.assertEqual(original_coordinate_status(precise), "trusted")
        gps = dict(MNHN_ROWS[0], georeferenceProtocol="GPS", coordinateUncertaintyInMeters="30")
        self.assertEqual(original_coordinate_status(gps), "trusted")

    def test_shared_coarse_point_is_reestimated_from_label_photo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "input" / "dwc.tsv", MNHN_ROWS)
            write_photos(root, MNHN_ROWS)
            report = run_with_label_client(root)
            output = read_rows(root / "out" / "modified_dwc.tsv")
            self.assertEqual(report.accepted, 2)
            self.assertEqual(LabelClient.calls, {"transcription_only": 2, "georeference": 2})
            for row in output:
                self.assertEqual((row["decimalLatitude"], row["decimalLongitude"]), ("23.472913", "120.888714"))
                self.assertIn("decision=correct_existing", row["georeferenceRemarks"])
            self.assertEqual(report.llm_skipped_precise_original, 0)

    def test_coarse_point_without_photo_is_kept_with_low_confidence(self):
        row = {key: value for key, value in MNHN_ROWS[0].items() if key != "associatedMedia"}
        result = select(row=row)
        self.assertEqual(result.curation_status, "accepted")
        self.assertEqual(result.decision, "keep_original")
        self.assertEqual(result.row["confidence"], "low")
        self.assertIn("coarse", result.row["georeferenceRemarks"])

    def test_precise_point_without_datum_uses_1000_m_datum_allowance(self):
        row = dict(catalogNumber="X", country="Myanmar", decimalLatitude="27.619417", decimalLongitude="96.981778")
        result = select(row=row)
        self.assertEqual(result.row["coordinateUncertaintyInMeters"], "2000")
        self.assertEqual(result.row["confidence"], "medium")

    def test_printed_seconds_coordinate_is_high_confidence(self):
        label = LabelRead(label_transcription="27°37'9.9\"N, 96°58'54.4\"E Kachin, Hponganrazi WS", catalog_number="E01539315")
        coordinates = extract_decimal_coordinates(label.label_transcription)
        result = select(row=dict(country="Myanmar"), label=label, coordinates=coordinates)
        self.assertEqual(result.curation_status, "accepted")
        self.assertEqual(result.row["confidence"], "high")
        self.assertLessEqual(float(result.row["coordinateUncertaintyInMeters"]), 2100)


class TerminalOutputTests(unittest.TestCase):
    def test_non_terminal_output_has_one_line_per_record_and_no_stage_lines(self):
        progress = TerminalProgress(True)
        stream = io.StringIO()
        with patch("sys.stderr", new=stream):
            progress.configure_run(total_records=2, worker_count=1)
            with progress.activity("x", stage="Label reading", index=1, total=2, catalog="P02439185"):
                pass
            progress.complete_record(RecordLine(completed=1, total=2, catalog="P02439185", result="accepted",
                confidence="medium", radius_meters="1500", basis="label (LLM)", locality="Chiayi Co., Alishan",
                seconds=41.2, tokens=71234))
            progress.finish_run()
        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2, lines)  # column header + one record
        self.assertIn("specimen", lines[0])
        self.assertIn("P02439185", lines[1])
        self.assertIn("1.5 km", lines[1])
        self.assertIn("71k", lines[1])
        self.assertNotIn("Label reading", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
