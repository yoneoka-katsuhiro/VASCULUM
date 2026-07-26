# VASCULUM

Version: `v0.1.5`

Repository: <https://github.com/yoneoka-katsuhiro/VASCULUM>

VASCULUM is a collection of pipelines for retrieving, curating, and organizing
digital herbarium specimen records and images.

The name refers to a vasculum, a portable botanical collecting case used to hold
plant specimens during fieldwork. This repository is intended to function as a
virtual vasculum for gathering, organizing, and holding digital herbarium
specimen data from multiple archives.

VASCULUM can also be read as:

```text
Voucher Archive Search and Curation for Unified Large-scale Use of Metadata
```

## Pipelines

| Directory | Purpose |
| --- | --- |
| `herbarium_specimen_collector/` | Retrieve and integrate Darwin Core (DwC)-oriented herbarium specimen datasets and associated specimen images from multiple digital archives. |
| `llm_georeference_curator/` | Perform LLM-assisted georeferencing from collector outputs, specimen-label evidence, and detailed locality strings while preserving reviewable coordinate candidates. |

## Setup

```bash
cd /path/to/VASCULUM-main
bash setup_mac.sh
bash check_release.sh
```

The setup script prepares an isolated Python environment for each pipeline. The
release check runs syntax checks, offline tests, CLI smoke tests, and a basic
secret scan without sending specimen data to external services.

## Current Scope

The collector pipeline searches by scientific name, merges duplicate portal
records that represent the same physical specimen, downloads linked specimen
images, and writes DwC-oriented exports.

The LLM-assisted georeference curator reads those collector exports, preserves
reliable original coordinates, adds reviewable coordinate candidates, excludes
records with insufficient locality evidence, and writes modified DwC exports
for downstream analysis.

## Combined Workflow

Pipelines can be run independently, or connected with the convenience runner:

```bash
bash run_collect_and_georeference.sh \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora" \
  --image-resolution standard \
  -- \
  --prompt-profile xie-modified \
  --habitat "subalpine forest" \
  --use-hydrology \
  --use-dem
```

Arguments before `--` are passed to `herbarium_specimen_collector`. Arguments
after `--` are passed to `llm_georeference_curator`.

The collector writes `dwc.csv`, `dwc.tsv`, and linked files under `images/`.
The combined runner detects that output directory and passes it directly to the
curator. The curator reads local image paths from DwC `associatedMedia`, runs
the selected LLM when coordinates need research, and returns
`modified_dwc.csv`, `modified_dwc.tsv`, `georeference_candidates.tsv`,
`summary.txt`, and `georeference.log.jsonl`.

## Shared Files

`LICENSE` applies to the source code in this repository. `CITATIONS.md` lists
the public data services used by the configured adapters.

## Publication Notes

Local environments, API-key files, generated outputs, caches, macOS metadata,
and ZIP archives are excluded by `.gitignore`. Run `bash check_release.sh`
before staging a public release. The setup process does not initialize a Git
repository or publish any files.
