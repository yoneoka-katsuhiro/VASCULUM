# Georeferencing Methodology And Audit Protocol

Protocol identifier: `georeference-evidence-v2`.

This document maps the agreed 16 changes to the implementation. It describes
implemented behavior, not an empirical demonstration that the enhanced method
outperforms another method. The source release contains no private study data.

## The 16 Changes

1. **Separate core and extensions.** `--georeference-method xie-baseline`
   supplies only country, state/province, county and locality to a text-only
   one-shot prompt. Web, image transcription, original-coordinate selection,
   gazetteer selection, route refinement and ecological checks are bypassed.
   `evidence-assisted` is the default extension. `--prompt-profile xie` selects
   the baseline unless an explicit method is supplied. The baseline is inspired
   by Xie, not an exact prompt/model replication.

2. **Read before researching.** The transcription stage sees the original whole
   image, without web search or command-line cropping. It distinguishes the
   collection label from determination and annotation labels. Verbatim text,
   language, locality, collector, date and elevation are retained. Geographic
   research receives text, not the image again, and cannot overwrite the reading.
   Unreadable images are not evidence that the actual label lacks detail.

3. **Route inexpensive cases first.** Documented originals can bypass LLM work.
   Detailed printed coordinates with a usable datum are converted locally,
   bypassing geographic research. Specific DwC locality strings can trigger
   inference even without photographs or original coordinates. Country/state-only
   descriptions can be excluded after evaluation. A timeout is a processing
   failure, never a conclusion that locality is insufficient.

4. **Trust evidence rather than decimal places.** Original coordinates require
   documented GPS/GNSS or explicit verification metadata, a recognized datum,
   positive uncertainty compatible with rounding, and no flagged coordinate
   issue. Optional sourced country/state boxes check gross consistency. Missing
   boxes mean political-boundary consistency was not independently checked.
   The legacy decimal-count option no longer establishes trust.

5. **Keep coordinate systems explicit.** Hemisphere, DMS minutes/seconds and
   geographic ranges are validated. Missing datum is not silently WGS84.
   PROJ/pyproj converts recognized geographic CRSs with axis order fixed and
   ballpark transformations disabled. Unavailable grids, unknown transformation
   accuracy, out-of-area points and unsupported projected-coordinate notation
   are retained for review. Original values and the coordinate operation remain
   in the candidate table.

6. **Bound geographic research.** Prompts ask for one relevant major local
   language first and stop when resolved. English is conditional on failure;
   Chinese is conditional on regional/language relevance, not used for every
   record. Dates, collectors and historical place names disambiguate locations.
   Four targeted queries are a prompt instruction, not an enforced tool quota.
   Accessible aerial/terrain context is a fallback, not a guarantee of access to
   Google imagery or every vegetation map.

7. **Review species ecology after selection.** `--habitat` and general taxon
   ecology cannot move coordinates, rank candidates, change uncertainty or
   exclude records. They can flag a selected point for review. Recorded habitat
   on the actual collecting label remains valid locality evidence. Present-day
   vegetation need not reflect conditions at the collection date.

8. **Constrain route proposals.** Route refinement requires route evidence and
   a nearby mapped route name matching the collection text. It never snaps to
   an arbitrary nearest road. Anchor uncertainty, displacement and DEM resolution
   are propagated conservatively; unknown historical-route error remains explicit.
   A route proposal is not automatically accepted as the collecting point.

9. **Accept based on evidence.** Accepted candidates need documented support,
   a usable datum and defensible uncertainty. LLM self-confidence is retained
   for audit only. Candidate selection considers acceptance, provenance class,
   source-domain diversity and uncertainty, not a confidence cutoff or route-first
   preference. Source-domain counts do not prove factual independence. Conflicting
   documented coordinates trigger review rather than an automatic override.

10. **Separate uncertainty types.** `reportedUncertaintyMeters` preserves the
    model/provider assertion. `uncertaintyBasis` and `uncertaintyComponents`
    distinguish a rounding minimum, explicit measurement allowance, documented
    reference extent, datum transformation and unresolved components. A coordinate
    at the center of a verified locality extent can cover that extent but does
    not identify the exact collecting spot. Unknown total uncertainty is left
    blank in DwC rather than fabricated as 10 km. Printed-coordinate measurement
    allowances are explicit sidecar/user policies requiring independent justification.

11. **Expose separate dispositions.** `accepted`, `review`, `unresolved`,
    `excluded` and `failed` are recorded. Standard mode includes non-excluded
    rows with status; robust mode exports accepted rows only. Review points
    must not be silently treated as validated occurrences in an analysis.
    `--max-uncertainty-meters` is an optional analysis-specific acceptance limit.
    An ecological warning alone does not change the disposition.

12. **Preserve label elevation.** Printed metric values are retained, including
    metric decimals; feet are converted to meters while raw units remain in the
    transcription. Without label elevation, the selected WGS84 point can obtain
    a DEM estimate. Estimated values are reported to 10 m granularity. This is
    not a claim of a +/-10 m vertical error, and uncertain horizontal position
    can cause much larger elevation uncertainty.

13. **Reduce redundant LLM work.** Defaults prefer Sol/medium with separately
    configurable label and research models. Stage-specific JSON, text-only
    research, content-keyed caches and duplicate-request locking avoid repeated
    work. `--max-llm-calls` enforces a shared fresh-provider-attempt budget,
    including retries and availability fallbacks. Stage timeouts default to
    600/600/600 seconds. Neither timeouts nor the call budget bound tokens within
    an agent request. Reported successful-call token counts exclude failures and
    unreported usage. Parallelism is throughput, not token reduction; account
    limits and backoff take precedence over local worker heuristics.

14. **Keep five primary outputs.** Modified DwC CSV/TSV, combined candidate TSV,
    summary and JSONL log remain the deliverables. Candidate TSV holds raw label
    text, original/candidate coordinates, datum operations, sources, uncertainty
    and decisions. Logs hold actual model, complete prompt, response-cache key,
    code/prompt/input/image hashes, timestamps and stage errors. Bounded source
    snapshots and geospatial request/response snapshots are private cache files,
    not additional primary exports. HTTP reachability is not locality validation.

15. **Resume without confusing failure and absence.** An append-only, flushed
    journal checkpoints each completed record before final table export. Matching
    code, inputs, image bytes and settings permit replay. Interrupted journal
    tails are tolerated. Transient stage/service failures remain retryable;
    successful stage responses remain reusable. Per-record progress bars and
    model names remain visible. The collector/curator handoff preserves its
    explicit y/n continuation decision after partial collection failure.

16. **Evaluate before claiming improved accuracy.** `benchmark_georeferences.py`
    prepares masked inputs and a separate truth table, and compares named
    prediction tables. Related collecting events and shared locality strings
    stay within the same tuning/evaluation split. Coordinate-bearing locality
    strings are excluded to avoid obvious leakage. Evaluation reports median,
    mean, p90/p95, maximum, large errors, failure rate, threshold success rates,
    defensible-radius coverage and optional reported tokens/call time. A secondary
    trimmed statistic is labeled separately; the primary analysis retains outliers.

## Benchmark Commands

Run these from `llm_georeference_curator/` after setup. The input must contain
independently documented GPS/verified coordinates, known datum and locality
descriptions. The tool checks metadata, but cannot certify the reference itself.
Manually curated coordinates without independent GPS validation are useful
for review comparisons, not automatically error-free ground truth.

```bash
.venv/bin/python scripts/benchmark_georeferences.py prepare \
  --input reference_dwc.tsv \
  --output output/benchmark
```

Ground truth is outside both model input directories. Never copy it into a model
workspace or prompt. Preparation retains only record identifiers and the four
hierarchical text fields in the masked DwC, excluding images, taxon, habitat,
original coordinates and coordinate provenance. Identifiers are not included
in the baseline prompt. Frozen grouping, split and seed are recorded in the
manifest. Check split sizes before use; tiny/grouped datasets can have an empty
split. Tune settings only on tuning records and freeze them before evaluation.

```bash
bash run_llm_georeference_curator.sh \
  --input output/benchmark/evaluation \
  --output output/benchmark/baseline \
  --georeference-method xie-baseline \
  --llm-mode on --llm-model gpt-6.1-sol --llm-reasoning-effort medium

bash run_llm_georeference_curator.sh \
  --input output/benchmark/evaluation \
  --output output/benchmark/enhanced \
  --georeference-method evidence-assisted \
  --llm-mode on --llm-model gpt-6.1-sol --llm-reasoning-effort medium

.venv/bin/python scripts/benchmark_georeferences.py evaluate \
  --truth output/benchmark/ground_truth.tsv \
  --prediction baseline=output/benchmark/baseline/georeference_candidates.tsv \
  --prediction enhanced=output/benchmark/enhanced/georeference_candidates.tsv \
  --run-log baseline=output/benchmark/baseline/georeference.log.jsonl \
  --run-log enhanced=output/benchmark/enhanced/georeference.log.jsonl \
  --output output/benchmark/evaluation_metrics.json
```

Candidate tables, not just robust DwC, are evaluated: a baseline point can be a
prediction while still lacking independently justified uncertainty. Failure
rates and threshold-success fractions use all reference records as denominator.
Uncertainty coverage uses only candidates with a defensible basis; it compares
the reference point with the predicted radius without silently expanding the
radius by reference error. Reference uncertainty is recorded separately. Inspect
its effect when interpreting coverage. Report sample size and strata such as
language, label quality, geography, time period and locality specificity.

The paired text comparison does not measure image-transcription accuracy. For
the full workflow, separately validate transcription against human-reviewed
labels and reserve held-out image specimens. Model training-data contamination
cannot be excluded merely by masking coordinates. Web lookup may retrieve the
original occurrence; do not count an evaluation that simply rediscovers hidden
ground truth as independent locality-reconstruction evidence. Inspect sources
and exclude/label such leakage. The current tool does not automatically detect
all online leakage or compute paired confidence intervals.

## Suggested Methods Description

Adapt to what was actually run; record release/commit, protocol, model returned,
reasoning effort, dates, acceptance threshold, manual review and measured results.

> Locality descriptions were georeferenced using the Xie et al. (2025)-inspired
> structured-locality approach implemented in VASCULUM, with additional specimen
> label transcription and source-traceable geographic research. Original
> coordinates, candidate locations, coordinate-reference-system transformations,
> uncertainty components, prompts, model settings and source snapshots were
> retained to support auditing and cached replay. Species-wide ecological
> expectations were used only to flag records after coordinate selection and
> did not determine candidate coordinates or acceptance.

Do not write "all occurrences were corrected to within 10 km" or "the extension
is more accurate" unless an appropriate independent experiment supports that
specific statement. Methodological similarity, auditability and reproducibility
are valuable properties, but do not replace positional validation.

## References

- [Xie et al. (2025), original article](https://doi.org/10.1038/s41477-025-02162-y)
- [Xie et al., supplementary methods and comparison](https://static-content.springer.com/esm/art%3A10.1038%2Fs41477-025-02162-y/MediaObjects/41477_2025_2162_MOESM1_ESM.pdf)
- [GBIF georeferencing best practices](https://docs.gbif.org/georeferencing-best-practices/1.0/en/)
- [PROJ/pyproj transformation selection](https://pyproj4.github.io/pyproj/3.6.1/api/transformer.html)
