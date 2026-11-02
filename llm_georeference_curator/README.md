# llm_georeference_curator v0.1.15

LLM-assisted georeferencing for herbarium specimen photographs and Darwin Core
(DwC) records. Original source material comes first; documented existing
coordinates are retained.

## One Selection Rule

1. **Trusted existing coordinates:** retain them, with or without a photograph.
   Do not reread the label with an LLM or research replacement coordinates.
2. **Untrusted/missing coordinates, photograph available:** read the original
   collecting label. Convert detailed printed coordinates, or research the
   collecting locality actually read from the photograph. DwC is auxiliary
   context, never a substitute for missing label evidence.
3. **Untrusted/missing coordinates, no local photograph:** exclude the specimen
   from final DwC. Do not infer coordinates from DwC locality text alone.
4. **Photograph unreadable or locality insufficient:** retain the reason in the
   candidate TSV and log, not the final DwC. An unreadable label requires review;
   an evaluated country/province-only label is excluded. A technical reading
   failure remains retryable, rather than being treated as a poor locality.

A remote image URL or a missing local file does not count as an available
photograph. A supplied transcription needs its linked source photograph.
Untranscribed images with LLM disabled are unresolved, not automatically accepted.

There are no standard/robust modes, alternate prompt profiles, text-only
benchmark workflows, or locality-only fallback. No digitization-bias analysis
or additional statistical reports are generated.

## Quick Start

From the VASCULUM root:

Install R from https://cran.r-project.org/ first. setup_mac.sh installs ggplot2
and ggalluvial into the pipeline's private .Rlibrary/ when needed. These R
packages are required for the automatic ribbon PDF; no alternate renderer is used.

```bash
bash setup_mac.sh
codex login
codex login status

bash llm_georeference_curator/run_llm_georeference_curator.sh \
  --input herbarium_specimen_collector/output/Haplopteris_mediosora \
  --habitat "subalpine forest"
```

Codex CLI is the default provider and uses the saved Codex/ChatGPT login.
An OpenAI API key is not required for that provider. Before actual LLM work,
the program checks the provider and requests confirmation about token/credit
use. Trusted-coordinate and no-photo-only runs do not need an LLM provider.
Use --llm-mode on to require LLM availability when photo reading is needed,
or --llm-mode off for local checks and supplied label transcriptions.

Models default to auto selection with gpt-6.1-sol first and medium reasoning.
Label reading and locality research use separate requests. Stage timeouts
default to 600 seconds each. Parallel workers default to auto and share
rate-limit backoff; this is not a claim about the account's allowed concurrency.
The terminal shows model names and per-record progress.

A small run:

```bash
bash llm_georeference_curator/run_llm_georeference_curator.sh \
  --input herbarium_specimen_collector/output/Haplopteris_mediosora \
  --llm-mode on \
  --limit 10
```

Exact-response caching and completed-record checkpoints are enabled by default.
Changed records/photos/settings/code invalidate the corresponding work. Removing
--limit processes the full input. Stage failures remain eligible for retry.
--max-llm-calls caps fresh provider attempts, not tokens.

## Outputs

Six primary files are written to output/<input_name>_georeferenced/:

- modified_dwc.csv
- modified_dwc.tsv
- georeference_candidates.tsv
- summary.txt
- georeference.log.jsonl
- georeference_flow.pdf

Final DwC contains accepted records only. The candidate TSV combines label
transcriptions, candidate coordinates, evidence and dispositions for all records.
The log preserves model requests, source references, input/image/code hashes,
before/after coordinates and errors. These are audit artifacts, not extra
bias-analysis reports. Input tables and original photographs are not modified.

Use --output PATH for a separate output directory, --dry-run to validate inputs,
or --input-dwc PATH to choose a table explicitly when edited TSV and CSV disagree.

### Ribbon Plot

The PDF is generated from actual per-record decisions: GBIF, CVH and Other keep
their fixed dark/mid/light grays through retained and excluded branches.
Ribbon opacity is 50%; width is proportional to count. Exclusions terminate
below the main stream. Only stage names and counts are shown; no simulated
records or estimates are inserted into production plots.

New collector outputs record source-specific counts and a DwC hash inside
summary.txt, and vasculumPrimarySource in DwC. A unique specimen keeps its
retained primary source; additional portal records terminate as merged duplicates.
vasculumSources preserves the contributing portal list. datasetName/datasetID
are reserved for the supplied underlying dataset metadata. Thus the diagram
tracks the retained representative portal, not every duplicate's original portal.

With matching, complete collector provenance, the plot starts with collection
and duplicate merging. Older/edited/subset inputs and personal additions start
at curator Input instead; unavailable duplicate paths are never reconstructed
by guessing. Mixed legacy datasetName values require vasculumPrimarySource for
unambiguous attribution. A plotting error is reported without discarding DwC,
candidate TSV or log; a previous run's plot is not left as a current result.

scripts/plot_georeference_flow.R is the same renderer used automatically and in
RStudio. Source it and call plot_georeference_flow(counts, "flow.pdf") with a
data.frame containing source/input/evidence/accepted, or
source/collected/unique/evidence/accepted. Counts used for the run are recorded
as Ribbon counts in summary.txt. R/package availability is checked before any
LLM request; --dry-run does not require R or write a plot.
R/package versions and the R renderer's hash are retained in the summary's
reproducibility configuration. Changes to the R renderer alone do not invalidate
completed scientific-record checkpoints.
Source evidence means the trusted-coordinate route or a usable source-label
reading was reached. Not advanced includes manual exclusions, unreadable/broad
labels, missing images and failures before that gate. Not accepted includes
review, unresolved and failed cases after the gate, not only rejected specimens.

## Add Your Own Specimens Before Curation

Add photographs and/or a personal DwC table beside the collector output:

```text
Haplopteris_mediosora/
  dwc.tsv
  dwc.csv
  images/
  personal/
    dwc.tsv
    images/
      MY0001__front.jpg
      MY0001__label.jpg
      MY0002.jpg
```

The personal/ folder is included automatically. A photo-only group becomes one
specimen; multiple whole images are read together. Useful view suffixes include
__front, __back, __recto, __verso, __label, __label1 and __1. JPEG, PNG and WebP
are supported. A personal DwC row without a photograph is retained only if
its existing coordinates pass the same trust checks.

External DwC CSV/TSV files or directories can be added with repeated
--additional-input PATH. Tables resolve their images beside themselves.
Use stable occurrenceID values for permanent specimen identities.
Ambiguous image ownership and duplicate occurrenceID values cause a clear error
instead of a guessed link. Source provenance remains attached to each specimen.

--no-include-personal disables personal/ discovery. Loose photographs in the
main collector images/ folder are not made into extra specimens unless
--import-unlinked-images is supplied. It is safer to place additions in personal/
than to rely on edits to main tables that the collector can regenerate.

## Remove Misidentifications Visually

Prefix an input image filename with miss_:

```text
images/P01185529_Haplopteris_mediosora.jpg
  -> images/miss_P01185529_Haplopteris_mediosora.jpg
```

On the next run, the entire linked specimen is excluded: both final DwC rows
and all exported photo views. This overrides even trusted coordinates.
No LLM/geospatial request is made for that specimen. The prefix is
case-insensitive; a marker on one view is enough.

Original marker photos are retained for audit and undo. Remove all miss_
prefixes on that specimen's input views to restore eligibility. Do not rename
derived final-image copies to mark exclusions.

This is a human identification workflow: inspect the photograph, mark a
misidentification, and rerun. The program does not automatically diagnose
species identification from the image.

## Optional Final Image Folder

Add --export-images to copy photographs of accepted specimens into the final
output/images/ folder and write portable media references in modified DwC.
miss_-marked and other unaccepted specimens are not copied.

Once enabled for an output directory, managed exports stay synchronized on
later runs. Only the program's unchanged managed copies are removed or replaced.
Unknown or user-edited files cause an actionable error; use a fresh --output
rather than risk overwriting original photographs.

## Collection-To-Curation Handoff

```bash
bash run_collect_and_georeference.sh \
  --pause-before-curation \
  --taxon "Haplopteris mediosora" \
  --synonym "Vittaria mediosora" \
  --image-resolution standard \
  -- \
  --habitat "subalpine forest" \
  --export-images
```

--pause-before-curation waits for manual additions/review after collection.
Collector partial failures offer a y/n continuation choice. The explicit pause
still waits for a decision even when curator --yes is used for unattended LLM
confirmation.

## Evidence And Elevation

Trust is based on coordinate provenance, a recognized/converted datum,
documented uncertainty and consistency checks, not decimal-place count.
Documented originals, sufficiently supported printed coordinates and verified
reference extents can pass these checks. Model confidence and reachable URLs
alone do not establish acceptance. LLM-only estimates remain review proposals
until the evidence requirements are met.
These are protocol checks, not proof of the unknown true collecting position.

Printed-coordinate rounding is only a minimum uncertainty. An explicitly
justified --label-coordinate-uncertainty-meters or sidecar measurement allowance
can be added; it must not be invented to increase acceptance. Unknown datum
and uncertainty remain unknown. --max-uncertainty-meters is an optional
study-specific acceptance limit, not an accuracy guarantee.

--label-tsv accepts manual transcriptions keyed by catalogNumber and linked by
imagePath. Supply localityText for locality research; detailed printed coordinates
can be parsed from labelTranscription. --gazetteer-tsv can provide verified
reference locations for names actually read from source photographs.
Neither input bypasses the photograph requirement for new coordinate estimates.

Whole photographs are passed to label reading; no command-line crop or OCR
pipeline is used. Search uses one relevant collecting-country language first,
then English only if needed; Chinese fallback is context-dependent.

Printed label elevations are retained, with common imperial units converted
to metres. Without a readable label elevation, DEM estimates are reported to
10 m granularity, not guaranteed plus/minus 10 m accuracy.
Recorded collecting-event terrain/habitat can support locality research.
--habitat supplies species-wide ecology for warnings only; it never relocates
points, changes uncertainty or excludes a specimen.

## Upgrade Notes

Remove the former --robust, --standard, --curation-mode, --prompt-profile,
--georeference-method, --original-precision-decimals, --taxon-habitat,
--llm-timeout-seconds and --debug-log options from old commands.
Use the three stage timeout options if needed; the six primary outputs,
including the log, are always produced.

Old output files do not change by themselves: rerun curation. Updated code
invalidates old completed-record checkpoints; matching LLM-response caches
remain reusable where prompts and inputs still match.

See [methodology](docs/methodology.md) for the routing and evidence rules.
The workflow is inspired by Xie et al. (2025), not an exact replication or
a claim of measured improvement. Six decimal places are formatting, not
sub-metre accuracy; no per-record 10 km guarantee is claimed.
