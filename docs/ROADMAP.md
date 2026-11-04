# Improvement ideas / 今後の改善案

These are proposals, not claims that the features are implemented. The current release prioritizes reproducible bug fixes over a broad rewrite.

| Priority | Feature | Benefit | Acceptance criterion |
| --- | --- | --- | --- |
| 1 | Durable specimen-review ledger | Store exclusions and comments by stable specimen ID, independently of filenames and portal ordering. | A renamed or newly ordered image retains the user's decision; conflicting decisions are visible and reversible. |
| 1 | Replayable research snapshots | Keep raw responses, query parameters, retrieval times and checksums with an optional offline replay mode. | An archived run can regenerate its tables without contacting portals; its scope and missing responses are explicit. |
| 1 | Atomic output snapshots | Finish in a staging directory and retain previous tables when a run fails; add a machine-readable run manifest. | Interruption at any write step preserves the last complete result and labels partial output clearly. |
| 2 | Structured coordinate quality report | Flag impossible values, uncertainty, datum gaps and suspicious localities without silently moving coordinates. | Original values remain intact; every proposed issue is linked to evidence and a reproducible rule. |
| 2 | Cross-platform review interface | Let researchers inspect a photo, specimen metadata and original source side by side, with include/exclude controls. | The same review ledger works in the CLI and interface; no decision is lost between platforms. |
| 2 | Manual image selection and explicit model budgets | Make checklist production possible without AI; show expected candidate calls and a configurable ceiling when AI is selected. | A no-AI run produces the same evidence table; the model budget stops further calls and labels unfinished selections. |
| 3 | Antimeridian/polar search support | Split spherical search regions into API-safe polygons and deduplicate overlapping result sets. | Boundary fixtures around ±180° and poles agree with independently calculated distances and contain no complementary-area error. |
| 3 | Scheduled adapter contract checks | Detect portal schema changes with tiny, rate-limited representative queries. | A failing adapter has a clear status and saved diagnostic response; release tests remain offline and deterministic. |

## 日本語

最優先は、**ファイル名に依存しない確認台帳**、**生応答を残した再実行可能な記録**、**前回の完了結果を壊さない出力保存**です。研究者の判断と解析の来歴を長期間保持できます。

次に、座標を勝手に補正しない品質レポート、写真と原記録を並べる確認画面、AIなしの画像選択と明示的なモデル利用上限が有用です。地域外の代表写真と地域内の分布根拠も、画面上で明確に区別できます。

日付変更線・極付近の検索と、検索先の仕様変更を知らせる定期チェックは、境界条件の検証と通信量への配慮を含めて実装します。新しい機能ごとに、上表の受け入れ条件を満たす小さな回帰テストを追加します。
