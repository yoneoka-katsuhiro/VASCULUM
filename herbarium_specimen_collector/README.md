# Herbarium Specimen Collector

Herbarium Specimen Collector searches public specimen databases by scientific
name, merges duplicate portal records that represent the same physical
specimen, and downloads linked specimen images at a practical research
resolution.

Version: `v0.1.3`

## Requirements

- macOS or Linux
- Python 3.9 or newer
- A contact email for polite HTTP identification

## Setup

After cloning or downloading `VASCULUM`, enter this pipeline directory. If you
download the GitHub ZIP archive instead of using `git clone`, the folder may be
named `VASCULUM-main`.

```bash
cd VASCULUM/herbarium_specimen_collector
bash setup_mac.sh
```

The setup script creates `.venv` and installs the packages listed in
`requirements.txt`. It does not upgrade or replace the system Python or pip.

## Recommended First Run

Validate the configuration before making network requests or writing output
files:

```bash
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --dry-run \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"
```

For the first real data run, the low image-resolution profile is a practical
starting point:

```bash
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora" \
  --image-resolution low
```

By default, results are written under `output/<taxon_name>/`.

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
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --dry-run \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"

# Metadata only
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --skip-images \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora" \
  --output output/Haplopteris_mediosora_metadata

# Smaller image files
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --image-resolution low \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"

# Selected sources and a small trial limit
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --sources gbif,tns,kag,taif \
  --limit 10 \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora" \
  --image-resolution low \
  --output output/Haplopteris_mediosora_trial

# Redirect terminal output separately from the diagnostic log
bash run_collect_specimens.sh \
  --contact-email "your.email@example.com" \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora" \
  --image-resolution low \
  > result_paths.txt 2> run.log
```

Run `bash run_collect_specimens.sh --help` for all options.

Offline verification after setup:

```bash
.venv/bin/python tests/test_offline.py
.venv/bin/python tests/test_resume.py
```

## Output

Each taxon is written to `output/<taxon_name>/` by default:

```text
dwc.csv
dwc.tsv
images/
logs/
summary.txt
```

`logs/run_<timestamp>.log` records structured diagnostic events, source and
query checkpoints, HTTP retries, errors, and output completion. The file
contains one JSON event per line after its timestamp and severity, making it
suitable for sharing with Codex when troubleshooting. Contact email values are
not logged, and credential-like URL parameters are redacted.

`dwc.csv` and `dwc.tsv` contain the same Darwin Core-oriented rows with
different delimiters. `summary.txt` contains counts, per-source status,
retries, errors, and the diagnostic log path. No raw response cache, working
manifest, duplicate report, or Appendix table is retained after completion.

Use `--output PATH` to choose another output directory.

## Resume An Interrupted Run

During collection, progress and temporary response caches are stored under
`output/<taxon_name>/.resume/`. If the process is interrupted, repeat the same
command with `--resume`:

```bash
bash run_collect_specimens.sh --resume \
  --contact-email "your.email@example.com" \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora"
```

The taxon, synonyms, sources, record limit, image profile, and GBIF filters
must match the interrupted command. The program resumes at the next unfinished
name/source checkpoint. If interruption occurred inside a long query, cached
responses are reused so completed requests are not repeated. Existing
downloaded images are also validated and reused.

Use `--restart` with the intended command to discard an incompatible or
unwanted checkpoint. The `.resume/` directory is removed automatically after
the output files are written successfully. Diagnostic logs are retained.

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
The `images/` directory is managed by the program. After a fully successful
run, JPEG files not referenced by the current DwC rows are removed. Files are
not pruned after a partial or failed run.

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

PteridoPortal responses are shared across its global and institution-filtered
searches during one run. Records that do not match an institution filter are
rejected from API metadata before their HTML pages are requested. Requests to
the PteridoPortal host are spaced by a transparent per-host interval configured
under `network.host_request_intervals`. Permanent HTTP errors such as 404 are
not retried; rate limits and transient server errors still use bounded retries.
Image requests use the source delay as a per-host interval, avoiding an
additional fixed wait after every completed image.

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

The program deliberately uses per-host request intervals, bounded retries, and
a contact email. It does not randomize requests to conceal automation. Do not
use it to bypass login, CAPTCHA, access controls, or institutional terms.

The source code is released under the MIT License. Downloaded data and images
are not covered by the software license.
