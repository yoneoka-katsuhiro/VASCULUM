# VASCULUM

**Find herbarium specimens, keep their evidence, and build illustrated plant checklists.**

[日本語](README_ja.md) · [English manual](docs/MANUAL_EN.md) · [Download](https://github.com/yoneoka-katsuhiro/VASCULUM/releases/latest) · [Changes](CHANGELOG.md)

VASCULUM searches public herbarium databases and keeps specimen photographs linked to their source records. Its collector merges repeated portal records of the same specimen and exports Darwin Core tables. Its checklist tool builds a regional species list from GBIF records and prepares an illustrated PDF for human review.

Version **0.1.19**. The name refers to a botanist's collecting case: a virtual case for specimen evidence.

## See the output

These are actual pages from the existing southern Philippines fern example in this repository. The specimen photographs and their source references appear together in the generated checklist. This gallery illustrates a previous run; it is not a new validation of the records or identifications.

<p>
<a href=".github/assets/checklist-gallery/philippines_fern_checklist_cover.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_cover_thumb.png" width="220" alt="Cover of an actual southern Philippines fern checklist"></a>
<a href=".github/assets/checklist-gallery/philippines_fern_checklist_hymenophyllaceae.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_hymenophyllaceae_thumb.png" width="220" alt="Actual Hymenophyllaceae page with specimen photographs and references"></a>
<a href=".github/assets/checklist-gallery/philippines_fern_checklist_index.png"><img src=".github/assets/checklist-gallery/thumbs/philippines_fern_checklist_index_thumb.png" width="220" alt="Scientific-name index in the generated checklist"></a>
</p>

## Choose a tool

| Tool | You supply | You receive |
| --- | --- | --- |
| `vasculum-collect` | A scientific name, optional synonyms and source selection | `dwc.csv`, `dwc.tsv`, specimen images and a run summary |
| `vasculum-checklist` | A country or a geographic circle and a taxonomic group | Species tables, evidence records and an illustrated checklist PDF |

Both are **command-line tools**. Open Terminal on macOS or PowerShell on Windows. The collector does not require an AI account. The checklist additionally requires an installed, authenticated Codex CLI; it asks before starting image evaluation, which can incur usage charges. AI image ranking does not confirm a specimen's identification.

## Download and run

Open [GitHub Releases](https://github.com/yoneoka-katsuhiro/VASCULUM/releases/latest) and choose the asset for your computer:

| Computer | Release asset |
| --- | --- |
| macOS, Apple silicon | `VASCULUM-v0.1.19-macOS-arm64.zip` |
| Windows, 64-bit Intel/AMD | `VASCULUM-v0.1.19-Windows-x64.zip` |
| Other systems or development | Source code; Python 3.11 or newer |

Extract the entire ZIP. The standalone programs include Python and their Python dependencies. Codex CLI is a separate installation. These binaries are not notarized by Apple or signed with a Windows publisher certificate. For installation, checksum verification and operating-system prompts, use the [manual](docs/MANUAL_EN.md#installation).

From the extracted directory, validate a small request without network access:

```bash
# macOS (replace with your contact address)
./vasculum-collect --dry-run --taxon "Haplopteris mediosora" --contact-email "you@example.org"
```

```powershell
# Windows PowerShell
.\vasculum-collect.exe --dry-run --taxon "Haplopteris mediosora" --contact-email "you@example.org"
```

Remove `--dry-run` and add `--sources gbif --limit 10 --skip-images` for a small metadata trial. Real collection sends your contact address in the HTTP User-Agent to the queried services. Start a new output directory for each research run; for example, add `--output "results/mediosora-trial"`.

## Research methods and limits

- The collector merges records using specimen identifiers. It preserves coordinate and image provenance from one supplying record instead of mixing unrelated metadata. Review possible identity collisions when institutions reuse or inconsistently format catalogue numbers.
- Coordinates are portal-supplied. VASCULUM does not infer or correct coordinates, establish coordinate accuracy, or authenticate identifications.
- GBIF supplies checklist regional evidence. CVH supplies additional representative-image candidates; an image from outside the region does not establish occurrence inside it.
- Scan limits, incomplete digitization and portal failures make checklists incomplete. An absent record does not demonstrate biological absence. Check the summary and exit code `3` before using a result. Collector partial runs use `dwc.partial.*` and `summary.partial.txt` to preserve complete exports.
- Existing images are retained by default. `--prune-unreferenced-images` is an explicit cleanup option and never runs after a reported partial failure. Files prefixed with `miss_` retain the user's exclusion decisions.
- Circle searches that cross the antimeridian or a pole are rejected until split-geometry support is implemented.

[English instructions](docs/MANUAL_EN.md) and [日本語マニュアル](docs/MANUAL_JA.md) explain the workflow, outputs, review procedure and troubleshooting. [Audit findings](docs/AUDIT.md) separate verified behavior from remaining limitations; [the roadmap](docs/ROADMAP.md) proposes further improvements.

## Develop and verify

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt -c constraints-build.txt
.venv/bin/python scripts/check_release.py
.venv/bin/python scripts/build_release.py
```

Use `.venv\Scripts\python.exe` on Windows. [Build instructions](docs/BUILDING.md) explain the native release jobs. CI tests Python 3.11 and builds with Python 3.13 on macOS and Windows. Each platform runs the offline regressions and the frozen programs' help and configuration checks before producing an artifact.

The maintenance approach follows [ai-biocode-kata](https://github.com/dritoshi/ai-biocode-kata/tree/main/chapters): small changes, explicit contracts, regression tests, fixed environments and traceable results.

## Cite and reuse

Use [CITATION.cff](CITATION.cff) to cite this software and include its version. Cite the herbaria and datasets behind the records as described in [CITATIONS.md](CITATIONS.md). Check each image's own licence before redistribution; the software's MIT licence does not license third-party specimen images.

Software: [MIT License](LICENSE).
