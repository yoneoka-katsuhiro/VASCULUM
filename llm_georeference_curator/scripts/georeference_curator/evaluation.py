from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from .checkpoints import file_hash, fingerprint
from .geocoding import extract_decimal_coordinates, haversine_km, parse_decimal, valid_lat_lon
from .inputs import read_table
from .outputs import atomic_text, write_table
from .policy import documented_original, positive_number, to_wgs84


LOCALITY_FIELDS = ("country", "stateProvince", "county", "locality")
DEFENSIBLE_UNCERTAINTY = {"documented_original", "documented_label", "verified_reference_extent", "mapped_locality_extent"}


def identity(row):
    return str(row.get("occurrenceID") or row.get("catalogNumber") or "").strip()


def prepare(input_path, output, *, seed="vasculum-v1", evaluation_fraction=0.8):
    if not 0 < evaluation_fraction < 1:
        raise ValueError("evaluation_fraction must be between 0 and 1")
    rows, _ = read_table(input_path)
    retained, excluded, seen = [], [], set()
    for row in rows:
        key = identity(row)
        reason = ""
        try:
            if not key or key in seen:
                raise ValueError("missing_or_duplicate_record_identity")
            seen.add(key)
            text = "\n".join(str(row.get(field, "")) for field in LOCALITY_FIELDS)
            if extract_decimal_coordinates(text):
                raise ValueError("embedded_locality_coordinates")
            if not documented_original(row):
                raise ValueError("ground_truth_not_documented_GPS_or_verified")
            lat, lon = parse_decimal(row.get("decimalLatitude")), parse_decimal(row.get("decimalLongitude"))
            if lat is None or lon is None or not valid_lat_lon(lat, lon) or (lat == 0 and lon == 0):
                raise ValueError("ground_truth_invalid")
            point = to_wgs84(lat, lon, row.get("geodeticDatum", ""))
            if point.accuracy_meters is None:
                raise ValueError("ground_truth_transformation_accuracy_unknown")
            if not str(row.get("locality", "")).strip():
                raise ValueError("missing_specific_locality")
            retained.append((row, point))
        except (ValueError, RuntimeError) as exc:
            reason = str(exc)
        if reason:
            excluded.append({"recordID": key, "reason": reason})

    # Connected groups keep shared sites and collecting events in the same split.
    parent = list(range(len(retained)))
    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    keys = {}
    for index, (row, _) in enumerate(retained):
        locality_key = fingerprint([str(row.get(field, "")).strip().casefold() for field in LOCALITY_FIELDS])
        grouping = ["site:" + locality_key]
        if row.get("eventID"):
            grouping.append("event:" + str(row["eventID"]))
        if row.get("recordedBy") and row.get("recordNumber") and row.get("eventDate"):
            grouping.append("gathering:" + fingerprint([row.get(field, "") for field in ("recordedBy", "recordNumber", "eventDate")]))
        for group in grouping:
            if group in keys:
                parent[root(index)] = root(keys[group])
            keys[group] = index
    members = {}
    for index, (row, _) in enumerate(retained):
        members.setdefault(root(index), []).append(identity(row))
    group_names = {key: fingerprint(sorted(values)) for key, values in members.items()}
    truths, masked = [], {"tuning": [], "evaluation": []}
    for index, (row, point) in enumerate(retained):
        group = group_names[root(index)]
        fraction = int(fingerprint([seed, group])[:12], 16) / float(16 ** 12)
        split = "evaluation" if fraction < evaluation_fraction else "tuning"
        record = {"occurrenceID": identity(row), "catalogNumber": row.get("catalogNumber") or identity(row)}
        record.update({field: row.get(field, "") for field in LOCALITY_FIELDS})
        masked[split].append(record)
        truths.append({"recordID": identity(row), "catalogNumber": record["catalogNumber"], "group": group, "split": split,
            "latitude": f"{point.latitude:.9f}", "longitude": f"{point.longitude:.9f}",
            "referenceUncertaintyMeters": positive_number(row.get("coordinateUncertaintyInMeters")) or "",
            "referenceCoordinateOperation": point.operation})
    output.mkdir(parents=True, exist_ok=True)
    for split, records in masked.items():
        write_table(output / split / "dwc.tsv", records, ["occurrenceID", "catalogNumber", *LOCALITY_FIELDS], "\t")
    write_table(output / "ground_truth.tsv", truths, ["recordID", "catalogNumber", "group", "split", "latitude", "longitude", "referenceUncertaintyMeters", "referenceCoordinateOperation"], "\t")
    write_table(output / "excluded.tsv", excluded, ["recordID", "reason"], "\t")
    manifest = {"protocol": "xie-inspired-evaluation-v1", "inputSha256": file_hash(input_path), "seed": seed,
        "evaluationFraction": evaluation_fraction, "retained": len(truths), "excluded": len(excluded),
        "groups": len(members), "splitCounts": {key: len(value) for key, value in masked.items()},
        "warning": "Ground truth is separate. Never place it in an LLM input directory. No accuracy has been measured by prepare."}
    with atomic_text(output / "manifest.json") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate(truth_path, predictions, *, split="evaluation", run_logs=None):
    truths, _ = read_table(truth_path)
    truths = [row for row in truths if split == "all" or row["split"] == split]
    if not truths:
        raise ValueError("No reference records in requested split")
    metrics = {"protocol": "xie-inspired-evaluation-v1", "split": split, "truthSha256": file_hash(truth_path), "methods": {}}
    for name, path in predictions.items():
        candidates, _ = read_table(path)
        selected, catalog_counts = {}, {}
        for row in candidates:
            if row.get("selected", "").lower() != "true":
                continue
            key = identity(row)
            if not key or key in selected:
                raise ValueError(f"{name}: duplicate or missing selected identity")
            selected[key] = row
            catalog = row.get("catalogNumber", "")
            catalog_counts[catalog] = catalog_counts.get(catalog, 0) + 1
        errors, coverage, details = [], [], []
        for truth in truths:
            row = selected.get(truth["recordID"])
            if row is None and catalog_counts.get(truth["catalogNumber"]) == 1:
                row = next(item for item in selected.values() if item.get("catalogNumber") == truth["catalogNumber"])
            lat = parse_decimal((row or {}).get("candidateLatitude"))
            lon = parse_decimal((row or {}).get("candidateLongitude"))
            if lat is None or lon is None or not valid_lat_lon(lat, lon) or row.get("candidateGeodeticDatum") != "WGS84":
                details.append({"recordID": truth["recordID"], "status": "failed_or_unresolved"})
                continue
            error = haversine_km(float(truth["latitude"]), float(truth["longitude"]), lat, lon)
            errors.append(error)
            radius = positive_number(row.get("candidateUncertaintyMeters"))
            covered = None
            if radius and row.get("uncertaintyBasis") in DEFENSIBLE_UNCERTAINTY:
                # Reference positional error is reported separately, not silently added.
                covered = error * 1000 <= radius
                coverage.append(covered)
            details.append({"recordID": truth["recordID"], "status": "predicted", "errorKm": error,
                "coveredByReportedRadius": covered, "referenceUncertaintyMeters": truth.get("referenceUncertaintyMeters", "")})
        denominator = len(truths)
        kept = max(1, math.floor(len(errors) * 0.95))
        usage = {"input_tokens": 0, "output_tokens": 0, "reportedSuccessfulFreshCalls": 0, "reportedCallElapsedSeconds": 0.0}
        if (run_logs or {}).get(name):
            ids = {row["recordID"] for row in truths}
            for line in run_logs[name].read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                if (entry.get("occurrenceID") or entry.get("catalogNumber")) not in ids or entry.get("provenance", {}).get("checkpointReused"):
                    continue
                for stage in entry.get("llmUsage", []):
                    if stage.get("cacheHit"):
                        continue
                    tokens = stage.get("usage", {})
                    for field in ("input_tokens", "output_tokens"):
                        usage[field] += tokens.get(field, 0)
                    usage["reportedSuccessfulFreshCalls"] += int(bool(tokens))
                    usage["reportedCallElapsedSeconds"] += stage.get("request", {}).get("elapsedSeconds", 0)
        metrics["methods"][name] = {
            "predictionSha256": file_hash(path), "referenceCount": denominator, "predicted": len(errors),
            "failureRate": 1 - len(errors) / denominator,
            "medianKm": statistics.median(errors) if errors else None,
            "meanKm": statistics.mean(errors) if errors else None,
            "p90Km": percentile(errors, 0.9), "p95Km": percentile(errors, 0.95), "maxKm": max(errors) if errors else None,
            "withinKmFractionAllReferences": {str(km): sum(value <= km for value in errors) / denominator for km in (1, 5, 10)},
            "over100KmFractionAllReferences": sum(value > 100 for value in errors) / denominator,
            "defensibleUncertaintyCount": len(coverage), "uncertaintyCoverage": sum(coverage) / len(coverage) if coverage else None,
            "secondaryTrimmedTop5PercentMedianKm": statistics.median(sorted(errors)[:kept]) if errors else None,
            "tokenAndTime": usage, "records": details,
        }
    return metrics


def named_paths(values):
    result = {}
    for value in values or []:
        name, separator, path = value.partition("=")
        if not separator or not name or name in result:
            raise ValueError("Use unique NAME=PATH arguments")
        result[name] = Path(path).expanduser().resolve()
    return result


def main():
    parser = argparse.ArgumentParser(description="Prepare leakage-controlled georeference benchmarks and evaluate predictions without LLM calls.")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--input", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--seed", default="vasculum-v1")
    prep.add_argument("--evaluation-fraction", type=float, default=0.8)
    assess = sub.add_parser("evaluate")
    assess.add_argument("--truth", type=Path, required=True)
    assess.add_argument("--prediction", action="append", required=True, help="NAME=georeference_candidates.tsv")
    assess.add_argument("--run-log", action="append", help="NAME=georeference.log.jsonl (optional usage accounting)")
    assess.add_argument("--split", choices=("tuning", "evaluation", "all"), default="evaluation")
    assess.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.input, args.output, seed=args.seed, evaluation_fraction=args.evaluation_fraction)
    else:
        result = evaluate(args.truth, named_paths(args.prediction), split=args.split, run_logs=named_paths(args.run_log))
        with atomic_text(args.output) as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != "methods"}, ensure_ascii=False))
    return 0
