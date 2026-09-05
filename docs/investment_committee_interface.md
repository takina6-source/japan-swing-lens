# Investment Committee 連携インターフェース

Japan Swing Lensがすでに算出した評価を、将来のInvestment Committeeが読み取るための静的JSONです。この層は分析や順位計算を行わず、生成済みの`data/snapshot.json`と`data/details/*.json`を共通形式へ転記します。そのため、Committee出力の追加はCore、Experimental、ランキング、画面表示に影響しません。

## 公開ファイル

- `committee/manifest.json`: 基準日、バージョン、銘柄一覧、各ファイルへの参照
- `committee/ranking.json`: 全銘柄の順位と要約
- `committee/latest/{ticker}.json`: 銘柄ごとの最新評価
- `committee/history/index.json`: 保持している基準日の一覧と順位スナップショット
- `committee/history/{evaluation_date}/ranking.json`: 基準日別の順位
- `committee/schema.json`: 個別銘柄JSONのJSON Schema（Draft 2020-12）

公開URLの基点は `https://takina6-source.github.io/japan-swing-lens/committee/` です。

## 共通キー

`ticker`はJPXコードを文字列で表し、Yahoo Finance用の`.T`を付けません（例: `9244`）。必要な場合だけ`provider_ticker`を参照します。`evaluation_date`は評価に使った市場データの基準日、`generated_at`はJSONを生成した日時であり、同じ意味ではありません。

`verdict`と`confidence`はSwing Lensの既存値をそのまま転記します。Committee向けに意味を変えたり、欠損を0へ置換したりしません。Swing Lensに共通のリスク分類がまだないため、`risk_level`は`null`です。

## CoreとExperimental

`core`は現在の順位を決める既存評価です。`experimental`は追加検証であり、`affects_core_ranking`は常に`false`です。Committeeは両者を別々の意見として扱い、ExperimentalをCoreの合否や順位に混ぜてはいけません。`signals`には両方を機械的に参照しやすい形でも収録します。

## 欠損とデータ由来

`source_status`は株価、年次財務、四半期決算の取得状態を`ok`、`partial`、`missing`で示します。欠損・N/A・部分取得は失敗や0ではありません。`reason_code(s)`、`coverage`、`fidelity`、`source`がある場合は併せて判断してください。Adapterは新しい推定値を作らず、既存成果物に存在する数値だけを`metrics`へ収録します。

## バージョンと履歴

比較時は最低でも`schema_version`、`engine_version`、`config_version`、`strategy_version`、`git_commit`を記録してください。異なるロジックや設定の値を同じ尺度として無条件に比較してはいけません。履歴は公開済み`history/index.json`を次回実行時に引き継ぎ、最大260取引日を保持します。初回公開以前の履歴は遡及生成しません。

検証成果物とは`ticker`、`evaluation_date`、`engine`、`engine_version`で結合します。個別JSONの`validation_reference`にCore/Experimentalの参照パスを収録しています。

## Fundamental Lensを接続するときの要件

将来のFundamental Lensも、共通キーとして`schema_version`、`engine`、`engine_version`、`ticker`、`company_name`、`evaluation_date`、`generated_at`、`verdict`、`confidence`、`risk_level`、`signals`、`metrics`、`source_status`を持たせます。ticker正規化、日時の意味、nullとmissingの扱い、バージョン・出典の保存を本仕様に合わせてください。Fundamental独自項目は別オブジェクトへ追加し、Swing LensのCore/Experimentalの意味を再定義しないでください。

Committee側では、各Lensの基準日とバージョンが比較可能かを先に確認し、欠損を反対票へ変換せず、出典とfidelityを残したまま統合します。注文判断へ直結させる場合は、静的スナップショットの遅延と無料データ源の制約を別途確認する必要があります。
