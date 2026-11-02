# llm_georeference_curator v0.1.16

Automatically determine a best point-radius georeference from existing DwC
coordinates or the finest legible locality on a specimen photograph. The tool
makes an adoption decision; final scientific validation belongs to the user.

## Decisions

- **accepted:** a best coordinate and positive uncertainty radius are determined
  and written to final DwC. Doubt is expressed as confidence high/medium/low,
  assumptions and radius, not a holding state.
- **excluded:** country/admin-1-only label locality; no photograph and no usable
  coordinate; a miss_ exclusion mark; or an obvious geographic contradiction
  that cannot be repaired from the label.
- **failed:** a technical interruption, unavailable required provider, invalid
  required response, timeout or transformation failure. Failed work is retryable
  and is not a completed-record checkpoint.

There are no review/unresolved record states. A county, township, village,
mountain, river, pass or forest road is not rejected for being broad. A partial
reading is usable: unreadable suffixes are not invented and do not prevent a
best estimate from the legible place. A label stating only country/admin-1 is
excluded for insufficient locality. A wholly unreadable image has not supplied
the required reading: it is a retryable reading failure, not an extra exclusion
category or a hidden review queue. Successful partial transcriptions are cached.

All final records have georeferenceVerificationStatus=unverified. accepted means
machine-adopted, not independently proven true. The user can update verification
status after checking every record. confidence is qualitative, not a probability.

## Quick Start

Install [R](https://cran.r-project.org/) before setup. From the VASCULUM root:

```bash
bash setup_mac.sh
codex login
codex login status

bash llm_georeference_curator/run_llm_georeference_curator.sh \
  --input herbarium_specimen_collector/output/Haplopteris_mediosora \
  --habitat "subalpine forest"
```

Codex CLI uses the saved account login and does not require an API key. An
available provider is used in --llm-mode auto; --llm-mode on requires LLM work
when needed, while off uses existing coordinates/local supplied evidence only.
The default model selection prefers gpt-6.1-sol with medium reasoning. Label
reading and geographic research have separate requests and 600-second stage
limits. --label-model and --llm-model can override the models independently.
--workers auto shares provider backoff; it does not override account limits.

Use --limit 10 for a small run. --max-llm-calls limits fresh provider attempts,
not tokens. LLM use can consume account allowance or credits; confirmation is
requested before calls unless --yes is supplied. Other CLI providers require
an explicitly configured compatible command; see .env.example.

## Coordinate Policy

Plausible existing decimal coordinates are adopted without an LLM call, even
without a photograph or GPS provenance. Zero placeholders, invalid/swapped
coordinates, country/state contradictions, indicated institutional locations
and apparent national-center/capital placeholders are screened first. A unique
valid latitude/longitude swap can be corrected locally. Current bundled Natural
Earth references provide conservative checks, not exact historical boundaries.
Institutional checks use supplied institutional coordinates or explicit source
flags; they are not a complete global herbarium-location registry.

A blank datum is assumed WGS84 with the exact remark
"datum not stated; assumed WGS84" and a 5000 m datum allowance added to the
radius. An unrecognized named geographic datum is also explicitly identified
as an assumption. Missing positional error receives a 1000 m policy allowance,
in addition to coordinate rounding and datum/transformation allowance. These
are conservative operational assumptions, not measured error or a universal
scientific bound. Supplied uncertainty is never reduced below the rounding
minimum. Original WGS84 decimal strings are preserved.

Otherwise read the whole specimen photograph. Detailed printed coordinates
are converted locally without geographic research; missing measurement error
uses the same explicit 1000 m allowance. Otherwise the LLM researches the
finest legible locality, including collecting-era names and relocated places.
It uses one relevant local language first, stops when sufficient evidence is
found and falls back to other languages only as needed.

For a named area, use a representative center or documented office, with a
radius covering the possible collecting area. A reported LLM radius is allowed.
Bounds enlarge an insufficient radius; a missing radius receives a clearly
identified low-confidence 50000 m policy allowance rather than a false precise
value. None of these policy defaults is an empirical accuracy guarantee.

Printed elevation is retained, converting common imperial units to metres.
Open-Meteo/Copernicus DEM may select an elevation-compatible point inside the
named bounds. Sparse DEM samples do not prove that all possible sites have
been constrained, so the automatic grid step retains a radius covering the
whole named extent. DEM height is reported to 10 m granularity, not guaranteed
10 m vertical accuracy. Species-wide --habitat generates warnings only; it
does not move points or exclude records.

No upper horizontal-uncertainty limit is imposed by default. For a particular
analysis, use --max-uncertainty-meters N. This explicit filter excludes an
otherwise adopted estimate exceeding N; it does not select a less defensible
alternative just because that alternative reports a smaller radius.

## Outputs

Six primary files are generated in output/<input_name>_georeferenced/:

- modified_dwc.csv
- modified_dwc.tsv
- georeference_candidates.tsv
- summary.txt
- georeference.log.jsonl
- georeference_flow.pdf

Final DwC contains accepted records, confidence, positive uncertainty, datum,
protocol, actual sources, decision remarks and unverified verification status.
The combined TSV retains transcriptions, alternatives and dispositions for all
records. JSONL logs preserve original/modified coordinates, model calls,
prompts, hashes, sources, assumptions and technical errors. Originals are not
modified. --output chooses a separate destination; --dry-run validates inputs
without LLM requests or new outputs. --input-dwc selects an edited CSV/TSV
explicitly if the two generated tables differ.

The automatic vector ribbon PDF uses one R/ggalluvial renderer, fixed
GBIF/CVH/Other grays and 50% opacity. Widths represent actual counts. Exits
terminate below the retained stream. Complete, hash-matched collector history
enables collection/deduplication stages; otherwise the figure starts at curator
input and does not invent missing collection paths. Source ownership after
deduplication is the retained representative portal, not all contributing portals.

Use scripts/plot_georeference_flow.R in RStudio for the same renderer:
source it, then call plot_georeference_flow(counts, "flow.pdf"). counts is a
data.frame with source/input/evidence/accepted or
source/collected/unique/evidence/accepted. The run's counts and R/package/renderer
versions are retained in summary.txt. Setup installs ggplot2 and ggalluvial into
the private .Rlibrary; R is checked before any LLM request.

## Personal Specimens And Identity Checks

Before curation, add optional personal/dwc.tsv (or dwc.csv) and whole photos in
personal/images/ beside the collector output. These are included automatically.
External tables/directories can be added with repeated --additional-input PATH.
Photo-only groups become records; use stable occurrenceID values. Names such as
MY1__front.jpg, MY1__back.jpg and MY1__label.jpg group whole views for one record.
Ambiguous image ownership and duplicate identifiers are reported, not guessed.

After visual inspection, prefix a misidentified specimen's input photograph
with miss_. Any marked view excludes the entire linked specimen, its final DwC
row and every exported view, without an LLM call. Markers are case-insensitive
and originals are retained for undo. --export-images creates a managed final
image folder for accepted specimens; unknown/edited files and symlinks in that
folder are protected. The software does not automatically certify identification.

Use run_collect_and_georeference.sh --pause-before-curation to pause between
collection and curation for additions and visual review. Partial collection
failures offer a y/n continuation choice.

## Cache And Interruption

v0.1.15 transcription instructions, response schema and cache-key format are
unchanged. Reuse the previous .cache/llm_georeference_curator directory (or pass
--llm-cache-dir PATH); matching model/settings, prompt and image content reuse
the completed reading. No command-line cropping or extra OCR stage is added.
Only geographic-research instructions changed. Old record decisions are
recalculated under the new policy; completed transcription responses remain
reusable. Retry a failed research call without rereading a successful label.

Ctrl-C cancels the entire run and all owned provider process groups, including
parallel workers, with exit code 130. A provider process exiting on SIGINT is
also treated as whole-run interruption, not a record error followed by the next
specimen. Completed checkpoints/caches are retained; partial results are not
published as a completed final dataset.

See [methodology](docs/methodology.md) and the root v0.1.16 validation record.
The point-radius approach follows the concepts of Chapman & Wieczorek (2020),
with explicit VASCULUM assumptions and automated LLM extensions; it is not a
claim of guaranteed accuracy or independent validation.
