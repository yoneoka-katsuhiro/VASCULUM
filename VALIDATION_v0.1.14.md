# v0.1.14 Validation Record

Validation date: 2026-11-01. This is a software-release check, not a measured
georeferencing-accuracy study or certification for a particular analysis.

## Checks

- `bash check_release.sh`: shell syntax, Python compilation, collector offline
  assertions, 94 unittest cases across collector/curator, CLI entry points and
  a basic credential-pattern scan.
- A separate distribution copy completed a fresh `bash setup_mac.sh`, including
  new isolated Python environments, R-package preparation and the release suite.
- Collector-to-curator tests check that supplied datum, uncertainty and GPS
  provenance survive export; unrelated taxonomy issues do not invalidate the
  coordinates. Geographic issue flags remain operative.
- Duplicate merging cannot combine a latitude/longitude pair or its trust
  metadata from different records. Data and media rights remain separate.
- Photo-first routing, `miss_` exclusions, personal additions, ambiguous label
  keys, conflicting collecting labels, checkpoints, failure retries, rate-limit
  backoff and image-export source protection have regression coverage.
- Candidate acceptance tests distinguish decimal formatting, positional
  uncertainty and provenance. Printed elevations in feet are converted;
  distances from a village/river are not treated as elevations.
- Synthetic integration: four collector records, including one with no photo
  or trusted coordinate, produce three accepted DwC rows and the six required
  outputs. Source counts in the ribbon agree with the actual decisions.
- R rendering covers empty input, all-unaccepted input, very small final
  counts, no losses, and collection/deduplication provenance. PDF pages were
  rendered and visually inspected. The main figure is vector artwork with
  embedded fonts and approximately 50% PDF fill opacity (8-bit device rounding).
- One live Codex CLI image-transcription smoke test used a clearly marked
  synthetic label, `gpt-6.1-sol`, medium reasoning and no web search. The printed
  coordinates and `6500 ft` were read correctly. Reported usage was 13,284 input
  and 144 output tokens. This checks image transport/JSON output, not real
  specimen readability or geographic research accuracy.
- Distribution checks exclude local environments, authentication, caches,
  generated outputs and local Git history. Source text was scanned for obvious
  secrets, private email addresses and user-specific filesystem paths.

## Environment

Local checks used macOS, Python 3.9.6, pyproj 3.6.1 / PROJ 9.3.0, R 4.5.1,
ggplot2 4.0.1, ggalluvial 0.12.6 and Codex CLI 0.160.0. The R packages emitted
built-under-R-4.5.2 warnings but rendered successfully on R 4.5.1. The configured
GitHub Actions suite covers Python 3.11/Linux after upload; a remote CI run has
not been claimed here. Runtime and renderer versions/hashes are also recorded
for actual curator runs.

## Scientific Limits And Study Decisions

This workflow is inspired by Xie et al. (2025), not an exact replication of
their model, input preparation or evaluation. It has not demonstrated lower
positional error than that study. Neither six decimal places nor 10 m elevation
reporting implies corresponding empirical accuracy.

Model-only coordinate estimates and model-reported uncertainty remain review
proposals. Final DwC contains records that pass the documented evidence policy.
The default imposes no universal upper horizontal-uncertainty threshold;
`--max-uncertainty-meters` must reflect the intended analysis. Unknown datum or
unjustified total uncertainty is not silently repaired. These are study-level
choices to settle before defining the publication dataset.

The synthetic tests do not assess handwritten/multilingual label accuracy,
all live portal adapters, web-source correctness, historical locality matches,
DEM vertical error or resulting SDM performance. An independently checked
specimen set is needed before making a quantitative accuracy claim. Preserve
its original evidence and evaluation protocol separately; no benchmark generator
or additional analysis feature is added to this pipeline.
