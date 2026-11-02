# VASCULUM v0.1.17

Focus: coarse existing coordinates, Claude as an LLM provider, and a readable
terminal. CheckList keeps its own component version (v0.1.2) and is unchanged.

## Georeferencing

- **Coarse existing coordinates are re-estimated from the label.** In v0.1.16
  every plausible existing coordinate was kept without an LLM call. That also
  kept regional placeholders, such as the point 23.51667, 120.8 that MNHN
  assigned to Taiwan specimens collected on different dates. An existing point
  is now kept as it is only if it is documented as GPS, or its rounding or
  stated radius is at most 1 km and no record with a different collecting date
  shares it. Otherwise the label photograph is georeferenced, and the old point
  is kept only as a low-confidence fallback (no photo, or no finer locality).
- **Degree-minute values are recognised.** 23.51667, 120.8 is 23°31′, 120°48′,
  so its rounding radius is now about 1.3 km instead of the 5 km of a 0.1° step.
- **Datum allowance reduced.** A missing datum adds 1000 m (was 5000 m) and no
  longer forces low confidence; the remark `datum not stated; assumed WGS84`
  stays. Arc-second coordinates printed on a label are now high confidence.
- A technical failure during locality research is retried instead of silently
  falling back to a coarse existing point.

## Claude provider

`--llm-provider claude --llm-model opus` runs label reading and locality
research through the Claude Code CLI with your Claude login (no API key).
Images are opened with Claude Code's Read tool; web research uses WebSearch and
WebFetch; answers are validated against the same JSON schema. Run
`claude auth login` once. The CLI bundled with the Claude desktop app is found
automatically when `claude` is not on `PATH`.

## Terminal

- One aligned line per finished specimen: result, confidence, radius, basis
  (where the point came from), time, tokens and locality.
- One live status line on a terminal (progress, elapsed, estimated time left,
  tokens, failures); omitted when output is redirected.
- A three-line run summary. Stage-level messages only with `--verbose`.
- All progress goes to stderr; stdout carries only `Output: <folder>`.
- `setup_mac.sh` and `check_release.sh` print one line per step and a single
  test count, showing test output only on failure.

## Documentation

READMEs rewritten (purpose, requirements, copy-paste quick start, example
terminal output, outputs table, radius components, limitations). Added
`CHANGELOG.md` and `CITATION.cff`; release notes moved to `docs/releases/`.

## Upgrading

Unzip into a new folder, run `bash setup_mac.sh`, copy
`herbarium_specimen_collector/output/<taxon>/` from the old folder and, to reuse
earlier label readings, `llm_georeference_curator/.cache/llm_georeference_curator/`.
The label-reading prompt is unchanged, so cached readings stay valid; record
decisions are recalculated under the new rules. See
[validation](VALIDATION_v0.1.17.md) for the checks performed.
