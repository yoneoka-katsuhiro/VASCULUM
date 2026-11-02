# VASCULUM

Version: `v0.1.15`

[Release notes](RELEASE_NOTES_v0.1.15.md) describe the georeferencing, personal-input
and human specimen-review updates in this release.
See the [validation record](VALIDATION_v0.1.15.md) for checks performed and
scientific claims that have not been validated.

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
| `llm_georeference_curator/` | Preserve documented coordinates or perform LLM-assisted georeferencing from linked specimen photographs, with auditable evidence and coordinate candidates. |
| `checklist/` | Build species lists from geographic conditions and generate visual checklist PDFs from herbarium specimen records and images. |

## Setup

Use the v0.1.15 ZIP distribution archive. It expands to a clean
`VASCULUM/` directory. Once published, release assets are available from GitHub Releases.

```bash
cd VASCULUM
bash setup_mac.sh
bash check_release.sh
```

Install [R](https://cran.r-project.org/) before setup. The setup script prepares
an isolated Python environment for each pipeline and the curator's R plotting packages. The
release check runs syntax checks, offline tests, CLI smoke tests, and a basic
secret scan without sending specimen data to external services.

## Reusing Previous Outputs

Collector outputs from recent releases can be reused after updating VASCULUM.
Place each taxon directory under
`herbarium_specimen_collector/output/<taxon_name>/`, not at repository root.
When the collector is rerun on the same output directory, valid existing JPEGs
are checked and reused instead of being downloaded again. See
`herbarium_specimen_collector/README.md` for the full migration notes.

## Current Scope

The collector pipeline searches by scientific name, merges duplicate portal
records that represent the same physical specimen, downloads linked specimen
images, and writes DwC-oriented exports.

The LLM-assisted georeference curator reads those collector exports, preserves
reliable original coordinates, adds reviewable coordinate candidates, excludes
records with insufficient locality evidence, and writes modified DwC exports
for downstream analysis.

### Visual Review Of Specimen Identity

A strength of VASCULUM is that occurrence metadata remains connected to actual
specimen photographs. Researchers can inspect the material and identification
labels themselves instead of accepting every downloaded record as correctly
identified. This supports human taxonomic review before georeferencing or SDM.
VASCULUM does not claim that an LLM automatically verifies plant identity.

After inspecting an **input photograph**, mark a misidentified specimen by
adding `miss_` to the beginning of its filename:

```text
MY0001__front.jpg  ->  miss_MY0001__front.jpg
```

The next curator run excludes the entire linked specimen, including its DwC
row and all photo views, without an LLM call. `miss_` is case-insensitive.
Input photographs are kept for audit/undo; exclusion reasons and identifiers
remain in summary, candidate TSV and log. A collector rerun also respects
marked download filenames instead of silently downloading that specimen again.

Use curator `--export-images` to build a final `images/` folder containing only
included specimens alongside the modified DwC. No marked image or photo of a
marked specimen is copied there. Reruns synchronize previously managed copies,
including removal of an earlier copy after you mark its input photograph.
See [manual exclusion instructions](llm_georeference_curator/README.md#remove-misidentifications-visually).

The CheckList pipeline reverse-searches species from a country or
coordinate-radius condition, selects representative herbarium specimen images,
and renders a visual checklist PDF with provenance tables for review.

## CheckList Example Gallery

Example output from a southern Philippines fern test run. The run covered 603
accepted species and completed in 4 h 14 min on a household Mac mini.

Scroll horizontally to browse the example pages. Click a card to open the full
page image.

<table>
  <tr>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_cover.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_cover_thumb.png" width="240" alt="Cover page for the southern Philippines fern CheckList"></a></td>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_search_maps.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_search_maps_thumb.png" width="240" alt="Search-area maps for the southern Philippines fern CheckList"></a></td>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_species_counts.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_species_counts_thumb.png" width="240" alt="Taxonomic species counts in the southern Philippines fern CheckList"></a></td>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_hymenophyllaceae.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_hymenophyllaceae_thumb.png" width="240" alt="Hymenophyllaceae page from the southern Philippines fern CheckList"></a></td>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_lindsaeaceae.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_lindsaeaceae_thumb.png" width="240" alt="Lindsaeaceae page from the southern Philippines fern CheckList"></a></td>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_thelypteridaceae.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_thelypteridaceae_thumb.png" width="240" alt="Thelypteridaceae page from the southern Philippines fern CheckList"></a></td>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_athyriaceae.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_athyriaceae_thumb.png" width="240" alt="Athyriaceae page from the southern Philippines fern CheckList"></a></td>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_polypodiaceae.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_polypodiaceae_thumb.png" width="240" alt="Polypodiaceae page from the southern Philippines fern CheckList"></a></td>
    <td width="240" align="center" valign="top"><a href=".github/assets/checklist-gallery/philippines_fern_checklist_index.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_index_thumb.png" width="240" alt="Scientific-name index from the southern Philippines fern CheckList"></a></td>
  </tr>
</table>

## SpecimenCollector Workflow

Use `herbarium_specimen_collector/` to retrieve Darwin Core-oriented specimen
records and linked herbarium specimen images.

```bash
cd herbarium_specimen_collector

bash run_collect_specimens.sh --dry-run \
  --taxon "Haplopteris mediosora"

bash run_collect_specimens.sh --skip-images --limit 10 \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"
```

Once the trial succeeds, drop `--skip-images` and `--limit` for a full run.
The collector writes `dwc.csv`, `dwc.tsv`, `summary.txt`, and linked files
under `images/`.

See `herbarium_specimen_collector/README.md` for source selection, image
resolution, duplicate handling, and output reuse.

## Georeference Workflow

Use `llm_georeference_curator/` to review and improve coordinates from an
existing collector output directory.

```bash
cd llm_georeference_curator

bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora \
  --habitat "subalpine forest" \
  --limit 10 \
  --llm-mode on
```

The curator reads `dwc.csv` or `dwc.tsv` and local specimen images, then writes
`modified_dwc.csv`, `modified_dwc.tsv`, `georeference_candidates.tsv`,
`summary.txt`, `georeference.log.jsonl`, and `georeference_flow.pdf`.
The ribbon PDF uses one R/ggalluvial renderer, fixed source grays and 50% opacity.
Install R before running setup; setup prepares the required R packages.

See `llm_georeference_curator/README.md` for LLM provider setup, habitat
advisory warnings, caching, parallel processing, and review notes. Species-wide
habitat expectations flag questionable points after selection; they do not
move coordinates, rank candidates, or exclude records.

### Include Personal Specimens

Before starting curation, put your own optional `dwc.tsv`/`dwc.csv` and specimen
photographs under the collector output's `personal/` directory:

```text
herbarium_specimen_collector/output/<run>/personal/dwc.tsv
herbarium_specimen_collector/output/<run>/personal/images/MY0001__front.jpg
herbarium_specimen_collector/output/<run>/personal/images/MY0001__label.jpg
```

These are included automatically. Photo-only groups become personal records;
DwC-only records are retained only when existing coordinates are trusted.
New coordinate estimates require a linked local photograph. Both whole images in a
named group are passed to label reading. External collections can be added
with repeatable curator `--additional-input PATH`. Personal input provenance
and image hashes are retained alongside portal-derived records.

Editing the main DwC also works, but choose `--input-dwc PATH` if generated
TSV and CSV now differ. Prefer a separate personal input because the collector
can regenerate its main tables. Use wrapper `--pause-before-curation` to wait
for manual additions between collection and curation. See the
[personal-input instructions](llm_georeference_curator/README.md#add-your-own-specimens-before-curation)
for identifiers, image grouping, duplicate checks and examples.

### Research Reproducibility

The georeferencing core is inspired by
[Xie et al. (2025)](https://doi.org/10.1038/s41477-025-02162-y), while image
transcription, geographic research and advisory ecological checks are explicit
VASCULUM extensions. This is not an exact replication of the original experiment.
There is one input policy: keep trusted original coordinates; otherwise require
and read the source photograph. Unreadable/broad labels do not fall back to
DwC locality text. No-photo records with untrusted/missing coordinates are excluded.

Reproducibility and later auditing are design requirements: preserve the input
DwC/images, six primary outputs and private response/snapshot caches. Logs
record before/after coordinates, actual models, complete prompts, timestamps,
code/input/image hashes, sources and stage errors. Completed-record checkpoints
allow cached replay. Fresh LLM calls and changing web sources are not guaranteed
to reproduce identical answers. See the
[photo-first methodology](llm_georeference_curator/docs/methodology.md).

Six-decimal coordinates do not imply sub-meter accuracy. The study's reported
error statistic is not an individual 10 km guarantee, and this extension has
not yet demonstrated improved positional accuracy on an independent benchmark.
The final DwC always exports accepted records only, without a mode switch;
model-only estimates remain review proposals until their locality support, datum and uncertainty are justified.

The collector and curator can be connected with `run_collect_and_georeference.sh`
when a single combined command is useful, but the two workflows are usually
easier to understand and check independently.

## CheckList Workflow

Use `checklist/` to build a species list from a geographic condition and render
a visual checklist PDF from herbarium specimen records and images.

First validate the configuration:

```bash
cd checklist
bash run_checklist.sh --dry-run \
  --country "Japan" \
  --taxon "Ferns"
```

### A. Administrative-Area Search

Use administrative fields such as `--country`, `--state`, or `--prefecture`
when the target area is a named region:

```bash
bash run_checklist.sh \
  --country "Japan" \
  --taxon "Aspleniaceae" \
  --title "Japan Aspleniaceae CheckList"
```

### B. Coordinate-Radius Search

Use latitude, longitude, and radius when the target area is a small field site
or locality. This is the recommended first real run:

```bash
bash run_checklist.sh \
  --country "Japan" \
  --latitude 35.303622 \
  --longitude 139.606166 \
  --radius 0.5 \
  --taxon "Ferns" \
  --title "Jimmuji Fern CheckList"
```

Very broad combinations, such as `--taxon "Angiosperms"` with a country-wide
search, can produce many species and substantially increase retrieval, image
processing, and LLM evaluation time. For routine work, use `--taxon` with a
family, genus, species, or small coordinate-radius search.

See `checklist/README.md` for taxon-scope examples, representative image
selection, output files, and citation guidance.

## Shared Files

`LICENSE` applies to the source code in this repository. `CITATIONS.md` lists
the public data services used by the configured adapters.

## Publication Notes

Local environments, API-key files, generated outputs, caches, macOS metadata,
and ZIP archives are excluded by `.gitignore`. Run `bash check_release.sh`
before staging a public release. The setup process does not initialize a Git
repository or publish any files.
