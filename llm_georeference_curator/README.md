# LLM Georeference Curator

Version: `v0.1.7`

`llm_georeference_curator` performs LLM-assisted georeferencing for
`herbarium_specimen_collector` outputs. It reads collector DwC exports and
whole specimen images, transcribes locality-bearing label evidence with an LLM
when needed, conducts multilingual web research across current and historical
locality sources, generates reviewable coordinate candidates, and writes
modified Darwin Core CSV/TSV exports.

## Design

This pipeline is intentionally separate from `herbarium_specimen_collector`,
but compatible with its output directory:

```text
herbarium_specimen_collector/output/<run>/
  dwc.tsv or dwc.csv
  images/
  summary.txt
```

The output is deliberately small:

```text
modified_dwc.csv
modified_dwc.tsv
georeference_candidates.tsv
summary.txt
georeference.log.jsonl
```

`georeference_candidates.tsv` combines label-reading results and coordinate
candidates in one audit table. It is the main file for later checking.

## Quick Start

```bash
cd VASCULUM/llm_georeference_curator
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --robust \
  --habitat "subalpine forest"
```

For a small real-LLM smoke test:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --robust \
  --habitat "subalpine forest" \
  --limit 10 \
  --llm-mode on
```

The default `xie-modified` profile uses a token-conscious three-stage flow:

1. Read the whole specimen image without web search. If a detailed WGS84 label
   coordinate is found, convert it to six-decimal decimal degrees and stop.
2. Only when no detailed coordinate exists and the locality reaches
   municipality or a comparably specific level, research a coordinate from the
   transcription without sending the image again.
3. Verify researched coordinates against available terrain, habitat, route,
   hydrology, and DEM evidence.

A coarse label coordinate such as `N 23°31′ E 120°48′` is a search anchor, not
the final result. Web research starts with the label language and only the most
widely used relevant official/local language of the collecting country. It
stops when a defensible source-backed locality is found. English is used only
if unresolved. Chinese is considered only for Chinese-language regions,
Chinese label text, or specifically relevant historical sources; it is not a
universal fallback. It refines coordinates with locality names,
historical toponyms, roads/trails, elevation, terrain, hydrology, vegetation,
collector context, and auditable sources. The profile follows Xie et al. (2025)
as a first-pass LLM georeferencing protocol, with VASCULUM additions for image
label reading and ecological review. For source-backed trail/locality anchors,
the default
post-processing stage queries nearby OpenStreetMap trail/road vertices and
mapped land-use, vegetation, water, coast, and substrate context, then compares
their Open-Meteo Copernicus 90 m DEM elevations with the researched elevation.
The selected route vertex is written as WGS84 with six decimal
places; `coordinateUncertaintyInMeters` retains honest uncertainty. Six digits
make the chosen point reproducible and do not imply sub-meter accuracy.

Disable only this network refinement stage with
`--no-geospatial-refinement`. The LLM and its web research remain available.

## Habitat Constraints

Provide the target taxon's environment before a run. For
`Haplopteris mediosora`:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --robust \
  --habitat "subalpine forest"
```

Repeat the option for compound ecology:

```bash
--habitat "cloud forest" --habitat river --habitat limestone
```

The controlled vocabulary covers:

- Human and disturbed: `city`, `ruderal`, `roadside`, `agricultural`, `paddy field`, `plantation`, `orchard`
- Forest: `forest`, `lowland forest`, `temperate forest`, `boreal forest`, `tropical rainforest`, `dry forest`, `cloud forest`, `montane forest`, `subalpine forest`
- Open and cold vegetation: `alpine`, `shrubland`, `heathland`, `grassland`, `meadow`, `savanna`, `steppe`, `tundra`
- Wetland and freshwater: `wetland`, `marsh`, `swamp`, `bog`, `fen`, `peatland`, `riparian`, `river`, `stream`, `spring`, `waterfall spray zone`, `lake`, `pond`, `freshwater aquatic`
- Coastal and marine: `brackish`, `estuary`, `mangrove`, `salt marsh`, `sea`, `seagrass meadow`, `intertidal`, `rocky shore`, `beach`, `coastal dune`
- Dry and geological: `desert`, `semi-desert`, `inland dune`, `limestone`, `karst`, `ultramafic`, `volcanic`, `geothermal`, `gypsum`, `saline`, `rock outcrop`, `cliff`, `scree`, `cave`
- Microhabitat: `epiphytic`, `canopy`, `soil`, `rock crevice`

Unrecognized free text is also retained and sent to the LLM. `--taxon-habitat`
remains as a compatibility alias for older commands.

The LLM is instructed to check official national topographic, land-use,
vegetation, hydrology, coastline, and geology sources and globally applicable
products such as ESA WorldCover or Copernicus land-cover data. The deterministic
post-processing stage directly checks OSM environmental geometry and Copernicus
DEM elevation. A clear contradiction is retained in the candidate TSV as
`candidateType=ecological_conflict` with score `0.00`, but cannot enter final
DwC. Missing map coverage is treated as unknown, not as evidence of absence.
The normalized input is written to `summary.txt` and the `habitatPrior` column
of every candidate row.

## LLM Provider

The default provider is `codex-cli`, intended for personal high-quality
verification on a signed-in machine:

```bash
codex login
codex login status
```

On another computer, LLM mode works only if the selected provider is available:

- `codex-cli`: ChatGPT/Codex CLI must be installed and `codex login` must be complete.
- `openai`: `OPENAI_API_KEY` must be set.
- `opus`/`opus5`/`custom-cli`: a local command must be available through `--llm-command` or PATH.

Before real LLM calls, the pipeline performs a preflight check and prints the
provider, model, executable/login status, reasoning effort, and web-search
mode. It then asks for confirmation because LLM calls and web searches may
consume tokens or credits. When route/DEM refinement is enabled, the same
confirmation discloses that anchor coordinates and nearby mapped route and
environmental features may be requested from public Overpass and coordinate
points may be sent to Open-Meteo; specimen images and label transcriptions are
not sent to those two services. Use
`--yes` only for intentional unattended runs.

While an LLM or environmental refinement request is running, the terminal
shows an animated or repeated status line containing the provider, model,
record number, catalog number, and elapsed seconds. Long web-research calls
therefore remain visibly active until completion or timeout.

Default model selection is `auto`, which currently prefers `gpt-5.5` for
routine high-throughput curation and keeps `gpt-5.6-sol` as the first fallback
or explicit deep-review model. Coordinate research uses reasoning `high`;
image transcription uses `medium` with web search disabled. Label reading,
coordinate research, and coordinate verification each default to 600 seconds:

```bash
--label-timeout-seconds 600 \
--georeference-timeout-seconds 600 \
--verification-timeout-seconds 600
```

Matching LLM responses are cached under
`.cache/llm_georeference_curator/`, so rerunning unchanged records does not
spend tokens again. Disable this only when intentionally re-evaluating every
record with `--no-llm-cache`. Candidate order can be set in
`.env`:

```bash
CODEX_MODEL_CANDIDATES=gpt-5.5,gpt-5.6-sol
LLM_REASONING_EFFORT=high
VASCULUM_LLM_WORKERS=4
```

Run explicitly with Codex:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --llm-provider codex-cli \
  --llm-model auto \
  --llm-web-search indexed
```

Use `gpt-5.6-sol` explicitly for difficult review cases:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --llm-model gpt-5.6-sol \
  --llm-reasoning-effort xhigh \
  --llm-web-search live
```

## Parallel Processing

Records are independent, so the curator can process them in parallel:

```bash
--workers auto
```

`auto` is the default. It chooses a provider-aware worker count, lowers the
cap for heavier `gpt-5.6-sol` or live-search runs, and respects
`VASCULUM_LLM_WORKERS`, `VASCULUM_MAX_WORKERS`, or Codex
`agents.max_concurrent_threads_per_session` when configured. Use
`--workers max` to use the configured cap, or a positive integer such as
`--workers 6` when you intentionally want a fixed limit.

The implementation follows OpenAI's rate-limit guidance: rate-limit or
usage-limit failures are retried with exponential backoff
(`--llm-rate-limit-retries`, default `2`). Non-rate-limit failures, including
timeouts and invalid JSON, are recorded in `summary.txt` and
`georeference.log.jsonl` rather than silently retried.

Unattended run after you have already checked the provider:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --robust \
  --limit 10 \
  --llm-mode on \
  --yes
```

Use OpenAI API instead:

```bash
export OPENAI_API_KEY="your_openai_api_key"

bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --llm-provider openai \
  --llm-mode auto
```

Use an Opus-style local CLI:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --llm-provider opus \
  --llm-command "opus5 --model {model} --json"
```

The command must return a JSON object on stdout. Placeholders available to
custom commands are `{model}`, `{prompt_file}`, and `{image_paths}`.

## Whole-Image Policy

The pipeline passes a whole specimen image to the label-reading stage only when
the image has not already been transcribed and the original DwC coordinate is
not already precise. It does not perform command-line label cropping. The
coordinate-research stage receives the transcription rather than the image.
The prompts instruct the LLM to:

- identify the main original collection label first
- distinguish annotation, determination, barcode, accession, exchange, and later
  herbarium labels
- avoid mixing locality evidence across labels unless it clearly refers to the
  same collecting event
- transcribe only confidently readable text
- search with the label language and one major collecting-country language,
  then use English only if unresolved
- use Chinese only when the specimen or historical-source context makes it
  relevant
- cross-check gazetteers, roads/trails, elevation, terrain, hydrology,
  vegetation, land-use, public map/satellite context, and collector context
  when available
- record source URLs and evidence layers for refined candidates
- return no coordinate candidates when locality is too broad or unreadable

Images that are small or heavily compressed are flagged in
`georeference_candidates.tsv` with `imageQualityStatus`, but they are still
passed as whole images.

## Elevation Priority

An elevation printed on the original label always takes precedence over an
LLM or DEM estimate. Metric values are retained in meters; common imperial
forms such as `ft`, `feet`, and `foot` are detected and converted to meters.
Only records without a readable label elevation receive an estimated elevation.
Estimated values are rounded to a 10 m reporting granularity (for example,
`2040 m` or `1970 m`). This reporting granularity is recorded in
`georeferenceRemarks` and is not a claim that a 90 m DEM has guaranteed
vertical accuracy of plus or minus 10 m.

## Curation Modes

`--standard`

Keeps valid but coarse original DwC coordinates as final coordinates when no
better candidate is found. These rows are marked
`verification=review_coarse_original`.

`--robust`

Keeps coarse original coordinates in `georeference_candidates.tsv`, but does
not select them for final DwC. Coarse degree/minute label coordinates are also
kept only as search anchors. A web-refined LLM candidate must include an
auditable source URL, score at least 0.60, and report uncertainty of 10 km or
less before automatic selection. When an exact collecting point cannot be
defended, a specific web-supported locality/trail anchor may still be selected
as a point estimate if its uncertainty is 5 km or less; it is explicitly
marked for review. This is safer for downstream SDM datasets.

When a source-backed anchor can be refined against mapped route geometry and
DEM elevation, the added `route_dem_refinement` candidate is preferred. Its
OSM way URL, elevation service documentation, evidence layers, and uncertainty
are retained in `georeference_candidates.tsv`. API/network failures fall back
to the LLM candidate and are listed as warnings in `summary.txt`.

Route geometry is credited to OpenStreetMap contributors under ODbL. DEM
values are credited to Open-Meteo and the Copernicus DEM GLO-90 dataset; the
relevant OSM copyright page, Open-Meteo documentation, and Copernicus DOI are
stored with each refined candidate.

## Optional Inputs

Label transcription table:

```bash
--label-tsv label_transcriptions.tsv
```

Recognized columns:

```text
catalogNumber
imagePath
detectedLanguages
labelTranscription
localityText
eventDateText
collectorText
elevationText
```

Local gazetteer:

```bash
--gazetteer-tsv regional_gazetteer.tsv
```

Recognized columns:

```text
placeName
decimalLatitude
decimalLongitude
country
stateProvince
aliases
historicalPlaceName
uncertaintyMeters
elevationMeters
language
source
```

## Exit Codes

- `0`: completed without errors
- `1`: fatal configuration or runtime error
- `2`: invalid command-line usage
- `3`: output completed, but one or more records reported errors
- `130`: interrupted
