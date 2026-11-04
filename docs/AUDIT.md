# VASCULUM maintenance audit

Date: 2026-11-05. Target: VASCULUM 0.1.19 / CheckList 0.1.3.

This audit follows the design, testing, debugging, deliverables, documentation and pre-publication checklist chapters of [ai-biocode-kata](https://github.com/dritoshi/ai-biocode-kata/tree/main/chapters). Changes preserve the existing two-pipeline design. Tests reproduce concrete failure cases rather than merely checking the implementation's shape.

## Findings and repairs

| Severity | Previous behavior | Repair and evidence |
| --- | --- | --- |
| High: loss of previous evidence | Collector cleanup could delete unreferenced photos after an incomplete portal run. An all-source failure could replace previous metadata with empty exports. | Preserve images by default; require explicit cleanup and a complete run. Abort before writing outputs if there are errors and no collected records. Save partial results to separate `dwc.partial.*` and `summary.partial.txt` files. Regression tests retain prior images and tables during failures. |
| High: geographic false acceptance | A portal coordinate of `NaN` could pass a circular distance filter because a comparison with NaN is false. NaN/infinite radius was accepted. | Reject nonfinite radii and out-of-range/nonfinite coordinates; test NaN, infinity and geographic bounds, including valid zero coordinates. |
| High: geographic scope | A circle crossing ±180° could generate a polygon interpreted as a different region by the API. | Reject unsupported crossing geometry before requests, including dry-runs. Add date-line and antipodal-distance tests. Split geometry is a documented future feature. |
| Medium: runtime failure | A confident GBIF taxon match without `rank` referenced an undefined local variable. | Preserve missing rank as empty; regression uses a realistic partial API response. |
| Medium: image degradation | Every reused JPEG was recompressed even when no resize was necessary. | Reuse unchanged bytes; use a temporary file and atomic replacement when resizing. Repeated-reuse tests check exact bytes, including preservation after a simulated disk error and failed redownload. |
| Medium: Windows/Linux PDF typography | The fallback font ignored the requested size, and font lookup only considered macOS locations. | Add Windows/Linux fonts, a user-selected font path and a size-aware fallback. Test font-size behavior and render an actual PDF offline. |
| Medium: CLI portability | Image selection assumed edition-specific Codex options. Windows npm exposes a command wrapper. | Probe `exec --help` without calling a model, require core features, and only use supported optional flags. Resolve the native Windows executable; provide an actionable error if unavailable. |
| Medium: incomplete validation | A CVH-only checklist passed dry-run despite lacking a regional evidence source. Image search radius was unchecked. | Require GBIF and a finite positive image search radius before work. Regression tests assert zero network use and no output from dry-run. |
| Low: provenance | Checklist runtime was recorded before image selection; the overall version came from the checklist component file. HTTP User-Agent still identified 0.1.9. | Record completion after image processing; read the overall version from the repository/bundle root; update User-Agent. |

A separate dependency audit found vulnerable pinned Pillow, requests and urllib3 versions. Runtime pins were updated to Pillow 13.0.0, requests 2.34.2 and urllib3 2.8.0. The post-update dependency audit reported **zero known vulnerabilities at audit time**. This is a dated package-advisory check, not a guarantee against unknown vulnerabilities. Pillow 13 requires Python 3.11 or newer; installation checks and manuals now state that minimum.

## Verification

`python scripts/check_release.py` runs five independent test programs: the existing collector assertion suite, coordinate-provenance tests, manual-review tests, new data-preservation tests, and new checklist regressions. It also compiles source and exercises both CLI help and dry-run paths. The suites contain **19 unittest cases plus the existing multi-adapter assertion suite**; 13 unittest cases were added in this release. All passed locally with Python 3.13 and the updated runtime dependencies. Production scripts pass Ruff `--select F`, and `git diff --check` is clean. The package builder preserves dependency notices and the Python licence; its licence collector was exercised locally.

The checklist regression uses the real renderer to write a complete PDF and the actual table exporters. Its input is explicitly synthetic and is not used as biological evidence. README photos are pre-existing real output examples, not fabricated interface screenshots or newly collected specimens.

The native build workflow runs those checks on macOS and Windows, then runs help/dry-run checks on both standalone executables. The local macOS standalone build and help/dry-run smoke checks passed outside the host sandbox. The initial sandboxed attempt encountered an OS semaphore restriction; it was not counted as a successful smoke check. Subsequent safety changes must be rebuilt and checked in CI before distribution. Platform CI results and release attachments remain the release-specific source of truth.

## Scope and remaining limitations

- The audit inspected CLI boundaries, metadata merge/coordinate provenance, image reuse/deletion, geographic filters, PDF generation, packaging and configuration. The existing offline suite exercises multiple source adapters. It did **not** perform a live end-to-end collection across all 24 configured sources.
- No paid Codex evaluation was run. CLI help was inspected locally, and capability handling was tested with a fixture. Model accuracy and account-specific access are unverified.
- The existing method does not establish a taxon's biological identity, the completeness of a regional flora, or coordinate accuracy. AI image ranking is assistance for human review.
- Identifier normalization can still collide when a portal uses inconsistent catalogue numbers. Filename-based `miss_` decisions need a durable ledger if upstream metadata changes.
- Collector partial results use separate files, but complete successful reruns and checklist reruns can update existing output tables. Use separate output directories or backups; full transactional snapshots covering all outputs and interruptions are proposed in the roadmap.
- Raw API responses are temporary. Full replay of historical data is not currently supported.
- Portal services, their permissions and their data licences are external dependencies. Some legacy adapters retain configurable TLS compatibility settings; this release does not certify every remote service.
- macOS notarization and Windows publisher signing are not configured. Linux is tested as a source installation; an Intel Mac binary is not supplied.

## 日本語の監査要約

今回の重点は、**過去の写真・表を失う条件の修正**、**地理検索の誤判定防止**、**不足したAPI項目による停止の修正**、**再圧縮による写真劣化の防止**でした。WindowsのPDFフォントとCodex呼び出しの互換性を改善し、英日README・マニュアル、配布ビルド、出力環境の記録を追加しています。

全検索先への実通信、AIによる同定精度、地域フロラの完全性は今回の試験対象ではありません。検証済みのオフライン動作と、外部条件に依存する未確認事項を区別しています。追加機能は[改善案](ROADMAP.md)に整理しました。
