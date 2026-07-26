# VASCULUM

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

## First Run

After cloning or downloading this repository, enter the pipeline directory and
run the setup script once. If you download the GitHub ZIP archive instead of
using `git clone`, the folder may be named `VASCULUM-main`.

```bash
cd VASCULUM/herbarium_specimen_collector
bash setup_mac.sh
```

Then run a small validation command:

```bash
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --dry-run \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"
```

For a practical first data run, use the standard image-resolution profile:

```bash
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora" \
  --image-resolution standard
```

## Current Scope

The current pipeline searches by scientific name, merges duplicate portal
records that represent the same physical specimen, downloads linked specimen
images, writes DwC-oriented exports, records diagnostic logs, and can resume
interrupted runs. By default, each run is written to a dated output directory.

Planned curation extensions include specimen-label transcription and automated
validation, correction, and enrichment of specimen metadata.

## Shared Files

`LICENSE` applies to the source code in this repository. `CITATIONS.md` lists
the public data services used by the configured adapters.
