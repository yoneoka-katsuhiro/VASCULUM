# VASCULUM CheckList v0.1.0

CheckList reverse-searches plant species from a geographic condition and
generates a visual checklist PDF from herbarium specimen records.

Species lists are built from GBIF preserved-specimen occurrence records.
Representative images are selected from herbarium specimen images only:

```text
GBIF image candidates
CVH top-up if fewer than ten valid candidates are available
maximum ten valid specimen-image candidates
technical QC
Codex-assisted diagnostic selection
highest-resolution image from the selected specimen record
PDF
```

CVH is used only as a representative-image candidate source after the GBIF
species list has been built. It is not used as regional evidence for deciding
which species are included.

## Requirements

- macOS or Linux
- Python 3.9 or newer
- A contact email for polite HTTP identification
- Codex CLI access for AI-assisted specimen-image selection

## Setup

```bash
bash setup_mac.sh
cp .env.example .env
```

Set `CONTACT_EMAIL` in `.env`. The email is used only in the HTTP User-Agent
for polite database access. Do not commit `.env`.

## Dry Run

```bash
bash run_checklist.sh --dry-run \
  --country "Japan" \
  --taxon "Ferns"
```

## Run

Country search:

```bash
bash run_checklist.sh \
  --country "Japan" \
  --taxon "Ferns" \
  --title "Japan Fern CheckList"
```

Coordinate-radius search:

```bash
bash run_checklist.sh \
  --latitude 35.0 \
  --longitude 138.0 \
  --radius 50 \
  --taxon "Ferns" \
  --title "Regional Fern CheckList"
```

## Representative Images

Representative images are searched as GBIF preserved-specimen images first. If
fewer than ten valid GBIF herbarium specimen-image candidates are available for
a species, CVH is used to top up the candidate set to a maximum of ten.

For coordinate-radius searches, CheckList bulk-fetches GBIF
preserved-specimen image records within the preferred `--image-radius-max`
scope and assigns candidates to species. This is a ranking and search-priority
scope, not a rejection threshold. More distant specimens may still be used when
nearer valid specimen images are unavailable.

Technical QC only removes download failures, broken files, duplicates, obvious
placeholders, and images whose metadata clearly indicates they are not
herbarium specimen images. Biological or diagnostic image choice is left to the
Codex-assisted selector.

After Codex chooses the best specimen record, the PDF uses the highest-quality
image available for that same source record. Final PDF images follow the
VASCULUM standard profile:

```text
maximum edge: 2400 px
JPEG quality: 88
preserve aspect ratio: yes
crop: no
upscale: no
```

Before Codex image assessment starts, CheckList verifies that the existing
VASCULUM-style Codex CLI selector is available and asks for explicit
confirmation:

```text
Species found: XXX
Adaptive image evaluation mode: SMALL / MEDIUM / LARGE
Initial candidate images per species: 10 / 5 / 3
Maximum candidate pool per species: 10
Estimated maximum initial Codex image evaluations: XXX

Continue? [Y/N]:
```

The initial Codex batch size is adaptive:

```text
<= 200 species: 10 images per species
201-500 species: 5 images per species
> 500 species: 3 images per species
```

The candidate pool remains up to ten valid herbarium specimen images per
species regardless of the initial batch size.

## Geographic Priority

Geographic priority is used for candidate ranking, not hard rejection.

Candidates with coordinates keep real `distance_km` from the checklist search
center. Structured administrative matches are kept separately as
`administrative_match`. CheckList does not invent pseudo-distances for
coordinate-free records.

Administrative matches are generated only from administrative fields explicitly
provided by the user as search conditions. Free-text `locality` is not
interpreted or geocoded in v0.1.0.

## Output

Outputs are written to `output/checklist/<date>_<title>/` by default.

```text
species_list.csv
species_list.tsv
species_list.md
records.csv
checklist.pdf
summary.txt
```

The PDF contains:

- cover page
- search-area map page with local and regional background maps
- taxonomic species-count page
- visual checklist pages with up to four portrait specimen cards per page
- scientific-name index with page numbers
- search/provenance page

A page never mixes families. If a family has fewer than four cards on its final
page, the remaining slots stay empty and the next family starts on a new page.

Families are sorted by the reusable order in
`config/taxonomic_family_order.json`; genera and lower ranks are sorted
alphabetically within that family order. The supplied fern order follows PPG I
and includes Arthropteridaceae near Tectariaceae and Cryptocaulaceae near
Nephrolepidaceae.

Generated files include:

```text
Generated with VASCULUM — CheckList
```

## Terminal Progress

All long-running stages print `running...` status lines. Species-level work
prints a progress bar, for example:

```text
running... [||||||||||||................] 45/114 species
```

## Exit Codes

- `0`: completed without retrieval or image errors
- `1`: fatal configuration or runtime error
- `2`: invalid command-line usage
- `3`: output completed, but one or more source or image operations failed
- `130`: interrupted by the user

## Licensing

Metadata and images remain subject to the licenses and terms supplied by their
source datasets and institutions. Review `license`, `rightsHolder`,
`references`, and source record pages before publication or redistribution.
See `CITATIONS.md`.
