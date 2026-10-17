# Phase 2計画・復旧手順（未実施）

Phase 1のレビュー完了まで実装・切替を行わない。mainマージと本番切替には別途明示承認が必要。本ファイルは手順案であり、実行承認ではない。

## ローカル保管とMacでの照合

Phase 1バックアップは専用Release `jsl-state-backup-run-38639189826` へ保存する。既存の `phase2a-state-v1` は使用しない。

1. [専用Release](https://github.com/takina6-source/japan-swing-lens/releases/tag/jsl-state-backup-run-38639189826) から `jsl-state-backup-run-38639189826.zip` と同名の `.zip.sha256`、`backup-manifest.json` を同じフォルダへダウンロードする。
2. MacのTerminalでそのフォルダへ移動し、次を実行する。利用者自身の任意の保存場所を使う。

```sh
shasum -a 256 -c jsl-state-backup-run-38639189826.zip.sha256
```

`OK` を確認する。アーカイブ全体のSHA-256と、内包された元StateそれぞれのSHA-256は別の値である。マニフェストの `streams.*.state.sha256` は元の非圧縮バイト列の値。

3. Phase 1コードと依存関係のある環境でアーカイブ内部も検証する。

```sh
python scripts/state_backup.py verify --archive /path/to/jsl-state-backup-run-38639189826.zip
python scripts/state_backup.py restore --archive /path/to/jsl-state-backup-run-38639189826.zip --target /path/to/new-restored-directory
```

`--target` には存在しないディレクトリを指定する。既存State・DBを上書きしない。3系統すべてを検証した後にだけ、復元ディレクトリが作成される。各 `validation/state.json`、`experimental/state.json`、`research/state.json` のSHA-256をマニフェストと照合できる。

```sh
shasum -a 256 /path/to/new-restored-directory/validation/state.json
shasum -a 256 /path/to/new-restored-directory/experimental/state.json
shasum -a 256 /path/to/new-restored-directory/research/state.json
```

復元したJSONは従来の `Database.import_validation_rows` / `import_experimental_rows` / `ResearchStore.import_state` へ取り込める。Phase 1ではネットワーク取得後の取込に `import_seed` を用い、同一系統内の全テーブルを1トランザクションで取り込む。リカバリ時も新しい検証用DBで確認し、本番DBへの直接上書きは行わない。

利用者から「保存済み」「SHA照合OK」を確認するまでPhase 2本番切替は禁止。今回の保存確認を、将来の最新バックアップに対する確認へ流用しない。

## 切替前の確定世代

1. 日次更新と切替が競合しないよう、同一concurrency groupで直列化する。停止・再開が必要なら人間へ具体案を提示する。
2. 最新成功Run、commit、artifact ID、生成時刻、Core市場日、Researchの観測日を記録する。全公開ファイルが入った不変のPages artifactから取得する。3つのURLを別々の時刻に取得しただけでは同一世代と見なさない。
3. Phase 1のバックアップ作成・復元・独立Releaseへの保存・再ダウンロード検証を最新世代で再実行する。Phase 1の固定Runを使い回さない。
4. 最新のローカル保管用ファイルとチェックサムを渡し、保存・照合の確認を得る。
5. その後の更新がないことを、公開RunとState SHAで再確認する。更新があれば再取得するか全差分を検証し、ローカル確認も更新する。バックアップした世代と切替入力のSHAを一致させる。

## 非公開dry-run（Phase 2で追加）

既存 `update-dashboard.yml` の `workflow_dispatch` は公開処理へ進むため使用しない。

- 独立したworkflowを使う。権限は `contents: read` / 必要なら `actions: read` のみ。`pages: write`、`id-token: write`、`github-pages` environmentを与えない。
- `configure-pages` / `upload-pages-artifact` / `deploy-pages` や本番Publish jobへの依存を置かない。別runnerの一時DBと出力先を使い、本番Cacheへ保存しない。
- 固定世代の3系統をマニフェストと照合し、gzip復元→分析・更新→再出力を連続2回以上実行する。データ提供者の入力も同一時点で固定し、旧コードの同一入力実行と比較する。
- byte一致と、日次追加を含む研究結果の一致を別々に検証する。H1–H4、全ID、Freeze/Version、LIVE_FORWARD、HOLDOUT、Corporate Action、現在Versionの探索セグメントを確認する。
- ログと検証レポートのみActions artifactとして保存する。終了後に実行記録の全job/stepとDeployment一覧を確認し、本番Publish job・Pages Deploymentが一度も実行されていないことを証拠化する。

Phase 1のPR CIはこの前段のオフライン検証のみ。外部価格データを使った本番相当の連続日次更新dry-runはまだ行っていない。

## 圧縮公開とMetricsの設計案

- `compress` でgzip level 9 / mtime 0 / filenameなし。既存JSONシリアライズ結果をそのまま圧縮する。ID、順序、精度、研究仕様を変えない。
- 各系統に `state.json.gz` と `state.manifest.json` を同じ世代で出力する。マニフェストは `describe` の値をそのまま保存する。
- 3系統を束ねる世代台帳も追加する。run ID、producer commit、市場日、親世代、各StateのSHAとサイズ、件数・IDダイジェストを固定し、独立保存先の既知の最新世代を復元の下限にする。cold cache時にも巻戻りを検出するために必要。
- gzip破損・欠落・manifest不一致・不明な世代では停止する。新旧が混在し同一SHAでない場合は停止する。別の古いJSONを自動採用しない。
- 初回切替時に旧JSONとgzipの二重公開を避ける。今回の実測では両方を追加すると約899 MiBとなり、1 GB上限への余裕は小さい。公開物を一時ディレクトリで完成・検証した後、承認済みの1世代を原子的にdeployする。
- UI用JSON/CSVの現行出力を維持する。大きいControl表示データは将来の分割読込・配信gzip等の候補だが、今回削除しない。
- Metricsは公開対象ファイル全体の実バイト、State元バイト、gzipバイト、マニフェスト等を別々に計測する。ZIP転送サイズやSQLiteファイルサイズをPages使用量として扱わない。
- 日次の実測を市場日で記録し、同一市場日の再実行を区別する。実測履歴から7日/30日の始点・終点・観測数・営業日数を示す。欠測や仕様変更、圧縮切替による容量段差を成長と混同しない。
- Pages全体の前回差分、7/30日の傾向、180営業日後、残容量、到達営業日数を算出する。履歴不足は `null` と信頼性不足を表示する。単日差分×30を実測30日平均と呼ばない。
- 既存Research単体の占有率・成長・旧警告も副指標として維持する。新しいPages警告で消さない。

暫定の新方式案：上限1,000,000,000 bytes、REDは現在85%以上・信頼できる180営業日予測が上限以上・残70営業日以内到達のいずれか。YELLOWは現在70%以上・予測85%以上・予測用実測履歴不足のいずれか。GREENはそれ以外。Research旧警告は別表示する。この条件をPhase 2でレビューし、根拠なく緩和しない。Phase 1で本番判定式は変更していない。

## ロールバックと停止条件

SHA不一致、ID/件数/研究結果の欠落・変更、継続更新失敗、バックアップ失敗、意図しないデプロイ、公開未確認、監視の危険な過小評価で停止する。エラーを空Stateで回避しない。

承認前はmainを変更していないため、このPRを取り下げれば稼働コードは従来のまま。Phase 2後の復旧では、問題世代のデプロイを停止して元データを保存し、最後の検証済み独立バックアップと、その後に増えたデータを照合する。新しい日次データを捨てて古いバックアップだけで再開してはならない。人間の承認後、整合した世代と旧writer/readerへ戻し、3系統の元JSON、DB、UI、次回更新を検証する。独立バックアップと失敗世代は保持する。

## 長期保存候補（今回移行しない）

| 案 | 利点 | 課題 | 移行難易度 |
|---|---|---|---|
| GitHub Releasesの不変世代 | 現行GitHub運用に近い、Pagesから独立、チェックサムで固定可能 | 最新世代選択、認証・レート制限、保持・復元テストが必要 | 低〜中 |
| S3互換Object Storage | 世代管理・保持期間・条件付き更新を構成できる | 契約、費用、権限、認証情報、監視の運用追加 | 中 |
| Pagesを表示専用にしStateを分離 | Pages容量を研究の増加から切り離せる | Seedの権威、ネットワーク障害対応、世代整合の設計が必要 | 中 |

まず残り約70市場営業日の研究を安全に完了する。長期基盤の全面移行をPhase 1へ混ぜない。
