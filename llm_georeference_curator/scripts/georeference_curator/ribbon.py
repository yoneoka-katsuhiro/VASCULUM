from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


SOURCES = ("GBIF", "CVH", "Other")
SCRIPT = Path(__file__).resolve().parents[1] / "plot_georeference_flow.R"


def source_group(row):
    value = row.get("vasculumPrimarySource", "").strip()
    if not value:
        values = {part.strip().lower() for part in row.get("datasetName", "").split(";") if part.strip()}
        groups = {item.upper() if item in {"gbif", "cvh"} else "Other" for item in values}
        if len(groups) > 1:
            raise ValueError("Mixed legacy datasetName needs vasculumPrimarySource; no source attribution was guessed.")
        return next(iter(groups), "Other")
    return value.upper() if value.lower() in {"gbif", "cvh"} else "Other"


def collector_counts(source_dir, dwc_path, input_counts):
    summary = source_dir / "summary.txt"
    if not dwc_path or not summary.is_file():
        return None
    lines = [line[len("Flow provenance: "):] for line in summary.read_text(encoding="utf-8").splitlines()
             if line.startswith("Flow provenance: ")]
    if len(lines) != 1:
        return None
    try:
        data = json.loads(lines[0])
        if data.get("version") != 1 or data.get("dwcSha256") != hashlib.sha256(dwc_path.read_bytes()).hexdigest():
            return None
        tables = []
        for name in ("retrieved", "unique", "excluded"):
            raw = data[name]
            if not isinstance(raw, dict) or set(raw) - set(SOURCES):
                return None
            table = {source: raw.get(source, 0) for source in SOURCES}
            if any(type(n) is not int or n < 0 for n in table.values()):
                return None
            tables.append(table)
        retrieved, unique, excluded = tables
        for source in SOURCES:
            if not retrieved[source] >= unique[source] >= excluded[source]:
                return None
            if unique[source] - excluded[source] != input_counts[source]:
                return None
        return retrieved, unique
    except (ValueError, TypeError, KeyError):
        return None


def ribbon_counts(rows, results, source_dir, dwc_path, *, complete_input):
    if len(rows) != len(results):
        raise ValueError("Ribbon input/result counts differ")
    inputs, evidence, accepted = Counter(), Counter(), Counter()
    for row, result in zip(rows, results):
        source = source_group(row)
        inputs[source] += 1
        eligible = bool(result.provenance.get("sourceEvidenceEligible", False))
        if result.include_in_dwc and not eligible:
            raise ValueError("Accepted record lacks its source-evidence route")
        evidence[source] += int(eligible)
        accepted[source] += int(result.include_in_dwc)
    history = collector_counts(source_dir, dwc_path, inputs) if complete_input else None
    stages = [("collected", history[0]), ("unique", history[1])] if history else [("input", inputs)]
    stages.extend((("evidence", evidence), ("accepted", accepted)))
    counts = [{"source": source, **{name: table[source] for name, table in stages}} for source in SOURCES]
    for row in counts:
        values = [row[name] for name, _ in stages]
        if any(a < b for a, b in zip(values, values[1:])):
            raise ValueError("Ribbon counts are not conserved by source")
    return counts


def r_environment(project_dir):
    env = dict(os.environ)
    library = SCRIPT.parent.parent / ".Rlibrary"
    if library.is_dir():
        env["R_LIBS_USER"] = str(library) + (os.pathsep + env["R_LIBS_USER"] if env.get("R_LIBS_USER") else "")
    return env


def prepare_ribbon_renderer(project_dir):
    executable = shutil.which("Rscript", path=(os.environ.get("PATH") or os.defpath) +
                              os.pathsep + "/usr/local/bin" + os.pathsep + "/opt/homebrew/bin")
    if not executable:
        raise RuntimeError("Ribbon PDF requires Rscript. Install R, then run llm_georeference_curator/setup_mac.sh. No LLM requests were made.")
    check = subprocess.run([executable, "--vanilla", "-e",
        'quit(status=if(all(vapply(c("ggplot2","ggalluvial"),requireNamespace,logical(1),quietly=TRUE))) 0 else 1)'],
        env=r_environment(project_dir), capture_output=True, text=True, timeout=30, check=False)
    if check.returncode:
        raise RuntimeError("Ribbon PDF requires ggplot2 and ggalluvial. Run llm_georeference_curator/setup_mac.sh. No LLM requests were made.")
    return executable


def renderer_provenance(project_dir, executable):
    result = subprocess.run([executable, "--vanilla", "-e",
        'cat(as.character(getRversion()),as.character(packageVersion("ggplot2")),as.character(packageVersion("ggalluvial")),sep="\\t")'],
        env=r_environment(project_dir), capture_output=True, text=True, timeout=30, check=True)
    versions = result.stdout.strip().split("\t")
    if len(versions) != 3:
        raise RuntimeError("Unable to record R ribbon runtime versions; no LLM requests were made.")
    return dict(zip(("R", "ggplot2", "ggalluvial"), versions),
                rendererSha256=hashlib.sha256(SCRIPT.read_bytes()).hexdigest())


def write_ribbon_plot(project_dir, output_dir, counts, executable):
    output = output_dir / "georeference_flow.pdf"
    # Do not leave a previous run's plot beside newly written tables after failure.
    if output.exists():
        output.unlink()
    with tempfile.TemporaryDirectory(prefix=".ribbon_", dir=output_dir) as directory:
        directory = Path(directory)
        table = directory / "counts.tsv"
        with table.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(counts[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(counts)
        pdf = directory / "flow.pdf"
        result = subprocess.run([executable, "--vanilla", str(SCRIPT), str(table), str(pdf)],
            env=r_environment(project_dir), capture_output=True, text=True, timeout=120, check=False)
        if result.returncode or not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF"):
            raise RuntimeError("R ribbon rendering failed: " + (result.stderr.strip()[-1500:] or "no valid PDF"))
        os.replace(pdf, output)
    return output
