# Changelog

User-facing changes by release, following [Keep a Changelog](https://keepachangelog.com/).
Detailed notes and validation records for each release are in [docs/releases/](docs/releases/).

## [0.1.19] - 2026-11-05

### Fixed
- Preserve existing photos by default and prevent pruning after incomplete runs; preserve previous tables on complete collection failure and write partial results separately.
- Reject invalid geographic coordinates/radii and unsupported date-line or polar-crossing circles before querying.
- Handle GBIF taxon matches with a missing rank without crashing.
- Reuse already-sized JPEGs without recompressing them; resize through an atomic replacement.
- Use suitable Windows/Linux PDF fonts and respect fallback font sizes.
- Detect Codex CLI capabilities and resolve Windows native command paths.
- Correct checklist completion timing and overall version provenance.

### Added
- Offline checklist/PDF and collector-preservation regression tests.
- Native macOS Apple silicon and Windows x64 build jobs, standalone CLI packaging, checksums and build manifests.
- English/Japanese illustrated overviews and manuals, a maintenance audit and a prioritized roadmap.

### Changed
- Runtime dependency updates address known package advisories; Python 3.11 or newer is required.
- Cleanup is opt-in with `--prune-unreferenced-images`; CVH-only checklists fail validation.
- CheckList component version is 0.1.3. See [release notes](docs/releases/RELEASE_NOTES_v0.1.19.md).

## [0.1.18] - 2026-11-02

### Removed
- The LLM georeference curator (`llm_georeference_curator/`) and the combined
  wrapper `run_collect_and_georeference.sh`. Its coordinate estimates were not
  reliable enough for research use. Coordinates in the collector's DwC exports
  are those supplied by the portals, with their datum, uncertainty and
  georeferencing provenance when available. Use v0.1.17 if you need the curator.
- R is no longer required.

### Changed
- `setup_mac.sh` and `check_release.sh` cover the collector and checklist only.

## [0.1.17] - 2026-11-02

### Added
- `--llm-provider claude`: run label reading and locality research with Claude
  (for example `--llm-model opus`) through the Claude Code CLI and your Claude
  login, as an alternative to Codex. No API key is needed.
- `--verbose` for the curator, to print every processing stage.
- `CITATION.cff` and this changelog.

### Changed
- Coarse existing coordinates are no longer accepted as they are. A point with
  whole-arc-minute or coarser precision, or one point shared by specimens with
  different collecting dates (for example the MNHN Taiwan records at
  23.51667, 120.8), is now georeferenced again from the label photograph. The
  coarse point is kept only as a low-confidence fallback.
- Coordinates converted from degrees and minutes get a ±0.5′ rounding radius
  instead of being treated as 0.1° decimals (about 1.3 km instead of 5 km).
- A missing datum adds 1000 m to the radius instead of 5000 m and no longer
  forces low confidence. Arc-second coordinates printed on a label are high
  confidence.
- The curator terminal shows one aligned line per specimen (result, confidence,
  radius, source of the point, time, tokens, locality) and a single live status
  line, instead of several lines per processing stage. Messages go to stderr;
  stdout carries only `Output: <folder>`.
- `setup_mac.sh` and `check_release.sh` print one line per step and a single
  test summary, and show test output only when a check fails.
- READMEs rewritten; release notes moved to `docs/releases/`.

### Fixed
- A technical failure during locality research no longer falls back silently to
  a coarse existing coordinate; the record is marked failed and retried.

## [0.1.16] - 2026-11-02

- Records are only accepted, excluded or (technically) failed; review and
  unresolved states were removed. Sub-province localities always receive a
  best point and radius. Ctrl-C stops the whole run with exit code 130.
  See [release notes](docs/releases/RELEASE_NOTES_v0.1.16.md).

## [0.1.15] - 2026-11-02

- Ribbon summary figure (R/ggalluvial), evidence-based acceptance, provenance
  kept through duplicate merging, `miss_` exclusions, personal specimen inputs.
  See [release notes](docs/releases/RELEASE_NOTES_v0.1.15.md).

## [0.1.14] - 2026-11-01

- See [release notes](docs/releases/RELEASE_NOTES_v0.1.14.md).
