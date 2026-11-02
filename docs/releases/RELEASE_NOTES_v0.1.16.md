# VASCULUM v0.1.16

## Automatic Point-Radius Adoption

- Record states are accepted/excluded, with failed reserved for technical work
  that can be retried. review/unresolved record states have been removed.
- Eligible sub-admin-1 localities receive a best point-radius estimate even
  with an illegible suffix or an imprecise exact site. confidence high/medium/low
  records doubt without withholding the best coordinate.
- Final DwC adds confidence and curationStatus; georeferenceVerificationStatus
  is unverified for the user's final checks. Machine adoption is not proof of
  empirical accuracy or a calibrated confidence probability.
- Plausible existing decimal coordinates are retained without LLM calls.
  Missing datum assumes WGS84, adding a 5000 m policy allowance and an explicit
  note. Missing source positional error uses a stated 1000 m allowance.
- Country/state, placeholder, swap and national-center/capital screens use
  supplied evidence and compact offline Natural Earth references. These are
  conservative current maps, not a historical/institutional-location authority.
- Place representatives and LLM-estimated uncertainty are eligible. Bounds
  enlarge inadequate radii. A missing locality radius uses an explicit
  low-confidence 50000 m policy allowance, not a false precise value.
- Label elevation can choose a DEM-compatible point within locality bounds.
  Sparse automatic DEM samples do not justify discarding the rest of the named
  feature from uncertainty. Printed height takes precedence; DEM reporting is
  to 10 m granularity, not guaranteed 10 m vertical accuracy.
- An explicit --max-uncertainty-meters filters after best-point selection.
  By default there is no upper uncertainty threshold.

## Cache And SIGINT

- Whole-image transcription instructions, schema and key format remain byte
  compatible with v0.1.15. Preserve/reuse .cache/llm_georeference_curator or
  pass --llm-cache-dir. Geographic policy changes do not reread matching photos.
- Successful readings are reused when a geographic-research call fails and is
  retried. Old completed-record decisions are recalculated under the new policy.
- Ctrl-C on the parent, or SIGINT/exit 130 from a provider, cancels the whole
  analysis and owned provider process groups, including parallel workers.
  The CLI exits 130 instead of continuing to the next specimen. Completed
  caches/checkpoints survive; partial results are not presented as complete.

## Validation And Upgrade

Regression fixtures cover PE00684280 and E01539315 as accepted, plus actual
live geographic research using the former's saved transcription. See
[validation](VALIDATION_v0.1.16.md) for exact checks, live-run results and limits.
The code does not promise accuracy from six decimal places or acceptance alone.

Install into a new folder, run bash setup_mac.sh, retain the collector outputs
and previous private cache, and rerun curation. No research outputs, credentials,
local Python/R environments or private caches are included in this source ZIP.
Collection, personal additions, miss_ exclusions, source-tracking grayscale
PDF, progress display and optional final-image exports remain supported.
CheckList retains its independent v0.1.2 component version and functionality.
