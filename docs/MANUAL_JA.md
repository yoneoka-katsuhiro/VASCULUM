# VASCULUM 日本語マニュアル

[English](MANUAL_EN.md) · [概要](../README_ja.md)

本書はVASCULUM 0.1.19、CheckList 0.1.3を対象にしています。例に含まれるメールアドレスと出力先は、ご自身の値に置き換えてください。

## インストール

### macOS・Windows用の実行ファイル

1. [Releases](https://github.com/yoneoka-katsuhiro/VASCULUM/releases/latest)を開きます。
2. Apple silicon搭載Macでは`macOS-arm64.zip`、64ビットIntel/AMD版Windowsでは`Windows-x64.zip`を選びます。整合性を確認する場合は、対応する`.zip.sha256`も保存します。
3. ZIPのSHA-256を確認します。macOSでは`shasum -a 256 VASCULUM-v0.1.19-macOS-arm64.zip`、PowerShellでは`Get-FileHash .\VASCULUM-v0.1.19-Windows-x64.zip -Algorithm SHA256`を実行し、同梱チェックサムと比較します。
4. ZIP全体を書き込み可能な場所に展開し、そのフォルダーをターミナルまたはPowerShellで開きます。
5. macOSでは`./vasculum-collect --help`、Windowsでは`.\vasculum-collect.exe --help`を実行します。ダブルクリックで操作するGUIアプリではありません。

PythonとPythonライブラリは実行ファイルに含まれています。Appleの公証とWindowsの発行者署名は含まれていません。OSが起動を止めた場合は、入手元とチェックサムを確認したうえで、個別アプリに対するOSの許可機能を使用するか、ソースから実行してください。OS全体の保護機能を解除する必要はありません。Intel Macではソース版を利用できます。

### ソースから実行する場合

**Python 3.11以上**が必要です。macOSではリポジトリ直下の`bash setup_mac.sh`がツール別の仮想環境を作ります。WindowsではPython 3.13を用意し、`powershell -File .\setup_windows.ps1`を実行します。手動設定も可能です。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r herbarium_specimen_collector/requirements.txt
```

Windowsでは最初のコマンドを`py -3.13 -m venv .venv`にし、Pythonのパスを`.venv\Scripts\python.exe`に置き換えます。

```bash
.venv/bin/python herbarium_specimen_collector/scripts/collect_specimens.py --help
.venv/bin/python checklist/scripts/checklist.py --help
```

以下の例では配布版のコマンド名を使います。ソース版では上記の呼び出し方に、Windowsでは`.\vasculum-collect.exe`または`.\vasculum-checklist.exe`に置き換えてください。

## 特定の種の標本を集める

最初に、通信せずに入力と設定を確認します。

```bash
./vasculum-collect --dry-run --taxon "Haplopteris mediosora" --sources gbif --contact-email "you@example.org"
```

次に、GBIFから最大10件のメタデータを取得します。

```bash
./vasculum-collect --taxon "Haplopteris mediosora" --sources gbif --limit 10 --skip-images --contact-email "you@example.org" --output "results/mediosora-trial"
```

動作と出力を確認したら、`--limit`と`--skip-images`を外し、異名や検索先を追加します。研究用の出力先は新しいフォルダーにします。

```bash
./vasculum-collect --taxon "Haplopteris mediosora" --synonym "Vittaria mediosora" --sources gbif,cvh --contact-email "you@example.org" --output "results/mediosora-images"
```

`--limit`は**検索先と検索名の組み合わせごと**の上限です。異名を増やすと、全体の取得数は上限値より多くなります。`--sources`を省略すると設定済みの全検索先を対象にするため、時間がかかる場合があります。

写真の最大辺は標準で2400ピクセルです。`--image-resolution low`では1600ピクセルにします。取得できない画像、条件より小さい画像、解読できない画像は採用されない場合があります。出力表と概要で状態を確認してください。

GBIFの初期設定は保存標本です。`--gbif-occurrence-mode observations`、`specimens-and-observations`、`all-images`では観察記録なども対象に含めるため、標本だけのデータセットとは区別してください。`--gbif-coordinate-filter with-coordinates`はGBIFの座標・問題フラグの条件を指定しますが、座標精度を保証するものではありません。

### 出力と再実行

| ファイル | 内容 |
| --- | --- |
| `dwc.csv`、`dwc.tsv` | 統合した標本情報、座標、出典、権利情報、画像の状態 |
| `images/` | ダウンロードまたは再利用したJPEG |
| `summary.txt` | 検索先、件数、警告・エラー、出力のチェックサムなど |

各実行で新しい`--output`を使うと、過去の結果を残せます。同じフォルダーで再実行すると表は更新されます。自動の版管理機能はありません。既存写真は、今回の表から参照されなくなっても初期設定で残します。全面的な失敗では過去の表を保持します。部分成功は`dwc.partial.csv`、`dwc.partial.tsv`、`summary.partial.txt`に別保存し、過去の完了結果を残して終了コード`3`を返します。正常に完了した再実行は通常の表を更新するため、同じ出力先を使う場合は事前にバックアップしてください。

`--prune-unreferenced-images`を指定すると、エラーのない実行の後に、今回の表から参照されないJPEGを削除します。`--keep-unreferenced-images`は従来コマンドとの互換性のため残してあり、現在の初期設定と同じ動作です。`miss_`で始まる写真は削除しません。

### 標本の同定を確認する

標本写真と出典ページを見て、研究者が同定を判断します。誤同定と判断した写真には、元のファイル名の先頭に`miss_`を付けてください。次回の収集では、対応する写真を再ダウンロードせず、その記録をDwC表から除外します。マークした写真は、確認履歴として残します。判断を取り消す場合は`miss_`を外します。それ以外のファイル名は変更しないでください。

この仕組みは生成ファイル名の一致に依存します。提供元で標本番号や命名に関わる情報が変わった場合は、過去のマークが正しく対応するか、元の標本識別子で確認してください。

## 地域のチェックリストを作る

小さい範囲で設定を確認します。

```bash
./vasculum-checklist --dry-run --country Japan --latitude 35.303622 --longitude 139.606166 --radius 0.5 --taxon Ferns --contact-email "you@example.org"
```

実行するには`--dry-run`を外し、`--output "results/jimmuji-ferns"`などを追加します。Codex CLIを別途インストールしてログインしておく必要があります。PATHに見つからない場合は、環境変数`VASCULUM_CODEX_COMMAND`に実行ファイルのパスを指定します。Windowsのnpm版は内部の`codex.exe`を探します。自動検出に失敗した場合は、そのパスを直接指定してください。`--dry-run`とリリーステストはAIを呼び出しません。

種の検索後、プログラムは画像候補数の見積もりを表示し、続行するかを尋ねます。画像評価では多くのモデル呼び出しが発生する場合があります。この見積もりは画像数であり、料金や所要時間を保証する値ではありません。特定のモデルを使う場合は、アカウントで利用可能なモデルを`CODEX_MODEL`に設定します。未指定の場合はCLIの設定に従います。

`--sources`には地域内の根拠記録を取得する`gbif`が必要です。`cvh`を加えると、代表写真の候補を補います。掲載写真は検索範囲外の標本になることもあります。写真の採集地と、地域内分布の根拠記録を区別して確認してください。

`--limit`は調べるGBIF記録数の上限で、種数の上限ではありません。上限に達すると部分結果として終了コード`3`を返します。`--max-pdf-images 10`はPDF用画像の取得数を制限しますが、それより前の地域内記録検索は制限しません。国全体と大きな分類群を組み合わせた検索は長時間になり得るため、最初は狭い地域・分類群を選んでください。

緯度、経度、半径は3つをまとめて指定します。半径の単位はkmで、有限の正数が必要です。日付変更線や極を跨ぐ円は誤解釈を避けるためエラーにします。小さい別々の範囲に分けてください。行政区の絞り込みは提供された地名文字列を利用するため、異表記を取りこぼす場合があります。

### チェックリストの出力

- `species_list.csv`、`.tsv`、`.md`：種名、根拠件数、代表画像情報、PDFの参照ページ。
- `records.csv`：検索地域内の根拠記録と識別子・出典リンク。
- `checklist.pdf`：表紙、検索範囲、科別件数、各種のページ、索引、来歴。
- `summary.txt`：実行コマンド、検索条件、検索先の状態、警告・エラー。

PDFはOSにあるフォントを使います。文字が欠ける場合は、使用言語に対応する`.ttf`または`.ttc`のパスを`VASCULUM_FONT_PATH`に設定します。macOSのシステムフォント、Windowsの游ゴシック・メイリオ・Arial、LinuxのNoto CJK・DejaVuを順に探します。

## 再現性と研究利用

実行コマンド、出力一式、概要、バージョン、変更した設定を保存してください。配布版の`BUILD_INFO.json`には、ビルド環境、Pythonパッケージ、元コミットを記録しています。ただし、外部データベースと画像URLは変化します。同じ検索の完全な再現は保証されません。現在の実装は取得時の生応答を一時フォルダーに置くため、再実行用の恒久アーカイブにはなりません。

データベースの採用名やAIが選んだ写真だけでは、標本の同定を立証できません。本リリースの保守作業は、個々の同定、地理情報、地域フロラの完全性を独立に検証するものではありません。標本庫・データセットを引用し、写真の再配布前には個別の利用条件を確認してください。

## 困ったとき

| 状態 | 対応 |
| --- | --- |
| 終了コード`0` | 処理完了、またはdry-run成功です。研究上の妥当性は別途確認します。 |
| `1` | 表示された設定・通信・該当記録なし・Codex関連のエラーを確認します。 |
| `2` | コマンド引数を修正します。 |
| `3` | collectorでは`summary.partial.txt`、checklistでは`summary.txt`を確認し、検索先・画像の失敗や取得上限を解消します。 |
| `130` | 中断されました。履歴を残す場合は新しい出力先で再実行します。 |
| メールアドレスが未設定 | `--contact-email`、`CONTACT_EMAIL`環境変数、ローカルの`.env`のいずれかに設定します。 |
| Codexのオプションエラー | CLIを更新します。必要な機能の有無を事前確認し、版固有の任意オプションは対応時だけ使います。 |
| 写真がない | 画像URL、利用条件、サイズ制限、取得エラーを確認します。生物の不在とは解釈しません。 |
| Windowsのパスに空白がある | 全体を二重引用符で囲みます。実行ファイルなら`& "C:\path with spaces\vasculum-collect.exe"`とします。 |

配布版は、現在開いているフォルダーの`.env`を読み、初期設定の`output/`もそこに作ります。ソース版は各ツールのフォルダーを基準にします。`--output`と`--contact-email`を指定すると、保存先と連絡先が明確になります。
