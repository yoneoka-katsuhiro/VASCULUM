from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from specimen_collector.models import SpecimenRecord
from specimen_collector.outputs import dwc_row
from specimen_collector.records import deduplicate
from specimen_collector.sources import dwc_metadata, gbif_records


class CoordinateEvidenceTests(unittest.TestCase):
    def test_coordinate_provenance_is_preserved_in_dwc(self):
        metadata = dict(geodeticDatum="WGS84", coordinateUncertaintyInMeters="30",
                        georeferenceProtocol="GPS measurement", georeferencedBy="Field collector",
                        datasetName="Example herbarium", datasetKey="dataset-key",
                        license="https://creativecommons.org/licenses/by/4.0/")
        record = SpecimenRecord(source="gbif", decimal_latitude="35.123456",
                                decimal_longitude="138.123456", **dwc_metadata(metadata))
        row = dwc_row(record, "Example species")
        self.assertEqual(row["geodeticDatum"], "WGS84")
        self.assertEqual(row["coordinateUncertaintyInMeters"], "30")
        self.assertEqual(row["georeferenceProtocol"], "GPS measurement")
        self.assertEqual(row["datasetName"], "Example herbarium")
        self.assertEqual(row["datasetID"], "dataset-key")
        self.assertEqual(row["georeferencedBy"], "Field collector")

    def test_duplicates_never_construct_hybrid_coordinate_or_trust_metadata(self):
        def merge(*rows):
            return deduplicate([SpecimenRecord(institution_code="P", catalog_number="A", **row) for row in rows])[0]
        incomplete = merge(dict(source="gbif", decimal_latitude="35"),
                           dict(source="cvh", decimal_longitude="139"))
        self.assertFalse(incomplete.decimal_latitude and incomplete.decimal_longitude)
        mixed = merge(dict(source="gbif", image_url="https://example.org/image.jpg",
                           decimal_latitude="35.123456", decimal_longitude="138.123456"),
                      dict(source="cvh", decimal_latitude="36.123456", decimal_longitude="139.123456",
                           geodetic_datum="WGS84", coordinate_uncertainty_meters="20", georeference_protocol="GPS"))
        self.assertEqual(mixed.decimal_latitude, "35.123456")
        self.assertEqual(mixed.geodetic_datum, "")
        self.assertEqual(mixed.coordinate_uncertainty_meters, "")
        self.assertEqual(dwc_row(mixed, "Example species")["geodeticDatum"], "")
        donor = merge(dict(source="gbif", image_url="https://example.org/a.jpg", decimal_latitude="35"),
                      dict(source="cvh", source_record_url="https://example.org/cvh/A",
                           decimal_latitude="36.123456", decimal_longitude="139.123456",
                           geodetic_datum="WGS84", coordinate_uncertainty_meters="20", georeference_protocol="GPS"))
        self.assertEqual(donor.decimal_latitude, "36.123456")
        self.assertEqual(donor.coordinate_source, "https://example.org/cvh/A")
        self.assertEqual(donor.source, "gbif")
        self.assertEqual(dwc_row(donor, "Example species")["coordinateUncertaintyInMeters"], "20")

    def test_gbif_data_and_media_licenses_are_not_conflated(self):
        class Client:
            def get_json(self, *_args, **_kwargs):
                return dict(endOfRecords=True, results=[dict(key=1, license="data-license",
                    geodeticDatum="WGS84", coordinateUncertaintyInMeters=50,
                    decimalLatitude=35.123456, decimalLongitude=138.123456,
                    georeferenceProtocol="GPS", issues=["TAXON_MATCH_FUZZY"],
                    media=[dict(identifier="https://example.org/photo.jpg", license="image-license")])])
            def sleep(self, _): pass
        with tempfile.TemporaryDirectory() as directory:
            records = gbif_records(Client(), "Example species", Path(directory), {}, None, 0, True)
        row = dwc_row(records[0], "Example species")
        self.assertEqual(row["license"], "data-license")
        self.assertEqual(row["vasculumImageLicense"], "image-license")
        self.assertEqual(row["geospatialIssues"], "")
        record = SpecimenRecord(decimal_latitude="35.123456", decimal_longitude="138.123456",
                                **dwc_metadata(dict(geodeticDatum="WGS84", coordinateUncertaintyInMeters=50,
                                    georeferenceProtocol="GPS", issues=["COORDINATE_COUNTRY_MISMATCH"])))
        self.assertIn("COORDINATE_COUNTRY_MISMATCH", dwc_row(record, "Example species")["geospatialIssues"])


if __name__ == "__main__":
    unittest.main()
