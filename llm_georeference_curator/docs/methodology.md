# Automatic Point-Radius Protocol v0.1.16

## Scope And Interpretation

VASCULUM adopts one best coordinate and a positive uncertainty radius for an
eligible specimen. The point-radius concepts follow Chapman & Wieczorek (2020),
[Georeferencing Best Practices](https://doi.org/10.15468/doc-gg7h-s853).
LLM locality research is inspired by
[Xie et al. (2025)](https://doi.org/10.1038/s41477-025-02162-y).
The automatic acceptance rules, datum/error defaults and LLM confidence below
are explicit VASCULUM extensions, not a claim of exact replication or measured
positional accuracy. Cite this versioned protocol as well as those references.

accepted means machine-adopted. All final DwC records remain unverified until
the user checks them. confidence is high/medium/low and is qualitative, not a
calibrated probability or a replacement for coordinateUncertaintyInMeters.
The software never uses review/unresolved record states.

## Routing And Exclusions

1. A miss_ mark on any input view excludes the whole specimen first.
2. Plausible existing decimal coordinates are adopted without an LLM call,
   irrespective of image availability. Coordinates are checked for range,
   zero placeholders, geographic conflict and recognizable placeholder patterns.
   A unique valid latitude/longitude swap can be corrected locally.
3. Otherwise use a local whole specimen photograph. Read the collecting label,
   keeping later determinations, accession labels and different events separate.
   Convert sufficiently detailed printed coordinates before locality research.
4. Use the finest legible locality for geographic research. An illegible suffix
   is not invented, but readable county/township/village/mountain/river/pass/road
   information still receives a best estimate. Source country, date, collector
   and other DwC fields are auxiliary context, not fabricated label text.
5. Exclude only a country/admin-1-only locality; no photograph and no usable
   coordinate; a miss_ mark; or an obvious geographic contradiction that cannot
   be repaired. With an explicit --max-uncertainty-meters, an additional
   analysis-specific radius filter applies after selecting the best point.

Technical failures are failed and retryable: provider/network timeout, missing
required reading/point response, invalid JSON/coordinates, unavailable provider
or failed CRS transformation. A successful partial reading is not a failure.
A wholly unreadable image has not supplied the required reading and is a
retryable reading failure, not an additional semantic exclusion category.
No valid geographical evidence is fabricated to satisfy an output contract.

## Datum, Radius And Confidence

Original WGS84 coordinates retain their supplied decimal strings. Blank datum
is interpreted as WGS84 with "datum not stated; assumed WGS84" in remarks and
a 5000 m policy datum allowance added to the radius. An unrecognized named
geographic datum receives a separate explicit assumption note. Recognized
datums use pyproj/PROJ transformations; transformation error is added where
available, with a stated policy allowance where accuracy is unknown.

Reported original uncertainty is retained at least at the rounding minimum.
When positional error is absent, a 1000 m policy allowance is used. Printed
coordinates combine rounding error with a supplied measurement allowance or
the same 1000 m default. Assumptions lower confidence and are recorded as
components; they are not empirically established universal error bounds.

A named feature is represented by its center, a documented office or a
better supported collecting-era point. The radius covers the possible
collecting area from that point. LLM-reported radius is allowed without an
independent-verification veto. Available bounds enlarge an inadequate radius;
a point outside its own bounds is replaced by the center, with low confidence.
A missing radius receives an explicitly marked low-confidence 50000 m policy
allowance. Uncertainty/confidence assumptions never imply measured accuracy.

Conflicting plausible evidence lowers confidence and may enlarge the radius
to cover alternative locations. A contradicted country/state location is not
preferred merely because it reports a smaller error. Optional radius filtering
is performed after selection, not by choosing a false precise alternative.

## Geographical Checks

Bundled Natural Earth v5.1.2 country outlines, admin-1 bounding boxes and
capital points are conservative current references. Country/state conflicts
require a 25 km margin. Borders near the tolerance, missing island detail,
marine/island context and unknown place aliases are not claimed to be resolved
by this coarse map. Explicit source/institution coordinates can identify
institutional placeholders; no complete global herbarium-location registry is
claimed. Optional reference-regions tables add user-supplied country/state checks.

Historical names and relocated settlements use collecting-era evidence, with
the selected interpretation in georeferenceRemarks. Current boundaries do not
establish historical boundaries. URLs/snapshots establish traceability, not
independent factual verification of a model's interpretation.

## Elevation And Ecology

Printed elevation takes precedence, preserving metric values and converting
feet to metres. Distances from roads/villages/rivers are not elevations.
For a locality with bounds and label elevation, a 5x5 Open-Meteo/Copernicus DEM
sample may select an elevation-compatible point within those bounds. The
automatic step retains full-feature radius: sparse samples alone cannot prove
that all possible sites outside a smaller radius have been eliminated.
DEM estimates use 10 m reporting granularity, not a 10 m accuracy guarantee.

Species-wide --habitat remains a post-selection warning only, with no effect
on point choice, ranking, radius or adoption. Current OpenStreetMap/DEM
heuristics do not reconstruct historical vegetation or prove plant identity.

## Audit, Replay And Interruption

Keep original DwC/images, the six primary outputs and private caches. Logs
record original/modified coordinates, confidence, uncertainty components,
model/prompt/response provenance, code/input/image hashes, sources and errors.
The ribbon conserves actual GBIF/CVH/Other counts, with exclusions/failures
terminating instead of reappearing at later stages. A deduplicated specimen
belongs to its retained representative portal for this diagram.

The v0.1.15 whole-image transcription prompt, schema and exact-response key
are unchanged. New adoption rules invalidate old completed-record decisions,
not successful matching label readings. Failed research retries reuse the
reading. Completed checkpoints survive Ctrl-C. Interruption of the parent or
a provider on SIGINT stops all workers/provider process groups with exit 130.

Cached replay can reproduce stored responses; fresh LLM/web calls are not
guaranteed identical. Quantitative accuracy and SDM performance need a separate
independently checked evaluation set. This release adds no benchmark generator
or extra bias-analysis output.
