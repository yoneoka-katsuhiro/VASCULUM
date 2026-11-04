# VASCULUM manual

[日本語](MANUAL_JA.md) · [Project overview](../README.md)

This manual covers VASCULUM 0.1.19 and CheckList 0.1.3. Commands use small examples; replace the contact email and output directory with your own values.

## Installation

### Standalone macOS and Windows downloads

1. Open [Releases](https://github.com/yoneoka-katsuhiro/VASCULUM/releases/latest).
2. Select `macOS-arm64.zip` for an Apple silicon Mac, or `Windows-x64.zip` for 64-bit Intel/AMD Windows. The release name includes the version. Download the matching `.zip.sha256` file if you want to verify integrity.
3. Compare the archive's SHA-256 with the value in that file: `shasum -a 256 VASCULUM-v0.1.19-macOS-arm64.zip` on macOS; `Get-FileHash .\VASCULUM-v0.1.19-Windows-x64.zip -Algorithm SHA256` in PowerShell.
4. Extract the entire archive into a writable directory. Open Terminal or PowerShell in the extracted folder.
5. Run `./vasculum-collect --help` on macOS or `.\vasculum-collect.exe --help` on Windows. These are terminal programs, so double-clicking them is not the normal workflow.

The programs bundle Python and Python dependencies. Apple notarization and Windows publisher signing are not included. If your operating system blocks the download, first verify its source and checksum; use the system's per-application approval interface or run from source. Do not disable system-wide security settings. This release targets Apple silicon and Windows x64; Intel Mac users can run from source.

### Run from source

Python **3.11 or newer** is required. For macOS, the existing `bash setup_mac.sh` creates pipeline-specific environments. For Windows with Python 3.13, run `powershell -File .\setup_windows.ps1` from the repository root. A manual installation works on either system:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r herbarium_specimen_collector/requirements.txt
```

On Windows, use `py -3.13 -m venv .venv` and `.venv\Scripts\python.exe` instead. Run the scripts with this interpreter:

```bash
.venv/bin/python herbarium_specimen_collector/scripts/collect_specimens.py --help
.venv/bin/python checklist/scripts/checklist.py --help
```

In the examples below, replace `./vasculum-collect` or `./vasculum-checklist` with the appropriate source invocation. In PowerShell, the standalone names are `.\vasculum-collect.exe` and `.\vasculum-checklist.exe`.

## Collect a species

First validate locally:

```bash
./vasculum-collect --dry-run --taxon "Haplopteris mediosora" --sources gbif --contact-email "you@example.org"
```

Then try at most ten GBIF records without images:

```bash
./vasculum-collect --taxon "Haplopteris mediosora" --sources gbif --limit 10 --skip-images --contact-email "you@example.org" --output "results/mediosora-trial"
```

For a larger collection, remove `--limit`, remove `--skip-images`, add synonyms and choose a new destination:

```bash
./vasculum-collect --taxon "Haplopteris mediosora" --synonym "Vittaria mediosora" --sources gbif,cvh --contact-email "you@example.org" --output "results/mediosora-images"
```

`--limit` applies **per source and search name**, so adding synonyms can increase the total above that limit. Omitting `--sources` queries all configured sources and can take much longer. `--image-resolution low` uses a maximum image edge of 1600 pixels; the default `standard` uses 2400 pixels. An image that is unavailable, too small, or unsuitable for decoding can be rejected; inspect the download status and summary.

By default, GBIF collection requests preserved specimens. `--gbif-occurrence-mode observations`, `specimens-and-observations`, or `all-images` broadens the record types; these modes are not equivalent to a herbarium-only dataset. `--gbif-coordinate-filter with-coordinates` applies GBIF's coordinate/issue filters, which do not establish coordinate accuracy.

### Outputs and reruns

| Output | Meaning |
| --- | --- |
| `dwc.csv`, `dwc.tsv` | Merged records, provenance, coordinates, licences and image status |
| `images/` | Downloaded or reused JPEGs |
| `summary.txt` | Sources, totals, warnings/errors and provenance checksums |

Choose a new `--output` directory for each research run. Reusing a directory updates its tables; it is not a versioned archive. Existing photos are kept by default, including photos not referenced by the newest table. A complete failure preserves existing tables. Partial results use `dwc.partial.csv`, `dwc.partial.tsv` and `summary.partial.txt`, return exit code `3`, and preserve the previous complete files. A later successful rerun still updates the normal files, so keep a backup when you intentionally reuse a directory.

The optional `--prune-unreferenced-images` deletes unreferenced JPEGs only after a run without reported errors. Back up first. The compatibility option `--keep-unreferenced-images` explicitly selects the default preservation behavior. Files beginning with `miss_` are never pruned.

### Review specimen identifications

Open the specimen image and its source record. If you judge the specimen misidentified, rename its downloaded photo by adding `miss_` to the existing basename. On the next collector run, the matching photo is not downloaded again and the record is excluded from the exported DwC table. The marked photo remains available for audit or reversal. Remove the prefix to undo that decision. Keep the rest of the filename unchanged.

This marker method depends on stable generated filenames. If catalogue metadata or image naming changes upstream, compare the source identifiers before relying on a previous marker. The roadmap proposes a durable review ledger.

## Create a geographic checklist

Validate a small search first:

```bash
./vasculum-checklist --dry-run --country Japan --latitude 35.303622 --longitude 139.606166 --radius 0.5 --taxon Ferns --contact-email "you@example.org"
```

To run it, remove `--dry-run` and add `--output "results/jimmuji-ferns"`. A logged-in Codex CLI must be available. Set `VASCULUM_CODEX_COMMAND` to its executable path if it is not on PATH. Windows npm installations are resolved to their native `codex.exe`; if detection fails, set that path explicitly. No AI request is made by `--dry-run` or the release tests.

After retrieving species, the program reports the initial candidate pool size and asks whether to continue. Read this estimate before confirming: image evaluation can involve many model calls. The estimate is a count of candidate images, not a guaranteed cost or runtime. Set `CODEX_MODEL` only if you want a specific model available to your account; otherwise the CLI chooses its configured default.

A regional search must include `gbif` in `--sources`. Adding `cvh` supplements image candidates. The program can select representative images from outside the search region; it keeps their locality and source information separately from regional occurrence evidence. Review both before publication.

`--limit` caps GBIF records scanned, not species. Hitting the cap produces a partial result with exit code `3`. `--max-pdf-images 10` limits images downloaded for the final PDF; it does not cap the preceding regional record search. Broad country-level or large-group requests can be expensive and incomplete, so start small.

Latitude, longitude and radius must be supplied together. Radius is in kilometres and must be finite and positive. Date-line or polar-crossing circles are rejected to avoid incorrect polygon interpretation; use smaller separate searches. Administrative filters use supplied place text and can miss alternative spellings.

### Checklist outputs

- `species_list.csv`, `.tsv`, `.md`: species names, supporting counts, representative image metadata and PDF page references.
- `records.csv`: regional evidence records with identifiers and source links.
- `checklist.pdf`: cover, search area, family counts, species pages, index and provenance.
- `summary.txt`: command, scope, source outcomes and warnings/errors.

The PDF uses system fonts. For missing glyphs or a required publication font, set `VASCULUM_FONT_PATH` to a suitable installed `.ttf` or `.ttc` file before running. The renderer checks macOS fonts, Windows Yu Gothic/Meiryo/Arial, and Linux Noto CJK/DejaVu. Ensure the font covers your languages.

## Reproducibility and interpretation

Save the exact command, output directory, summary, software version and relevant input configuration. Standalone releases include `BUILD_INFO.json` with their platform, Python packages and source commit. Portal contents and image URLs change; the same search is not guaranteed to return the same records later. Raw portal payloads are currently temporary and are not retained as a replayable archive.

Neither the database's accepted name nor the AI's image preference proves an identification. No independent taxonomic, geographic or complete-flora validation was performed by this maintenance release. Cite the original institutions and datasets, and inspect image-specific licences before sharing.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Exit `0` | Operation completed, or a dry-run validated. Review scientific suitability separately. |
| Exit `1` | Read the error; fix configuration, network, no-results or Codex availability issues. |
| Exit `2` | Correct the command-line arguments. |
| Exit `3` | Inspect `summary.partial.txt` for the collector, or `summary.txt` for the checklist; one or more sources, images or scan limits were incomplete. |
| Exit `130` | The run was interrupted. Use a new output directory if retaining an audit trail. |
| Missing email | Supply `--contact-email`, set `CONTACT_EMAIL`, or copy `.env.example` and set it locally. |
| Codex option error | Update the CLI. Required features are checked; edition-specific optional flags are only sent when supported. |
| No photos for some records | Check image URLs, rights, size limits and download errors. Do not treat this as biological absence. |
| Windows path with spaces | Quote the entire path with double quotes. Run an executable path via `& "C:\path with spaces\vasculum-collect.exe"`. |

For standalone programs, `.env` is read from your current working directory, and the default `output/` is created there. Source commands use the corresponding pipeline directory. Supplying `--output` and `--contact-email` makes this distinction explicit.
