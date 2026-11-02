# Offline Geographic Screen

geographic_screen.json is derived from Natural Earth v5.1.2 GeoJSON:

- ne_50m_admin_0_countries: names, simplified outlines and computed centroids.
- ne_10m_admin_1_states_provinces: names and bounding boxes, not exact polygons.
- ne_110m_populated_places: national capital points.

The JSON records original URLs and SHA-256 hashes. Natural Earth data are
[public domain](https://www.naturalearthdata.com/about/terms-of-use/).
Its default boundaries are contemporary de facto references, not a political
or historical adjudication. The 25 km screening tolerance is a VASCULUM
operational rule, not Natural Earth's advertised positional accuracy.

Regenerate with the original three files in one directory:

```bash
python scripts/build_geographic_screen.py SOURCE_DIRECTORY data/geographic_screen.json
```

shapely performs geometry operations; no analysis-time download or paid map API
is needed for these basic checks. The dataset is only a coarse screen, not a
gazetteer for inventing fine collecting localities.
