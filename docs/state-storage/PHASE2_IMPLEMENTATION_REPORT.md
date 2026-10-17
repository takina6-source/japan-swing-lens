# Phase 2 実装・検証報告（本番切替前）

2026-10-17 JST（元データ市場日2026-10-15）。PR #4（base: Phase 1 PR #3）は実装・検証が完了し、レビュー提出可能。本番容量は未削減。main・Pagesは変更していない。利用者からの最新回答は「ローカル保管・照合は未実施、検証は続行」。本番切替・mainマージの承認は未取得。承認設定は false/null のまま。

## 実装

- `engine/state_generation.py`：3系統のStateを別の未公開ディレクトリへ決定的gzip出力。元バイト列・SHA・構造・件数・IDを検証してから未公開側のraw重複を除去。公開元は保持する。
- Pages外の独立Releaseを世代の基準にする。3系統・市場日・producer Run・親世代・圧縮SHA・履歴SHAを固定し、cold cacheでも古いPages・途中デプロイ・混在・欠落は停止する。全ファイルを検証してからDB取込を始める。
- 更新前のSignal/Control/Research Event等の主キー、H1–H4/Familyの定義、Version/Freeze、LIVE_FORWARD/HOLDOUTの区分を更新後も照合する。従来の成熟履歴処理・計算式は変更しない。
- `engine/bulk_downloads.py`：Validation/ExperimentalのSignals/History/Performance/Controls/Control PerformanceのJSON/CSV計18個を可逆gzip化。元バイト列は全数照合。利用者は外部ツールから旧URLを直接取得していないと回答済み。`index.json`の`available_files`とダウンロードリンクを更新する。
- `public/dashboard/downloads.js`：画面のCSVリンクはgzipを取得し、圧縮側・解凍側SHAとバイト数を検証して元ファイル名のCSVを保存する。対応APIのないブラウザは説明付きでgzipを保存する。破損時は保存しない。Service Workerの世代を更新する。
- Committeeの`core_history_path`/`experimental_history_path`が指すValidation/Experimentalの2履歴JSONは非圧縮で維持する。Morning Brief、What Changed、Committee、Coreデータ、summary、Research画面が取得するJSONのURL・内容は圧縮対象から除外する。
- `engine/storage_metrics.py`・Research UI：実ファイル合計、State論理/圧縮容量、前回実行/市場日との差、実測7/30営業日トレンド、180営業日予測、残容量・到達営業日数・信頼性を表示。Research単体の従来警告も別に残す。
- 日次workflow案：独立世代確認→分析→別ディレクトリに圧縮→同じ世代の独立Releaseバックアップ→再ダウンロード・SHA・全復元→Pages公開→3 Stateと配信ファイルの再検証。失敗時は次回も古い世代へ黙って戻らない。cache保存は成功後のみ。
- `scripts/state_rollback.py`：最新の同一世代バックアップから旧形式を別の未公開ツリーへ復元する。古いバックアップによる新しい記録の消失を拒否し、gzip配信CSVも元バイト列へ戻す。容量上限超過時は公開できると扱わず停止する。デプロイ機能はない。

## 最新バックアップと同一データ容量比較

確定元：公開Run **38806096548**、artifact **12008701930**、main `be9d1e565b90bc98b06019f78e5eb44a3a0a80ba`。Core市場日2026-10-15。Researchのbenchmark市場日は2026-10-14、経過21/90営業日という既存表示を維持している。

|対象|圧縮前|圧縮後|削減|
|---|---:|---:|---:|
|3 State|568,573,240 B（542.23 MiB）|57,187,231 B（54.54 MiB）|89.94%|
|公開ツリー全体|923,057,629 B（880.30 MiB）|199,460,077 B（190.22 MiB）|78.39%|
|18 bulk JSON/CSV|240,521,736 B|28,272,154 B|88.25%|

公開全体は同じ凍結Pagesデータをステージした実ファイルの合計（manifest・世代台帳・履歴・16KiB固定サイズの運用metrics JSONを含む）。凍結データに最終Phase 2のindex.html/research.js/sw.js/downloads.jsを組み合わせた比較。本番切替による実績ではない。

Stateのみでは全体411,697,937 B（392.63 MiB）。10/14→10/15の同方式比較で11.16 MiB/営業日の増加、条件付き猶予約50営業日となり、残り69営業日に足りないためbulk配信も圧縮した。

追加圧縮込みの同じ2世代の増加は **5,809,736 B（5.54 MiB）/1営業日区間**。上限まで800,539,923 B（763.45 MiB）。同じ速度を仮定すると約137.79営業日の余裕、69営業日後600,331,861 B（572.52 MiB）。ただし観測は1区間だけで、7/30営業日の実測平均でも研究完了の保証でもない。2倍の増加では残り69営業日の容量を満たせない。この1区間だけで研究完了の安全性が確立したとは扱わない。増加が2倍なら69営業日後1,001,203,645 B、3倍なら1,402,075,429 Bとなる。研究完了を保証せず、実測7営業日以降の予測で早期警告する。詳細は `phase2-evidence/capacity-final.json`。

### 元Stateと復元後のSHA-256（全系統一致）

|系統|元バイト数|SHA-256|
|---|---:|---|
|Validation|229,722,407|`832eef5c5e5109d936f2f60055e3b145593ba565d6056d0a20617288b023f113`|
|Experimental|259,855,047|`c06fe262adcb52bb43c8839c477ca182d279b7d03ec396554d5076c1229a6412`|
|Research|78,995,786|`2cb5f85866f975bee2624110678c9e7f0f5ee6b21436491ec7f3dc85d2c32a15`|

バックアップmanifestに全テーブルの件数・全ID集合のSHA・Version・研究区分件数がある。例：Validation Signals1467/Controls33992、Experimental Signals2210/Controls61880、Research Events4277（LIVE_FORWARD2784/HOLDOUT2716）、Subjects3388（LIVE_FORWARD2024/HOLDOUT1966）。同一Stateの可逆検証と、日次更新後の研究結果一致検証は区別する。最新凍結データの5/5×Momentum95+は50件（WATCH38 / BREAKOUT12）で、元データとgzipから戻したデータは完全一致。平均T+5は3.2476721256280947%、Matched超過は2.101994646972922%。これは更新処理後の一致判定ではない。全IDと各群の値は`phase2-evidence/latest-source-segment.json`を参照。

独立Release：[jsl-state-backup-run-38806096548](https://github.com/takina6-source/japan-swing-lens/releases/tag/jsl-state-backup-run-38806096548)。ZIP SHA：`218f68d363f0ef214b59d2d73d3005ff97554d941c589542f310cb54e67c41a3`。

[バックアップActions Run 38817230594](https://github.com/takina6-source/japan-swing-lens/actions/runs/38817230594)とMacで再ダウンロード、`shasum -a 256 -c`、3系統の復元に成功。利用者自身の保管確認は未実施。

## 新旧の警告

単位：1 MiB=1,048,576 B。上限は保守的にdecimal 1 GB=1,000,000,000 Bとする。Researchの旧方式はResearch論理容量等を監視し、そのstatusを`research_budget_status`として残す。新方式の主対象はPages公開全体の実バイト数。

- 旧Research方式：Research占有比が50%超、単回増加からの180日外挿が500 MiB超、または単回増加が5 MiB超でRED。占有比30%以上または同外挿250 MiB以上でYELLOW、それ以外GREEN。既存の`rolling_30d_growth_mb`は単回増加×30であり、実測30日平均として使わない。論理容量はDB行をJSON化した推計で、公開Stateファイルそのもののバイト数とも異なる。
- 新Pages方式のRED：実容量85%以上、または信頼できる180営業日予測が上限以上、または到達猶予70営業日以下。
- YELLOW：70%以上、180営業日予測85%以上、または履歴不足。
- GREEN：それ以外。Research旧警告が残る場合、全体が健全とは表示しない。
- 7/30営業日の傾きは市場カレンダー上8/31点が揃ったときだけ計算し、大きい方を予測に使用する。欠損・同日再実行・保存方式の変更を営業日増加に見せない。推定では負の成長を0にする。
- 圧縮前92.31%は新方式でもRED。圧縮後19.95%は履歴不足のためYELLOW。Researchの既存REDは保持する。圧縮を理由にGREENへ書き換えていない。

## 検証

- `python -m pytest -q`：319 passed（`phase2-evidence/pytest-macos.txt`）。
- `python scripts/fixed_input_regression.py`：11 artifacts passed（同`fixed-input-regression.txt`）。
- `node tests/js/downloads.test.cjs`：通常保存、元SHA破損、圧縮SHA破損、API非対応の4ケース成功。
- ローカルブラウザでControls CSVを保存。元CSVと保存CSVのSHAは両方 `7bb7fec0f925778a40807b40945a2f91be1b3f511081991f5272f5407c888b42`。ファイル内容・数値精度を維持。ローカル画面でもPages YELLOW / Research RED、190.22 MiB、履歴不足表示を目視確認。
- 実データの復旧用旧形式ツリーを生成：923,062,257 B、同じ3 State SHA一致。未公開。詳細 `phase2-evidence/rollback-real.json`。
- 最終実装commit `2c4a6f02f6d072ace2b9e1f7df38881d4e56eae6` のPR CI：[Phase 1 Run39027426545](https://github.com/takina6-source/japan-swing-lens/actions/runs/39027426545)、[Phase 2 Run39027426543](https://github.com/takina6-source/japan-swing-lens/actions/runs/39027426543)が成功。
- **全量の旧形式・圧縮形式各2回継続更新：成功**。[Run39024659114](https://github.com/takina6-source/japan-swing-lens/actions/runs/39024659114)、commit `a38b91531a661fbb8734fcbf3beb59353b8cae6b`。各回1,967出力の論理値と5/5×Momentum95+の50件の全行・ID・数値が一致した。Core/Validation/Experimental/Research、Morning Brief/What Changed/Committeeを含む。比較から除外するものはcreated_at/generated_atとResearch運用telemetry。数値の丸めによる一致判定はしていない。
- 入力965銘柄、Corporate Action候補35件、同じ開始DB・価格・財務・benchmark・固定時計を使用。入力gzip SHA `21df0c0a6774b847de42cf66bedb944044c560225b59590185246f1b175cba6f`。1回目のDBと出力Stateを2回目に継承し、各更新を別プロセスで実行。worker PIDは旧2646/2954、新3268/3479。固定した同じ市場日の入力による継続更新検証であり、将来2営業日のデータ取得・成長を実証したものではない。
- 証跡artifact12101344487をMacへ再取得し、ZIP SHA `8340d26132c89a6c6b350f24b81b4905870850ec1ade7a26d7a896f96656053b` を照合。保存済みreportについても両回の全出力hash辞書と対象行を再比較した。ユーザー向け証跡ZIPの `phase2-evidence/full-continuation-report.json` に全比較値、リポジトリの同 `full-continuation-summary.json` に要約を保持。全reportは上記Actions artifactからも取得できる。
- 全量比較後の最終実装commitとの差分は日次Releaseへの運用履歴/metricsの追加退避と検証ログの文言のみ。研究計算・State・配信処理の変更はない。最終commitのPR CIも成功。

非公開workflowは`contents:read`/`actions:read`のみ。成功した実行のjob/step記録にPages環境・upload-pages-artifact・deploy-pages・Release作成・production cache保存がないことを確認。本番最新Runは引き続き79（38806096548）、mainも`be9d1e565b90bc98b06019f78e5eb44a3a0a80ba`。`config/state-cutover.json`はfalse/null。非公開検証による本番切替はない。

## 切替前に残る条件

1. 実装・全量継続更新・最終CI・画面確認は完了。以下の保存確認・承認・本番切替が残る。
2. 切替直前に最新公開Runを再確認する。更新されていたら3 Stateのバックアップと利用者の保存確認を更新する。
3. 利用者がReleaseのZIPと`.sha256`をローカル保管し、Macで以下を実行してOKを確認する。

```sh
shasum -a 256 -c jsl-state-backup-run-38806096548.zip.sha256
```

4. 利用者の明示的なmainマージ・本番切替承認を受けた後だけ、最新RunとSHAを`config/state-cutover.json`に記録する。PR #3→#4の依存関係を処理する。承認前のマージは日次更新を止めるため禁止。
5. 同じ確定世代を使って切替し、公開後の3 State復元・市場日・全bulkファイル・画面を確認する。Service Workerの世代更新後に画面を再読み込みし、新しいCSVリンクとResearch容量表示を確認する。失敗時は新世代Releaseを消さず、同じ最新Stateから復旧する。古いcommit/backupへの単純revertをしない。

本Goal全体は本番削減と日次継続確認まで未完了。長期的なState全面分離は今回行わない。Releasesは独立バックアップと世代基準に利用する。将来はPagesを表示専用にすると容量余裕が増す一方、保持・復元・配信責任が増えるため別の移行判断が必要。

### 再実行・復旧のコマンド

リポジトリrootで依存関係をインストールした環境から実行する。`restore`と`state_rollback`の出力先は既存ディレクトリを指定しない。

```sh
python -m pytest -q
python scripts/fixed_input_regression.py
node tests/js/downloads.test.cjs
python scripts/state_backup.py restore --archive /path/to/jsl-state-backup-run-38806096548.zip --target /path/to/new-restored-directory
python scripts/state_rollback.py --source /path/to/current-compressed-pages --archive /path/to/same-generation-backup.zip --archive-sha256 SAME_GENERATION_ARCHIVE_SHA256 --target /path/to/new-unpublished-recovery
```

最後のコマンドは復旧用ファイル生成だけで、公開しない。初回切替後にデータが追加された場合はRun38806096548のバックアップで戻さず、その最新世代の独立Releaseを使用する。旧形式ツリーが1GBを超える場合は停止し、圧縮形式のままの修復等を判断する。

公開後に使う検証処理もlocalhost上で実行し、3 StateのDB復元・市場日・履歴・表示ファイル・18個のbulk配信の元/圧縮SHAが成功した（`phase2-evidence/local-publication-verification.txt`）。実際の本番公開後の検証とは区別する。

## 長期方式の比較（今回の実装対象外）

2026-10-16確認の[GitHub Pages公式上限](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)は公開サイト1GB。Actionsによるカスタム公開で除外されるのは時間あたりビルド回数の制限で、公開サイズ制限を無視する理由にはしない。

|選択肢|利点|追加の運用責任・移行難度|
|---|---|---|
|GitHub ReleasesへStateを分離|現在の認証・世代バックアップを活用できる。Pagesの増加からStateを分離できる|継続読込URLと保持方針、削除・失敗時復旧の管理が必要。中程度。公式では1ファイル2GiB未満で、Release全体サイズ・帯域には上限を設定していないが、無期限保存保証とは解釈しない。[公式仕様](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)|
|S3互換object storage（例：Cloudflare R2）|StateをPagesと別に管理できる。R2は10GB-monthの無料保存枠と無料egressがある|アカウント・鍵・権限・操作回数課金・世代保持・独立バックアップを管理する必要。中〜高程度。料金条件は導入時に再確認。[公式料金](https://developers.cloudflare.com/r2/pricing/)|
|Pagesを表示専用にする|表示に必要な小さいJSONのみを公開できる|Stateとbulk配信先の契約・世代整合・監視・復旧設計を分離する必要。上記保存先への移行と合わせて実施するため中〜高程度|

現在の90営業日の継続を優先し、全面移行・外部サービスの契約・新しい鍵の発行は実施していない。

## 検証環境と途中実行の扱い

全量比較は元cacheを使い捨てDBへコピーし、旧・新両方に同じ `--fast-temp-db` 設定（SQLのcommit/rollback境界を維持し、同期I/O待ちのみ省略）を適用した。本番DBは通常同期のままで変更しない。元cacheを変更できないパスガードとrollbackテストがある。異常終了した一時DBは破棄し、本番seedに使わない。再現用DBのコピーは研究入力に使わないfetch_logのみ除外・VACUUMし、研究テーブルと価格・財務・setupを維持する。

途中のRun38818967541は60分制限、39021459870は元Pages artifactの期限切れ、39021811270/39022080568はSIGSEGVにより未受入。最後の2件は通常同期でも発生し、原因を断定していない。定期スタック取得を外し、実際の日次更新と同じ1更新1プロセスに分離した最終Runで4処理すべて成功した。旧形式1回だけ完了した途中証跡はユーザー向け証跡ZIPの`partial-legacy-cycle.json`に残すが、成功の根拠にはしない。

期限切れ対策として、元ZIPとSHAが同じ公開Pages専用fixtureを[独立Release](https://github.com/takina6-source/japan-swing-lens/releases/tag/jsl-state-verification-fixture-run-38806096548)に保管した。プロバイダー入力の新規Release添付は自動承認レビューに拒否され実施していない。公開済みPagesのみの保存に限定し、プロバイダー入力は既存Actions artifactから取得した。全量検証の証跡artifactは30日保持（期限2026-11-16）で、再現用DB・固定入力・各回reportを含む。ユーザー向け証跡ZIPにはreportと要約を収録し、DBやプロバイダー入力を新しく公開しない。

日次世代Releaseには3 StateのZIP・世代台帳とともにstorage-history.json/storage-metrics.jsonを退避し、再ダウンロード時に元バイト列と比較する。Pagesの運用履歴破損時も同じ世代の履歴を復元できる。公開容量や研究計算には影響しない。
