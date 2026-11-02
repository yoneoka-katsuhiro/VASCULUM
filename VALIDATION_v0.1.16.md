# v0.1.16 Validation

Date: 2026-11-02. Software/regression checks are distinct from empirical
georeferencing-accuracy validation. See the versioned methodology for exact
adoption and uncertainty assumptions.

## Tests

- 101 curator unittest cases, six collector unittest cases and the collector's
  additional offline assertions; shell syntax, compilation and CLI entry checks.
- Release checks passed in a separate clean staging tree with newly created
  Python environments. No installed research folder was overwritten.
- PE00684280 with partially illegible township locality is accepted with a
  point-radius/low confidence; E01539315 adopts 27.619417, 96.981778 without
  an LLM call, with blank-datum assumptions explicitly recorded.
- Positive LLM uncertainty and place_centroid estimates are accepted. Unknown
  provenance/datum do not produce review/unresolved states. Optional maximum
  uncertainty filters after best-point selection.
- Geography, label height/imperial conversion, extent coverage, immutable
  species-wide habitat warnings, source provenance, personal records, miss_
  exclusion, image-export protection, ribbon provenance and real R rendering
  retain regression coverage.
- Byte-identical v0.1.15 transcription contract fingerprint is checked. A
  successful reading followed by a research timeout is reused on retry without
  a second transcription call. Invalid empty geographic responses do not remain
  cached as successful estimates and block a fresh retry.
- Real subprocess tests send SIGINT to the parent and separately to one of two
  active provider subprocesses. Both cases stop the whole CLI with exit 130,
  stop the other provider, do not begin the two remaining records and leave no
  completed final DwC output.

## Live Two-Specimen Check

Existing local photographs/DwC and a copy of the old private transcription
cache were used; original inputs/cache were not modified. Codex CLI
gpt-6.1-sol/medium performed one fresh geographic-research call in approximately
85 seconds. Fresh transcription calls: zero. Both records were accepted;
there were no failed/excluded records or run errors.

| Specimen | Latitude | Longitude | Radius (m) | Confidence | Verification |
| --- | --- | --- | --- | --- | --- |
| E01539315 | 27.619417 | 96.981778 | 6000 | low | unverified |
| PE00684280 | 28.765000 | 96.736000 | 166319 | low | unverified |

The former's radius includes the explicit unstated-position/datum allowances.
For the latter, the model chose a collecting-era administrative-seat proxy
and reported a broad modern-township extent. Full-extent coverage enlarged
its model-estimated 120000 m radius to 166319 m. This is not evidence of a
precise historical collecting point; the large radius is intentional disclosure
of uncertainty. The historical interpretation was not independently validated.
No labels were reread, cropped or upscaled for this test.

A repeat run after the final code changes also accepted both records, with
zero fresh transcription calls, zero fresh geographic-research calls, two
response-cache hits and no errors.

## Environment And Limits

Local macOS checks used Python 3.9.6, pyproj 3.6.1/PROJ 9.3.0, Shapely 2.0.7,
R 4.5.1, ggplot2 4.0.1, ggalluvial 0.12.6 and Codex CLI 0.160.0. GitHub Actions
is configured for Python 3.11/Linux, not claimed as already remotely executed.

These checks demonstrate routing, integration, interruption and cache reuse,
not error distributions across countries, languages, handwriting, historical
geography or SDM performance. No per-record 10 km guarantee, improved accuracy
relative to Xie, or universal validity of policy allowances is claimed.
accepted/unverified is a best estimate for the user's final all-record check.

The distribution is checked for ZIP integrity, included source files and basic
private-path/credential patterns. Credentials, live outputs, local environments,
research images and response caches are excluded. Natural Earth references are
public-domain source data; regression fixtures are not ground-truth datasets.
