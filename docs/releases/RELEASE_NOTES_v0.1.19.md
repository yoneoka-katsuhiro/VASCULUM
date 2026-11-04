# VASCULUM 0.1.19

This maintenance release preserves previous collection evidence more safely, rejects invalid geographic searches, repairs a missing-rank taxon-match crash, and avoids repeated JPEG recompression. It adds native macOS Apple silicon and Windows x64 CLI build jobs, bilingual illustrated documentation, and offline checklist/PDF regressions.

## Migration

- Python source installations now require **3.11 or newer** because of updated image-library dependencies.
- Existing images are retained by default. Use `--prune-unreferenced-images` only when deliberately cleaning a completed run; failed or partial runs never prune. Partial exports use `dwc.partial.*` and `summary.partial.txt`, preserving prior complete metadata. Back up existing research outputs before reusing a directory.
- A CVH-only checklist is rejected because GBIF supplies its regional occurrence evidence.
- Circles crossing the antimeridian or a pole are rejected. Use smaller separate searches until split-geometry support is available.
- Codex CLI remains an external, authenticated dependency for representative-image selection. No AI invocation is required for collector use or dry-runs.

Read the [English manual](../MANUAL_EN.md), [日本語マニュアル](../MANUAL_JA.md), [audit](../AUDIT.md) and [roadmap](../ROADMAP.md). Release files include a SHA-256 checksum and environment manifest. Check the successful platform CI jobs before distributing their artifacts.

## 日本語

過去の標本写真を初期設定で保持するように変更し、全面的な取得失敗による既存表の上書きを防ぎました。不正な座標・半径、日付変更線を跨ぐ検索、GBIF応答のrank欠落を検出・修正しています。写真の不要な再圧縮を止め、WindowsのフォントとCodex CLIの版差に対応しました。英日説明書と配布ビルドを追加しました。

ソース実行はPython 3.11以上が必要です。AIによる画像順位は同定の保証ではありません。解析では新しい出力先を使い、部分失敗の警告を確認してください。
