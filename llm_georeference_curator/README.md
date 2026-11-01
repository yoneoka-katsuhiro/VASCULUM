# LLM Georeference Curator

**Turn specimen labels and locality metadata into reviewable coordinate
decisions with auditable LLM assistance.**

Version: `v0.1.14`. Evidence protocol: `georeference-evidence-v2`.

LLM Georeference Curator reads Darwin Core records and whole specimen images,
transcribes locality-bearing label evidence when needed, researches current and
historical place sources, and records the evidence behind every coordinate
decision. It is designed to support expert review, not to turn model confidence
into automatic geographic truth.

| Input | Main output | Best suited for |
| --- | --- | --- |
| Collector output, other Darwin Core tables, and/or personal specimen images | Curated Darwin Core CSV/TSV, candidate TSV, summary and JSONL audit log | Georeferencing backlogs, collection data cleaning, SDM preparation and reproducible locality research |

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

## Exclude Misidentified Specimens With `miss_`

Visual inspection of actual specimen photographs is part of the workflow, not
just a download preview. A researcher can check identifications and collection
labels and remove unsuitable specimens before geographic inference. The
decision is a human taxonomic review; this is not automatic species diagnosis.

Rename an **input** photograph by prefixing its filename with `miss_`:

```text
images/P01185529_Haplopteris_mediosora.jpg
    -> images/miss_P01185529_Haplopteris_mediosora.jpg

personal/images/MY0001__label.jpg
    -> personal/images/miss_MY0001__label.jpg
```

No option is needed. The prefix is case-insensitive. The next run excludes the
linked specimen from both modified DwC files in standard and robust modes,
even if its original coordinates are trusted. Marking one view excludes the
whole specimen, not just that image; an unmarked front view cannot bring a
misidentified specimen back into the dataset. Main, personal, external and
explicit label-sidecar image links are supported. Exact filenames and scoped
catalog matches are used; `AB1` does not match `AB10`. Ambiguous matches require
disambiguation instead of guessing. Photo-only groups are recognized after
stripping `miss_`, rather than becoming a new specimen named `miss_MY0001`.

Excluded specimens are not sent to an LLM or external geographic services.
The candidate table and JSON log retain the record identifier, marker path and
`exclude_manual_misidentification` reason. The summary reports the manual
exclusion count separately from insufficient locality.

If a collector rerun has already omitted the record before handing off its DwC,
that earlier exclusion is recorded in the collector's summary instead; the
curator does not invent a missing input record from a filename alone.

Original input files are neither deleted nor renamed by the curator. Audit tables intentionally
mention `miss_` paths; they are not part of the final specimen image set.

Changes are read on the next run, not during an already running analysis.
Checkpoints cannot override a new marker. Remove the prefix from every marked
input view and rerun to restore eligibility under the normal quality rules.
Do not rename derived final-output copies instead of their input photographs;
inputs are the authoritative review markers.

### Export A Clean Final Image Folder

The usual five primary outputs remain the default. Add `--export-images` when
you also want a portable final image set:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/My_run \
  --llm-mode on --export-images
```

Only images linked to records actually included in modified DwC are copied to
the final output's `images/<specimen-key>/` subdirectories. `associatedMedia`
then points to those copies. No `miss_` photographs, nor other views of an
excluded specimen, enter the final image folder. Robust mode also omits images
of records rejected by its acceptance rules.

Once enabled for an output directory, later reruns keep its managed image set
in sync even when the option is omitted. Old managed copies of newly excluded
specimens are removed; source photos remain untouched. Ownership/hashes are
retained in the private cache and log. Unrecognized or manually edited files
inside an existing output image folder are never silently deleted/overwritten:
choose a fresh `--output` if the tool reports such a conflict. Preserve the
private cache for managed-export synchronization, as with response replay.

## Add Your Own Specimens Before Curation

The curator reads inputs when it starts, not a frozen collector record list.
You can append/edit main DwC rows, add a separate personal DwC, add specimen
photographs without DwC rows, or combine those approaches. Inputs are not
overwritten by the curator. The recommended structure is:

```text
herbarium_specimen_collector/output/<run>/
  dwc.tsv
  dwc.csv
  images/                       # collector photographs
  personal/
    dwc.tsv                     # optional; dwc.csv also supported
    images/
      MY0001__front.jpg
      MY0001__label.jpg
      MY0002.jpg
```

`personal/` is included automatically. A photograph group with no DwC row gets
a stable personal occurrence identifier and is sent through whole-image label
reading before locality inference. Naming `MY0001__front.jpg` and
`MY0001__label.jpg` groups both whole images as one specimen; recognized suffixes
are `__front`, `__back`, `__recto`, `__verso`, `__label`, `__label1`, `__image1`
and numeric suffixes such as `__1`. Use unique filename stems for different
specimens. Photo-only groups in separate subdirectories remain distinct even
when their filename stems are the same.
No extra cropping operation is required.

For metadata, a personal CSV can contain, for example:

```csv
occurrenceID,catalogNumber,country,stateProvince,locality,associatedMedia
urn:my-collection:MY0001,MY0001,Japan,Nagano,"Mt. Example, north trail","images/MY0001__front.jpg | images/MY0001__label.jpg"
```

Use normal DwC fields for collection date, collector, locality, coordinates,
datum and uncertainty. Photographs are optional for locality-based inference.
An explicit `occurrenceID` is recommended; absent personal identifiers are
generated deterministically as UUID URNs from run name, source and catalog number.
These generated identities are run/source-scoped, not proof that records from
different collections are the same physical specimen. Supply permanent identifiers
when merging projects or renaming/moving source directories. If catalog number
is also absent, a metadata-based identifier is generated, which can change
when that metadata is edited. Provided identifiers are never replaced.

An unlinked personal photo whose filename group exactly matches a unique
existing `catalogNumber` is attached to that record rather than creating a
duplicate specimen. A personal DwC row's own images resolve beside its table.
Ambiguous image/record matches and duplicate occurrence identifiers stop input
loading with an actionable message, before LLM calls. `AB1` never prefix-matches
`AB10`. To update an existing specimen's metadata, edit its original DwC row;
additional tables do not silently override existing occurrence identifiers.

Main DwC edits are also supported. If both `dwc.tsv` and `dwc.csv` exist and
differ, choose the edited file explicitly; the curator does not silently ignore
CSV edits or decide which table is newer:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/My_run \
  --input-dwc ../herbarium_specimen_collector/output/My_run/dwc.csv \
  --llm-mode on
```

An external DwC file or a directory with `dwc.tsv`/`dwc.csv` and/or `images/`
can be added without copying it into collector output:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/My_run \
  --additional-input ../my_collection/dwc.tsv \
  --additional-input ../another_collection \
  --llm-mode on
```

Repeat `--additional-input` as needed. An explicit table imports its rows and
matched images, not unrelated photos beside it; a directory also imports
otherwise unlinked images in its `images/`. Relative `associatedMedia` paths
resolve beside that source table, and absolute local paths are supported.
Supported local image formats are JPEG, PNG and WebP. Remote URLs are retained
but this stage does not download them.

`--no-include-personal` disables automatic `personal/` discovery. Unlinked files
in the main collector `images/` are only reported as warnings, since they may
be alternate downloads rather than new specimens. To deliberately import those
as records, use `--import-unlinked-images`; review for duplicates afterwards.
For ordinary manual additions, `personal/images/` is safer and needs no flag.

The summary reports personal DwC rows, photo-only specimens and photos attached
to existing records. Candidate TSV columns `inputType`, `inputSource` and
`imagePaths`, DwC extension columns `vasculumInputType`/`vasculumInputSource`,
and per-image hashes in the log preserve provenance. These additions do not
bypass uncertainty/acceptance rules: an imported specimen may still require
review or be excluded for genuinely insufficient locality. Personal images and
text can be sent to the selected LLM after the usual confirmation; take care
with private collecting sites and names.

Add/edit inputs **before** starting the curator. A running analysis uses the
input snapshot it already loaded. Added or edited rows and changed photographs
are detected on the next run; unchanged completed records remain reusable.
`--limit` applies to the combined list, with main DwC rows first; remove it when
you want every personal addition processed, not just the first N records.
The collector can regenerate its main DwC on a later collection run, so keep
personal additions in `personal/` or an external additional input rather than
relying on edits to generated main tables being preserved.

For an intentional edit break in the combined workflow, run from repository root:

```bash
bash run_collect_and_georeference.sh --pause-before-curation \
  --taxon "Haplopteris mediosora" --image-resolution standard \
  -- --habitat "subalpine forest"
```

The wrapper prints the collector output path and waits for `y/n` after collection.
Add your personal files, then answer `y`. Answering `n` stops before curation
without deleting collected or personal inputs. This explicit pause is not
bypassed by curator `--yes`. You can also run the two tools independently.

## Research Method And Reproducibility

The methodological core is inspired by [Xie et al. (2025)](https://doi.org/10.1038/s41477-025-02162-y):
an LLM infers latitude/longitude from hierarchical locality descriptions.
VASCULUM distinguishes this core from its extensions: specimen-image reading,
modern/historical web research, datum handling, uncertainty assessment and
post-selection ecological warnings. `--georeference-method xie-baseline`
implements a text-only one-shot comparator using country, state/province,
county and locality, without images, web research or geographic refinement.
It uses a new illustrative prompt and the selected model, so it is **not an
exact replication** of the original prompt, model or reported experiment.

Reproducibility and traceability are central: retain the input DwC/images,
five output files and private `.cache/llm_georeference_curator/` directory.
Per-record logs record before/after coordinates, actual returned model, stage
settings, complete prompt, prompt/code/input/image hashes, timestamps,
response-cache keys and errors. The candidate TSV preserves verbatim label
text. Retrieved source pages have bounded local snapshots, retrieval
timestamps and content hashes. Reachability alone does not establish a factual
locality match. Completed checkpoints and stage responses support replay
without new LLM calls. Live LLM calls and changing web/maps are not guaranteed
to regenerate identical results; preserve responses and snapshots used in a study.

The Xie supplementary comparison reports a GPT-4o median error of about 9.7 km
after excluding the largest 5% of errors. It does **not** guarantee that each
specimen lies within 10 km. Newer models and added research may help but do not
establish improved accuracy without an independent evaluation. Six-decimal
output is formatting, not six-decimal positional accuracy. Following an
existing methodological core does not automatically validate every output
for publication. Study-specific review and reporting remain necessary.

See [the 16-point implementation and evaluation protocol](docs/methodology.md)
for decision rules, limitations, benchmark commands and a suggested methods
description. The policy follows the distinction between precision, accuracy
and uncertainty in [GBIF georeferencing best practices](https://docs.gbif.org/georeferencing-best-practices/1.0/en/).

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

1. Retain a documented original coordinate when its datum and uncertainty are
   known and checks do not flag a conflict. Otherwise read the whole specimen
   image without web search. Detailed printed coordinates with a recognized
   datum are converted locally without geographic research. Unknown measurement
   error still requires review even when the printed coordinate is detailed.
2. Only when no detailed coordinate exists and the locality reaches
   municipality or a comparably specific level, research a coordinate from the
   transcription without sending the image again.
3. Refine coordinates using documented locality, route and elevation evidence.
   Afterwards, optionally review the selected point against species-wide
   habitat expectations, adding advisory warnings only.

A coarse label coordinate such as `N 23°31′ E 120°48′` is a search anchor, not
the final result. Web research starts with one major relevant local language of
the collecting country, preserving original label spellings as query terms. It
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
A route proposal requires a mapped route name tied to the collection text;
an arbitrary nearby road is not used. Route vertices remain review proposals,
not automatically preferred collecting points. Their uncertainty cannot
shrink merely because the map or DEM has fine resolution. Only established
total uncertainty is exported in `coordinateUncertaintyInMeters`; an LLM's
self-reported radius and a coordinate-rounding minimum are retained separately.

Disable only this network refinement stage with
`--no-geospatial-refinement`. The LLM and its web research remain available.

## Habitat Review

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

Species-wide habitat input is used only after selecting the final coordinate.
It is not sent to the coordinate-research LLM, used to rank route points, or
used to change scores, uncertainty, candidate eligibility or inclusion in DwC.
The taxon name is also omitted from the research payload; the prompt forbids
inferring location from general species ecology. Habitat explicitly recorded
in the label or the DwC `habitat` field remains collecting-event evidence.

The advisory check uses OSM environmental geometry and Copernicus DEM
elevation. A potentially conflicting location keeps its coordinate and receives
`habitatReviewStatus=warning` in the candidate TSV. Results may also be
`no_conflict_detected`, `unknown` (including unmapped expected habitat),
`unavailable` (service failure), `not_checked` or `not_requested`. Current map
conditions are not assumed to represent the historical collection environment.
These flags are heuristics for human review, not proof of a coordinate error.

The selected candidate's `habitatReviewRemarks` and `habitatReviewSources`
retain the explanation and sources. DwC `georeferenceRemarks`, JSON log
`habitatReview`, and summary counts expose the review result. The existing
`habitatPrior` column retains the supplied text for compatibility and does not
mean it influenced coordinate selection. Unrecognized free text is retained
but returns `unknown` when no controlled review rule exists. `--taxon-habitat`
remains a compatibility alias. Disabling network refinement also disables
this external habitat check. Habitat-only reruns can reuse the same cached
coordinate research, because the habitat input is absent from its prompt.

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

Default model selection is `auto`, which prefers `gpt-6.1-sol` with `medium`
reasoning for both coordinate research and image transcription. The previous
`gpt-5.5` and `gpt-5.6-sol` candidates remain availability fallbacks; fallback
occurs only for a model-selection error, never to bypass a usage limit.
Image transcription always disables web search. Label reading,
coordinate research, and coordinate verification each default to 600 seconds:

```bash
--label-timeout-seconds 600 \
--georeference-timeout-seconds 600 \
--verification-timeout-seconds 600
```

The two stages request different JSON outputs: label reading returns the full
verbatim text and extracted label fields, while coordinate research returns
only candidate points, sources and concise evidence. Research cannot overwrite
the original transcription. Detailed label coordinates are still converted
locally without an additional research call; documented original coordinates and
insufficient localities keep their existing early-exit behavior.

Matching LLM responses are cached under
`.cache/llm_georeference_curator/`, so rerunning unchanged records does not
spend tokens again. Disable this only when intentionally re-evaluating every
record with `--no-llm-cache --no-resume`. Identical requests made concurrently share one
call; the same image bytes can be reused under a different filename. Country,
collection date, locality, elevation, recorded habitat, original coordinates, model,
effort and instructions still distinguish requests. Different specimens are
not merged merely because they mention the same town. Updated instructions
and schemas invalidate earlier cache entries, so the first run after this
update may need fresh calls. Candidate order can be set in
`.env`:

```bash
CODEX_MODEL_CANDIDATES=gpt-6.1-sol,gpt-5.5,gpt-5.6-sol
LLM_REASONING_EFFORT=medium
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

For difficult records, explicitly choose `gpt-6-astra` with `medium`
reasoning for coordinate research while retaining Sol for label reading:

```bash
bash run_llm_georeference_curator.sh \
  --input ../herbarium_specimen_collector/output/Haplopteris_mediosora_low \
  --label-model gpt-6.1-sol \
  --label-reasoning-effort medium \
  --llm-model gpt-6-astra \
  --llm-reasoning-effort medium \
  --llm-web-search live
```

`--label-model` defaults to the research model. To evaluate a lower-cost
visual model separately, use `--label-model gpt-6.1-terra` with
`--label-reasoning-effort medium`. Compare difficult handwritten, multilingual
and multiple-label specimens against trusted transcriptions before adopting
it for a dataset. `LLM_LABEL_MODEL` and `LLM_LABEL_REASONING_EFFORT` provide
the corresponding environment overrides. Existing `.env` model/effort
settings take precedence over new defaults; edit them or use explicit CLI
options when upgrading.

Successful Codex JSON events and OpenAI Responses usage are recorded in
`summary.txt` and each record's `llmUsage` log entries. Input totals include
provider-cached input; local response-cache hits add no new tokens. Failures
or providers that do not report usage are excluded, so these totals are not
a billing estimate or a hard budget. Search-query limits in the prompt and
stage timeouts are also not token caps. Parallel workers improve throughput,
but do not reduce per-record usage.

This default is a starting configuration, not a measured accuracy claim for
herbarium labels. OpenAI describes Sol as a lower-cost model for complex
work and recommends comparing models on the same representative inputs.
Codex subscription limits and API prices use different accounting; consult
the [official model selection guide](https://developers.openai.com/api/docs/guides/model-selection)
and [Codex pricing and usage guide](https://learn.chatgpt.com/docs/pricing).

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
not already documented as reliable. It does not perform command-line label cropping. The
coordinate-research stage receives the transcription rather than the image.
The prompts instruct the LLM to:

- identify the main original collection label first
- distinguish annotation, determination, barcode, accession, exchange, and later
  herbarium labels
- avoid mixing locality evidence across labels unless it clearly refers to the
  same collecting event
- transcribe only confidently readable text
- search with one major collecting-country language, preserving label spellings,
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

Exports `accepted`, `review`, `unresolved` and `failed` records with explicit
`georeferenceVerificationStatus`. A selected review point is a proposal, not a
validated research occurrence. Country/province-only records that were actually
evaluated and remain insufficient are `excluded`. Processing failures are not
treated as evidence of insufficient locality.

`--robust`

Exports **only accepted records**. All proposals, transcriptions, unresolved
and excluded records remain in the candidate TSV and audit log. This differs
from older releases: a detailed-looking coordinate, high LLM self-score or
reachable source URL does not establish acceptance. Required evidence includes
a recognized/converted datum, documented locality/coordinate provenance and
defensible horizontal uncertainty. Documented originals, printed coordinates
with a justified measurement allowance, and explicitly verified local gazetteer
references can satisfy those checks. Name-matched but unverified gazetteers,
LLM-only points and route proposals remain reviewable.

Candidate selection prioritizes protocol acceptance and documented evidence,
not model confidence or a fixed route-first rule. Contradictory documented
points require review. `--max-uncertainty-meters` sets an optional study-specific
threshold; it is never a guarantee that a specimen is within that distance.
The legacy `--original-precision-decimals` option is accepted for compatibility
but has no trust-establishing effect. Six decimal places alone are insufficient.

Printed-coordinate rounding error is only a minimum. Use the sidecar column
`labelCoordinateUncertaintyMeters` or explicitly justified
`--label-coordinate-uncertainty-meters` for a measurement allowance. The
allowance is added conservatively to rounding and known transformation error;
it must not be invented to increase acceptance. Missing datum remains unknown.
Non-WGS84 geographic datums use PROJ/pyproj without ballpark transformations;
missing grids or unknown transformation accuracy require review. Projected
eastings/northings are not silently reinterpreted as latitude/longitude.

`--reference-regions-tsv` optionally supplies sourced country/state bounds for
consistency checks. Without supplied regions, the program does not claim to
have independently verified political boundaries. Near-border, historical
boundary and map-resolution ambiguities still require judgment.

Route/DEM proposals retain OSM URLs, elevation-service documentation and
uncertainty components in the candidate TSV. No route proposal is automatically
accepted merely because it lies on a modern path. API failures are warnings
and cannot establish an ecological or geographic contradiction.

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
labelCoordinateUncertaintyMeters
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
geodeticDatum
verificationStatus
boundingBox
```

Only set `verificationStatus=verified`/`validated` for a reference whose name,
extent, datum and collecting-locality applicability were independently reviewed.
`boundingBox` is JSON `[south,west,north,east]` in the reference datum; its full
extent is conservatively covered. Alternatively provide a documented positive
`uncertaintyMeters`. A verified flag is a user assertion, not an LLM assertion.
The reference region table uses `country`, optional `stateProvince`, `south`,
`west`, `north`, `east`, and `source` in WGS84.

## Resume And Spending Controls

Completed records are checkpointed immediately, before final exports, under
`.cache/llm_georeference_curator/runs/`. Matching input records, image bytes,
settings and implementation hashes allow reuse. Changed inputs/settings/code
invalidate completed-record checkpoints; stage response caches can still be
reused when their exact requests match. Transient failures are not saved as
completed records. Use `--no-resume` to reprocess records, and additionally
`--no-llm-cache` for new model calls. `--refresh-sources` refreshes retrieved
source snapshots and bypasses completed-record reuse; cached LLM responses
are still eligible for reuse.

`--max-llm-calls 20` limits actual fresh provider attempts across all workers,
stages, availability fallbacks and retries. Records that exceed the budget stay
retryable. The limit is not a token, money or web-tool-call cap: a single agent
request may perform multiple internal searches. Timeouts and the four-query
prompt budget are not token caps either. Parallelism changes throughput, not
the token requirement of the same work. The automatic worker count is a local
resource heuristic, **not a known account-wide OpenAI concurrency entitlement**;
provider usage limits always take precedence. Never rotate accounts or models
to bypass a usage-limit response.

`--verify-source-urls` retrieves at most three URLs per candidate with bounded
response bodies (up to 1,048,577 bytes), keeping private local snapshots.
`--no-verify-source-urls` disables that audit; it does not turn unverified points
into accepted ones. Query/response logs and snapshots can contain specimen
localities or personal names. They are excluded from source releases by default;
review sensitive-locality and collector information before sharing study data.

## Exit Codes

- `0`: completed without errors
- `1`: fatal configuration or runtime error
- `2`: invalid command-line usage
- `3`: output completed, but one or more records reported errors
- `130`: interrupted
