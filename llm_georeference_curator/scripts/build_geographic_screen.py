"""Regenerate compact, offline plausibility references from Natural Earth v5.1.2."""
from pathlib import Path
import argparse
import hashlib
import json
from shapely.geometry import mapping, shape


def build(source_dir, destination):
    names = {"countries": "ne_50m_admin_0_countries.geojson",
             "admin1": "ne_10m_admin_1_states_provinces.geojson",
             "capitals": "ne_110m_populated_places.geojson"}
    result = {"version": "Natural Earth v5.1.2", "license": "public domain", "sources": {}}
    for kind, filename in names.items():
        path = source_dir / filename
        raw = path.read_bytes()
        result["sources"][kind] = {"url": f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/{filename}",
                                   "sha256": hashlib.sha256(raw).hexdigest()}
        records = []
        for feature in json.loads(raw)["features"]:
            properties = feature["properties"]
            geometry = shape(feature["geometry"])
            aliases = sorted({str(value) for key, value in properties.items() if value and value != -99 and
                (key.lower().startswith("name") or key.lower() in {"admin", "geonunit", "postal", "iso_a2", "iso_a3", "adm0_a3", "iso_3166_2", "woe_name", "gn_name"})})
            if kind == "countries":
                records.append(dict(code=properties["ADM0_A3"], names=aliases,
                    geometry=mapping(geometry.simplify(0.025, preserve_topology=True)),
                    center=[geometry.centroid.y, geometry.centroid.x]))
            elif kind == "admin1":
                records.append(dict(country=properties.get("adm0_a3"), names=aliases,
                    bounds=list(geometry.bounds)))
            elif properties.get("ADM0CAP") == 1:
                records.append(dict(country=properties.get("ADM0_A3"), name=properties.get("NAME"),
                    latitude=geometry.y, longitude=geometry.x))
        result[kind] = records
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="Directory containing the three original Natural Earth GeoJSON files.")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build(args.source_dir, args.destination)
