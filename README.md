# 日本株 Swing Lens

日本株を6つの著名なスイング／モメンタム手法で横断分析し、候補、判定根拠、
条件付きエントリー帯、損切り・利確目安を表示するローカルアプリです。
J-Quantsの有料契約なしで運用できる構成を標準にしています。

## スマホ・クラウド版

`public/dashboard` に、Macを起動しなくても閲覧できる軽量Web版を実装しています。
生の株価DBは公開せず、全銘柄ランキングと銘柄別の最新分析だけを書き出します。

- スマホ優先レイアウト
- 銘柄コード・会社名検索
- 状態フィルター
- エントリー、損切り、1R・2R利確
- 手法別判定と全条件の内訳
- 直近180日のチャート
- ホーム画面追加対応
- Strategy固有Structure PivotとPivot Fidelity
- 5手法Breakout Consensus（Connorsは別枠）
- Coverage / Confidence
- Signal Snapshot・1/5/10/20営業日後の自動追跡
- ChatGPTレビュー用CSV / JSON
- 20日平均売買代金の段階評価・低流動性警告・Trading Value Ratio

`.github/workflows/update-dashboard.yml` は平日19:00（日本時間）に分析を実行し、
成功した場合だけGitHub Pagesを更新します。失敗した日は前回公開版がそのまま残ります。
手動実行にも対応しています。

### GitHub Pagesを有効にする

1. このフォルダをGitHubリポジトリへ登録する
2. GitHubの **Settings → Pages → Source** で **GitHub Actions** を選ぶ
3. **Actions → Update Swing Lens → Run workflow** を一度実行する
4. 表示されたPages URLをスマホへ登録する

EDINETを使う場合だけ、GitHubの **Settings → Secrets and variables → Actions** に
`EDINET_API_KEY` を登録します。キーはWebサイトや公開データには書き出されません。

公開データを手元で再生成する場合は次を実行します。

```bash
.venv/bin/python scripts/export_web.py
```

### ChatGPTへ検証を依頼する

最初に次のURLを提示してください。

`https://takina6-source.github.io/japan-swing-lens/validation/index.json`

ChatGPTは件数と期間を確認後、目的に応じて `signals.csv`、`performance.csv`、
`controls.csv`、`control_performance.csv`、`summary.csv` を参照できます。Market、固定Random、
条件の近い非Signal銘柄に対するExcess Returnも比較できます。画面下部から直接ダウンロードできます。
詳細なschemaと判定仕様は [`docs/VALIDATION.md`](docs/VALIDATION.md) を参照してください。

### Experimental Strategies（並走検証）

Coreとは独立したResearch Layerとして、Turtle / Donchian Breakout、Earnings Momentum、
Sector Relative Strengthの3手法を `2026-09-01` から記録します。ランキング一覧では
`Experimental x/3` だけを補助表示し、詳細画面で各手法のStateと根拠を確認できます。
Experimentalの結果はCore Consensus、Confluence、BREAKOUT判定、順位、Confidence、Coverageへ
一切加算しません。

Experimental Validationの入口は次の固定URLです。

`https://takina6-source.github.io/japan-swing-lens/experimental/index.json`

Signal、History、1/5/10/20営業日Performance、Random / Matched Control、Version別Summaryを
`/experimental/` 配下へ出力します。仕様、判定条件、無料データの制約は
[`docs/EXPERIMENTAL.md`](docs/EXPERIMENTAL.md) を参照してください。

## 起動方法

Finderで `start.command` をダブルクリックします。初回は環境準備と株価取得に数分かかることがあります。
ブラウザが自動で開いたら、左側を次のように設定してください。

1. 運用モード: **無料実用**
2. 分析範囲: 最初は **主要500+Growth**（約1,000銘柄）
3. 取得後に「今日のランキング」から候補を選ぶ

2回目以降はSQLiteの保存データに直近分だけを追加するため、初回より短時間になります。
更新をやり直したい場合は左側の「データを更新」を押します。

macOSが初回起動を止める場合は、Finderで `start.command` をControlクリックして「開く」を選びます。
同一LANのiPhoneからは、MacのIPアドレスと `:8501`（例 `http://192.168.1.20:8501`）へ
アクセスできます。利用中はMacとアプリを起動したままにしてください。

## 無料実用モードのデータ構成

| 用途 | データ源 | キー | 注意点 |
|---|---|---:|---|
| 上場銘柄・市場・業種 | JPX公式「東証上場銘柄一覧」 | 不要 | 月次更新 |
| 日足・出来高・TOPIX代理系列（1306 ETF） | Yahoo Finance / `yfinance` | 不要 | 非公式経路。仕様変更や一時失敗の可能性あり |
| 財務（売上・EPS・ROE等） | EDINET → J-Quants → Yahoo | EDINET/J-Quantsは無料キー・任意 | 年度単位で優先ソースを採用。Yahooは非公式補完 |
| 公式株価との照合 | J-Quants Free | 無料キー・任意 | 遅延データ。ランキング株価には不使用 |

株価の取得に失敗しても、保存済みデータがあれば分析を継続します。画面には最終株価日、取得率、
EDINET財務の保有銘柄数を表示します。財務がない条件は `×` にせず `N/A` として扱います。

## 無料キーの設定（任意）

日足テクニカル分析だけならキーなしで使えます。CAN SLIMなど財務条件も充実させる場合は、
本人がEDINETで無料APIキーを発行し、画面左の「無料EDINET財務を設定」に入力してください。
J-Quants Freeは「任意：J-Quants Free照合」から設定します。

年次EPSはEDINET標準タグ、EDINET拡張タグ、EDINETからの派生値、J-Quants、Yahooの順で
不足年度だけを補います。同じ年度に複数値がある場合は優先順位を固定し、10%超の差を
`DATA_CONFLICT` として残します。取得状態は銘柄詳細の「CAN SLIM A：年次EPS」で確認できます。
一括診断は `python scripts/diagnose_fundamentals.py --na-only`、1銘柄は末尾に4桁コードを指定します。

キーはこのMacの `.streamlit/secrets.toml` にだけ保存され、株価DBと同様にGit管理対象外です。
チャットへキーを貼り付けないでください。

## 無料版で有料版より弱い部分

- Yahoo日足は公式APIではなく、継続性・補正仕様・取得安定性を保証できません。
- EDINETは速報用ではないため、四半期決算直後の売上・EPS判定が遅れることがあります。
- 決算予定、機関投資家保有、詳細な業績予想などは欠損しやすく、該当条件は `N/A` です。
- 自動売買や証券会社への注文送信は行いません。

したがって、候補探索と売買計画の下調べには使えますが、注文前には適時開示、会社IR、
証券会社の現在値・決算予定を確認してください。

## 売買シナリオ

銘柄詳細に、条件付きエントリー帯、損切り目安、第1利確目安（1R）、基本利確目安（2R）、
伸長時の20%目安を表示します。Pivotから5%超離れた銘柄は追随警告を出し、
セットアップ未成立・過熱・失敗状態では価格を無理に提示せず「見送り」と表示します。

これらは教育・検証用の機械算出値であり、個人の資産、許容損失、税、注文状況、
決算リスクを反映した投資助言ではありません。

## 判定と保存

- **STRICT**: 原典条件を数値化できるもの
- **PRACTICAL**: 日足データ上での実用的な実装
- **PROXY**: 目視・独自データが必要な条件の機械判定近似

`data/momentum.db` に銘柄一覧、日足、財務、条件判定、シグナル、売買シナリオ、取得ログを保存します。
判定は `logic_version` とともに保存され、閾値は `config/thresholds.yaml` に分離されています。

## 開発者向け検証

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[jquants,test]'
.venv/bin/pytest
.venv/bin/python -m streamlit run app.py
```

実装条件と取得可否の詳細は [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) を参照してください。
