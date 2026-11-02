# VASCULUM v0.1.14

This archive contains the complete VASCULUM source tree, including the collector,
LLM georeference curator, CheckList, documentation and offline tests. Collection
and curation can run separately or through the combined wrapper. CheckList's
independent component version remains v0.1.2; its functionality is unchanged.

## Main Changes

- Automatic georeference_flow.pdf uses R/ggalluvial only, fixed GBIF/CVH/Other
  grays and 50% opacity. Actual counts are conserved by source; exclusion
  branches terminate instead of inflating later stages. No synthetic results
  enter production plots. Matching collector provenance enables the collection/
  deduplication steps; older or changed datasets begin at curator input.
- Install R before setup_mac.sh. Setup prepares the R plotting packages and
  analysis checks their availability before LLM work. The default output set
  now contains six artifacts, including the ribbon PDF.
- Evidence-based georeference acceptance replaces decimal-count trust and LLM
  self-confidence cutoffs. Datum conversion, uncertainty components, conflicting
  evidence and accepted/review/unresolved/excluded/failed states are explicit.
- DwC-based collection adapters retain supplied datum, uncertainty and coordinate
  provenance for the curator. Duplicate merging cannot mix coordinate components
  or trust metadata from different records. Occurrence-data and image licenses,
  underlying datasets and portal provenance are recorded separately.
- Label distances from roads, rivers or villages are not interpreted as elevations.
  Printed altitude/elevation takes precedence over DEM-derived estimates.
- Final modified DwC always contains accepted records only. Curation-mode
  switches have been removed; review, unresolved, excluded and failed records
  remain in the candidate TSV and audit log for inspection and retry.
- One photo-first routing rule retains trusted existing coordinates without LLM
  use. New estimates require a local photograph and readable collecting-label
  evidence. DwC locality text is auxiliary context, never a no-image fallback.
- Text-only comparison, benchmark generation/evaluation, prompt profiles and
  no-effect legacy switches have been removed. No bias-analysis feature is added.
- Species-wide habitat is advisory after coordinate selection; it does not
  relocate points, rank candidates, change uncertainty or exclude occurrences.
  Named-route proposals require collection-text support and remain reviewable.
- Defaults prefer Codex CLI with gpt-6.1-sol/medium. Label reading and geographic
  research have separate model settings and response contracts. Exact-response
  caches, duplicate-request locking, run-wide provider-attempt budgets, usage
  reporting and per-record checkpoints reduce redundant LLM work.
- Stage timeouts default to 600/600/600 seconds. Timeouts/call budgets are not
  token caps. Local concurrency heuristics do not override provider/account limits;
  model fallbacks do not bypass rate/usage limits.
- Personal DwC tables and/or photographs can be added between collection and
  curation. Automatic personal/ discovery, repeatable --additional-input, stable
  scoped identifiers, multiple whole-photo views and provenance are supported.
  Personal DwC-only rows use the same trusted-coordinate/photo requirement.
- Main TSV/CSV disagreements and ambiguous image/record links are reported
  before LLM work, instead of silently ignoring an edited file or guessing a link.
- --pause-before-curation provides an explicit manual-edit break in the combined
  wrapper. Partial collector failures preserve the existing y/n continuation choice.
- Human misidentification review uses the miss_ image filename prefix. The linked
  specimen, including all its photo views, is excluded from final DwC without an
  LLM request. Input markers remain available for audit/undo and survive collector
  pruning; matching collector downloads are not fetched again.
- Optional --export-images creates a clean final image set for included records
  and synchronizes previously managed copies after exclusions. Unknown/edited
  files in an existing export folder are not silently deleted or overwritten.

## Upgrade And Use

1. Extract into a new VASCULUM directory and run bash setup_mac.sh.
2. Reuse prior taxon outputs under herbarium_specimen_collector/output/. Keep a
   backup when moving research data; the distribution itself contains no outputs.
3. Add personal/dwc.tsv or personal/dwc.csv and/or personal/images/ within a run,
   or use curator --additional-input for an external collection. Apply miss_ to
   input photographs, not derived final-output copies.
4. Run bash check_release.sh before a real analysis. Use --dry-run to inspect the
   merged input count. Remove --limit when all additional records should run.
5. Add curator --export-images for a clean final photo folder. The default primary
   artifacts are modified DwC CSV/TSV, candidate TSV, summary, JSONL log and ribbon PDF.

Documented original coordinates remain subject to provenance, datum, uncertainty
and consistency checks. Printed label coordinates without justified total error,
model-only points and route proposals may require review. Final DwC exports
accepted records only; imported personal data does not bypass those safeguards.
Remove --robust, --standard, --curation-mode, --prompt-profile,
--georeference-method, --original-precision-decimals, --taxon-habitat,
--llm-timeout-seconds and --debug-log from older commands. Rerun
curation to apply the new policy to existing outputs; old record checkpoints
are invalidated by the code change, while exact LLM-response caches remain usable.

## Validation And Limits

The offline suite covers collector behavior, stage/provider contracts, token
accounting, photo-first evidence policy, CRS handling, checkpoints,
habitat-warning immutability, personal inputs, handoff prompts, miss_ exclusions
and managed image exports. Release checks also compile source, validate shell
scripts, smoke-test CLI entry points and scan for obvious credential patterns.

No new live-LLM positional-accuracy experiment is claimed by this release. Newer
models, source tracing and methodological similarity to Xie do not establish
an individual 10 km guarantee or measured improvement. Six-decimal coordinates
are a reporting format, not a sub-meter accuracy claim. See the methodology
document for routing, acceptance, replay and limitations. Protocol acceptance
does not establish positional accuracy; independent ground-truth validation is
still required for a quantitative accuracy claim.

The distribution excludes virtual environments, authentication/config secrets,
private caches, generated analysis outputs and local Git history. Review personal
names, collecting localities, image metadata and licenses before separately
publishing research data. Creating this archive does not publish a GitHub release
or push changes to the remote repository.
