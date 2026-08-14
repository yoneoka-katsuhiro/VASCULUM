# Data Sources

VASCULUM CheckList v0.1.2 uses these public data services:

- [GBIF Occurrence API](https://techdocs.gbif.org/en/openapi/v1/occurrence)
- [Chinese Virtual Herbarium](https://www.cvh.ac.cn/spms/list.php)
- [OpenStreetMap](https://www.openstreetmap.org/copyright)

Species-list evidence comes from GBIF preserved-specimen occurrence records.
Representative specimen-image candidates come from GBIF first, with CVH used
only as top-up when fewer than ten valid herbarium specimen-image candidates
are available.

For research outputs, cite each underlying dataset and institution using the
information in `records.csv`, `species_list.csv`, the PDF provenance page, and
the original source record pages.
