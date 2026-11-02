# Photo-First Georeferencing Protocol

## Scope

The curator improves occurrence coordinates from original specimen photographs,
while preserving documented existing coordinates. DwC is metadata and auxiliary
context, not a replacement for unreadable or missing original label evidence.

The locality-research approach is inspired by
[Xie et al. (2025)](https://doi.org/10.1038/s41477-025-02162-y).
Image reading, stage caching and explicit evidence checks are implementation
choices, not an exact replication of that experiment. No new positional-accuracy
measurement is claimed. Consult
[GBIF georeferencing best practices](https://docs.gbif.org/georeferencing-best-practices/1.0/en/)
for precision, datum, provenance and uncertainty concepts.

## Routing

Manual miss_ exclusions take precedence over every other route.

1. Existing coordinates with documented provenance, datum, uncertainty and no
   detected inconsistency are retained. No LLM transcription/replacement search
   is performed, regardless of photo availability.
2. Otherwise a linked local source photograph is required. Read its collecting
   label using the whole image, keeping determination/accession/barcode labels
   separate. Parse detailed printed coordinates before geographic research.
3. Without that photograph, exclude the specimen from final DwC even if DwC
   contains a detailed locality or verbatim coordinate string. A remote URL,
   missing file or transcription without its photo does not satisfy this rule.
4. If the image is unreadable, record review status. If a completed reading
   establishes only broad locality, exclude it. If no locality can be read, keep
   it for review. Technical failures are retryable failures. None of these
   cases falls back to a more specific DwC locality.

Only locality actually read from the photograph can initiate coordinate
research. DwC country, dates and other metadata help disambiguate that
evidence. Search stops when sufficient evidence is available; it does not
repeat every language or every geographic layer.

## Acceptance

The same acceptance criteria apply to all specimens. Final DwC contains
accepted records only. review, unresolved, excluded and failed records remain
in the combined candidate TSV and log with reasons; they are not silently
treated as validated occurrences.

Decimal precision, high model confidence, a reachable URL, fine map resolution
or proximity to a path does not prove accuracy. Unknown datum/uncertainty remain
unknown. Printed coordinates need an independently justified measurement
allowance in addition to rounding error. Conflicting documented points require
review. Local gazetteer verification is an explicit user assertion; a name
match alone is not verification.
For existing points the implemented checks require valid nonzero coordinates,
a recognized datum/available transformation, positive reported uncertainty at
least as large as the rounding minimum, and GPS/GNSS provenance or an explicit
verification status. Reported geographic issues and approximate/centroid
provenance prevent automatic trust. Country/state bounds are checked only when
a reference-regions table is supplied; there is no implicit global boundary
verification. These checks cannot discover every incorrect source assertion.
For a supplied gazetteer, uncertaintyMeters is the user-justified total
point-radius uncertainty (including feature extent and source/map error), not
map resolution or coordinate rounding. Optional boundingBox is
[south, west, north, east] in the supplied geographic datum; it is a minimum
coverage check and never substitutes for a justified total uncertainty.

A named route can support a proposal only if the collecting text names it;
route geometry must not shrink documented uncertainty. Species-wide habitat
is advisory after selection and cannot move/rank/reject coordinates.
Label elevation takes precedence; feet are converted. DEM estimates use
10 m reporting granularity, not a 10 m accuracy claim.
Habitat warnings use heuristic rules with current OpenStreetMap tags and DEM,
not a validated species niche model or a historical vegetation reconstruction.

## Audit And Replay

The default artifacts are modified DwC CSV/TSV, one combined transcription/
candidate TSV, summary, JSONL log and one ribbon PDF. The diagram uses actual
decisions, fixed source grays and 50% opacity. Missing collection provenance
is omitted, not invented. Original data are untouched. Inputs,
images, implementation/settings, actual model calls, source references,
timestamps and errors are recorded. Checkpoints and exact-response caches
reduce repeated work; retryable failures are not treated as completed records.
Configured evidence layers are distinct from sources actually used for a
selected point; enabled services are not automatically listed as consulted
georeference sources. Retained original provenance is preserved.
The source groups in the ribbon are retained representative portals after
deduplication, not an allocation of each merged specimen to every contributing
portal. Counts are numbers of records/specimens at each stage, not taxon counts.
Source evidence is the trusted-original route or completed usable label gate;
its loss branch also includes technical failures before that gate. The final
loss branch includes review/unresolved/failed results. "Accepted" means passed
this protocol, not independently proven true coordinates.

Cached replay can reproduce stored results. Fresh model calls and changing web
sources are not guaranteed to give identical outputs. This workflow has no
text-only comparator, benchmark generator, sampling-bias analysis or alternative
acceptance modes. Empirical accuracy claims require a separate validation study.
