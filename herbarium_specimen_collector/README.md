# Herbarium Specimen Collector

Herbarium Specimen Collector searches public specimen databases by scientific
name, merges duplicate portal records that represent the same physical
specimen, and downloads linked specimen images at a practical research
resolution.

Version: `v0.1.10`

## Requirements

- macOS or Linux
- Python 3.9 or newer
- A contact email for polite HTTP identification

## Setup

```bash
cd herbarium_specimen_collector
bash setup_mac.sh
```

The setup script creates `.venv` and installs the packages listed in
`requirements.txt`. It does not upgrade or replace the system Python or pip.

## Recommended First Run

Confirm the configuration without touching the network first, then try a small
metadata-only collection before downloading images:

```bash
# 1. Validate arguments and configuration only (no network, no files)
bash run_collect_specimens.sh --dry-run --taxon "Haplopteris mediosora"

# 2. Small trial: a few records, metadata only
bash run_collect_specimens.sh --skip-images --limit 10 \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"
```

Once the trial succeeds, drop `--skip-images` and `--limit` for a full run.

## Run

```bash
bash run_collect_specimens.sh \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"
```

When `--taxon` is omitted in an interactive terminal, the program asks for the
scientific name and optional synonyms. It also asks for a contact email when
neither `--contact-email` nor `CONTACT_EMAIL` is set.

For repeat use, create `.env` from `.env.example` and set `CONTACT_EMAIL`.
Never commit `.env`.

Useful examples:

```bash
# Validate configuration without network requests or output files
bash run_collect_specimens.sh --dry-run \
  --taxon "Haplopteris mediosora"

# Metadata only
bash run_collect_specimens.sh --skip-images \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"

# Smaller image files
bash run_collect_specimens.sh --image-resolution low \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"

# Selected sources and a small trial limit
bash run_collect_specimens.sh \
  --sources gbif,tns,kag,taif \
  --limit 10 \
  --taxon "Haplopteris mediosora"

# Save terminal progress only when a log is needed
bash run_collect_specimens.sh \
  --taxon "Haplopteris mediosora" \
  > result_paths.txt 2> run.log
```

Run `bash run_collect_specimens.sh --help` for all options.

## Output

Each taxon is written to `output/<taxon_name>/` by default:

```text
dwc.csv
dwc.tsv
images/
summary.txt
```

No raw response cache, working manifest, duplicate report, Appendix table, or
automatic log file is retained. `dwc.csv` and `dwc.tsv` contain the same Darwin
Core-oriented rows with different delimiters. `summary.txt` contains counts,
per-source status, retries, and errors.

Use `--output PATH` to choose another output directory.

## Reusing Outputs From Earlier Releases

Collector outputs created with recent releases, including v0.1.7 and v0.1.8,
can be reused with v0.1.10. A reusable taxon directory should contain the DwC
table and its image folder:

```text
VASCULUM/
  herbarium_specimen_collector/
    output/
      Taxon_name/
        dwc.csv
        dwc.tsv
        summary.txt
        images/
```

`VASCULUM/output/` is not the standard collector output location. If an older
working copy has `output/` directly under `VASCULUM/`, move it before rerunning
the standard collector commands:

```bash
mv output herbarium_specimen_collector/output
```

When the same taxon is collected again into the same output directory, existing
JPEGs with the expected names are inspected first. Valid images are reported as
`already_downloaded` and are not fetched again. Only missing files, damaged
files, files below the configured minimum size, or files with unexpected names
need to be downloaded again.

By default, v0.1.8 and later remove JPEGs in `images/` that are not referenced
by the current DwC rows at the end of a run. Use `--keep-unreferenced-images`
when you intentionally want to keep extra images from an earlier dataset:

```bash
bash run_collect_specimens.sh \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora" \
  --keep-unreferenced-images
```

If you use a custom location, pass the taxon output directory explicitly:

```bash
bash run_collect_specimens.sh \
  --taxon "Haplopteris mediosora" \
  --output /path/to/output/Haplopteris_mediosora
```

To resume only the georeference curator, do not rerun the collector. Point the
curator directly to a directory that contains `dwc.csv` or `dwc.tsv` and
`images/`:

```bash
cd VASCULUM

bash llm_georeference_curator/run_llm_georeference_curator.sh \
  --input herbarium_specimen_collector/output/Haplopteris_mediosora \
  --robust \
  --habitat "subalpine forest" \
  --llm-mode on
```

## Image Names

Image names share their specimen code with the DwC `catalogNumber` field:

```text
TNSVS255550_Haplopteris_mediosora.jpg
TAIF2180_Haplopteris_mediosora_holotype.jpg
```

The code is formed from `institutionCode` and the source catalog number without
spaces or punctuation. If the source catalog number already begins with the
institution code, it is not repeated. The unmodified source value is retained
in `otherCatalogNumbers` when it differs.

Images are stored as JPEG. The default `--image-resolution standard` profile
uses a maximum side length of 2400 pixels and quality 88. The
`--image-resolution low` profile uses a maximum side length of 1600 pixels and
quality 84. The low profile is intended to reduce disk use while retaining
readable specimen-label text when the source image is sufficiently sharp.
Profile values are in `config/source_settings.json`.
The `images/` directory is managed by the program. JPEG files that the current
DwC rows no longer reference are removed at the end of every run (including
partial runs) so that the number of files in `images/` equals the reported
`Images downloaded` count. Pass `--keep-unreferenced-images` to preserve images
from earlier runs instead; those extra files are then reported as
`Unreferenced JPEGs` in `summary.txt` and on the final `Totals` line.

## Duplicate Handling

The collector distinguishes:

- The same physical specimen shown by multiple portals: one DwC row and one
  downloaded image.
- Different physical specimens distributed from the same collecting event:
  separate DwC rows with a shared Darwin Core `eventID`.

Physical identity uses normalized `institutionCode + catalogNumber` first,
then occurrence identifier or record/media URL when catalog data are absent.
Collector, collection number, date, and locality are used only to group
duplicate gatherings; they never cause specimen deletion.

## Sources

Configured source codes:

```text
gbif, pteridoportal, cvh, cnh, avh, reflora, kag, bm, b, us, ny, l,
mo, ucjeps, mich, f, e, flas, ti, tns, tai, taif, sing, bo
```

Direct public adapters are configured for GBIF, PteridoPortal, CVH, CNH, AVH,
Reflora, KAG, BM, B/JACQ, US/NMNH, L/Naturalis,
UC/JEPS/CCH2, E/RBGE, TI fern types, TNS WebMuseum, TAI, TAIF, SING/BRAHMS,
and BO/BRIN DwC-A. NY, MO, MICH, F, and FLAS use PteridoPortal records.

## Terminal And Exit Codes

Live progress is written to standard error. Final output paths are written to
standard output, so normal shell redirection works as expected. The live table
shows the number of selected sources, keeps one row per source across accepted
names and synonyms, and automatically removes columns or shortens text to fit
the current terminal width.

- `0`: completed without retrieval or image errors
- `1`: fatal configuration or runtime error
- `2`: invalid command-line usage
- `3`: output completed, but one or more source or image operations failed
- `130`: interrupted by the user

## Research And Licensing

Metadata and images remain subject to the licenses and terms supplied by their
source institutions. Review `license`, `rightsHolder`, `references`, and
`associatedMedia` before publication or redistribution. Cite the underlying
datasets and herbaria, not only this software. See `../CITATIONS.md`.

The program deliberately uses request delays, bounded retries, and a contact
email. Do not use it to bypass login, CAPTCHA, access controls, or institutional
terms.

The source code is released under the MIT License. Downloaded data and images
are not covered by the software license.
