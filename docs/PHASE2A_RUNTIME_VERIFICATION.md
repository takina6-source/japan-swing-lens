# Phase 2A 設計前 Runtime Verification

検証日: 2026-09-11 JST

## 結論

**Result: READY_WITH_DESIGN_GAPS**

Phase 2A の設計には進める。ただし、現行成果物だけでは「run 成功」と「対象全銘柄を同じ市場日で観測できた」を区別できず、`setup_id` もセットアップの継続識別子として使用できない。Phase 2A では full candidate を複製せず、run 完全性と銘柄別の最小観測事実を保存する必要がある。

## 検証対象

- 最新公開 Core snapshot: `2026-09-11T00:51:49+09:00` 生成、基準日 `2026-09-10`
- 最新公開 Morning Brief: `2026-09-11T00:51:55+09:00` 生成、基準日 `2026-09-10`
- 公開 snapshot に列挙された全 964 銘柄の detail
- ローカル `momentum.db` / `data/momentum.db`（SHA-256一致、分析最終日 `2026-09-03`）
- 現在の JPX master cache と `主要500+Growth` の scope 定義

公開物とローカル保存物は世代が異なるため、最新runの完全性は公開物、過去runの構造はローカルDBから確認した。

## 1. 最新公開run

| 項目 | 結果 |
|---|---:|
| scope | 主要500+Growth |
| 現行masterから算出したscope対象 | 965 |
| snapshot `universe_count` | 964 |
| snapshot candidate数 | 964 |
| detail取得成功 | 964 / 964 |
| 基準日 2026-09-10 | 960 |
| 基準日 2026-09-09 | 4 |
| snapshot `errors` | 1 |
| Morning Brief出力 | Top 20 |
| Morning Brief status | PARTIAL |

9月9日止まりの4銘柄は `5138 Rebase`、`9254 ラバブルマーケティンググループ`、`9331 キャスター`、`9342 スマサポ`。公開成果物からは、無取引、Yahoo側の未配信、取得失敗のどれかを区別できない。

Morning BriefのPARTIALは公開失敗ではない。Top 20全件でCAN SLIM等の条件N/Aとcoverage不足があり、13件ではProxy Pivotも使われている。またsnapshotが報告した上流error 1件を引き継いでいる。versionは `SAME_JOB_ATTESTED`。

## 2. 965対象と964保存の差

差分は `8303 SBI新生銀行` 1銘柄。ローカル価格履歴は184本で、分析入口の最低200本を満たさないため除外されている。

したがって `965 -> 964` は保存漏れではなく、現行ルールによる価格履歴不足除外。ただし現行snapshotは入力scope総数を保存せず、`universe_count` に分析通過後の件数を入れているため、画面上は除外理由を復元できない。

## 3. `970 / 2 / 968 / 964` の正体

DBの `analyses.as_of` を単純集計した件数であり、同一母集団の日次減少ではない。

| analysis date | logic version | 銘柄数 | 解釈 |
|---|---|---:|---|
| 2026-08-21 | v3-free | 970 | 当時のフルrun |
| 2026-08-21 | v4/v5/v6 | 各964 | 同一日を別ロジックで再計算 |
| 2026-09-01 | v7 | 2 | 9月2日のrun内で、2銘柄だけ価格日が前日止まり |
| 2026-09-02 | v7 | 968 | 同じrunの当日基準銘柄 |
| 2026-09-03 | v8 | 964 | 次世代run |

`2026-09-01` の2件は `5025 マーキュリー` と `9253 スローガン`。書込時刻は9月2日分968件と連続しており、独立した「2銘柄だけの部分run」ではなく、1回のrunに複数の銘柄別基準日が混在したものと判断できる。

現行DBには `run_id` がないため、書込時刻からの推定以上には確定できない。日付単位の件数だけをrun完全性判定に使ってはいけない。

## 4. data_quality母集団の注意点

最新公開 `data_quality.universe` は次を報告する。

- diagnostic_total: 970
- ranked_total: 964
- outside_current_scope: 6
- insufficient_price_history: 0

scope外の6銘柄は旧診断レコードで、現在の公開detailはすべて404になっており、公開ディレクトリの残骸は除去されている。

ただし `diagnostic_total` はAnnual EPS診断レコードを起点にしている。診断レコードのない8303は `insufficient_price_history` に数えられない。この集計は分析母集団監査には使用できず、Phase 2Aではscopeを起点に段階別件数を出す必要がある。

## 5. setup_id実測

ローカル9月3日detailと最新公開9月10日detailの共通964銘柄を比較した。

| 項目 | 件数 |
|---|---:|
| 総合setup_id維持 | 0 |
| 総合setup_id変更 | 964 |
| consensus state維持 | 711 |
| consensus state変更 | 253 |

総合`setup_id`は全銘柄で変化しており、同一setupの継続キーとしては使えない。既存 `setup_registry` も970銘柄・4,850行に対して4,626種類の手法setup IDしかなく、手法IDは銘柄をまたいで衝突している。

既存signal trackingは185銘柄、247 signalに限定され、全964銘柄の日次状態を復元できない。

## 6. Phase 2Aへ固定する要件

full candidate、detail、チャート、財務原票は複製しない。次の最小観測事実を保存する。

### run単位

- `run_id`、開始・終了時刻、logic/strategy/threshold version
- `expected_market_date`
- `scope_total`
- `price_history_eligible_total`
- `observed_total`
- `current_date_total`、`stale_date_total`
- `excluded_total`、`fetch_failed_total`、`analysis_failed_total`
- run status: `COMPLETE` / `PARTIAL` / `FAILED`
- 構造化されたerror codeと対象銘柄。件数だけの保存は禁止

### 銘柄単位

- `run_id`、code、observed_at、analysis_date
- close、consensus state、手法別state、採用Pivot
- 当日総合setup_idと手法setup ID（観測値としてのみ保存）
- logic/strategy/threshold version
- observation status: `CURRENT` / `STALE_MARKET_DATE` / `INSUFFICIENT_HISTORY` / `FETCH_FAILED` / `ANALYSIS_FAILED` / `OUT_OF_SCOPE`
- reason code、price history本数、直近価格日

### setup継続性

現行setup_idをactive setupの主キーにしない。Phase 2A State Machine Designで、銘柄コードを含む安定したsetup identity、Pivot継続、失効、再挑戦、新setupへの分岐を別途定義する。

## 7. 設計判断

- Top 20 Morning BriefはPhase 2Aの保存母集団ではない。
- 「前日」ではなく「前回成功観測」と比較し、欠測区間を明示する。
- 価格日が前日でも直ちにfetch失敗と決めない。市場無取引と取得失敗をreason codeで分ける。
- 最新日が混在するrunを許可する場合も、`PARTIAL`として識別可能にする。
- scope変更、上場区分変更、履歴不足を分析エラーから分離する。

以上により、Phase 2A State Machine Design Goal作成へ進める。
