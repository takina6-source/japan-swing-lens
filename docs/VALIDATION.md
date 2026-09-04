# Swing Lens Observer / Validation仕様

> この文書は既存Core Validationの仕様です。Coreと分離したTurtle、Earnings Momentum、
> Sector Relative Strengthの並走検証は [`EXPERIMENTAL.md`](EXPERIMENTAL.md) を参照してください。
> Experimental列は導入日以降のCore Snapshotへ観測値を付加するだけで、Core判定値を変更しません。

## 目的

Swing Lensは、100点満点の予測器ではなく、5つのMomentum／Breakout系Strategyと
Connorsの押し目判定を記録する「スクリーナー兼観測装置」とする。Signal発生時の判定を
Immutable Snapshotとして残し、その後の価格・Consensus・各Strategy Stateを追跡する。

## CAN SLIM A

年次EPSは次の順で無料取得する。

1. EDINET標準Basic EPSタグ（STRICT候補）
2. EDINET企業拡張タグ
3. EDINETの年次利益÷期中平均株式数による派生値
4. J-Quants通期財務（キー設定時のみ）
5. Yahoo Finance年次損益計算書（Basic EPS優先、非公式）

不足銘柄は1回30銘柄まで強い銘柄から補完し、日次の全件再取得はしない。年度別に異なる
ソースを組み合わせられるが、同一年度は上記の優先順位で1値だけを採用する。3期未満は
理由コード付きN/Aとし、FAILへ変換しない。診断結果は
`validation/fundamental_diagnostics.csv` / `.json` に書き出す。

判定は、William O'Neil / IBDの「過去3年の利益成長率25%以上」を中心にする。
4期以上あり直近3回の前年比が各25%以上ならSTRICT、3期以上で黒字継続・減益なし・
3年CAGR 25%以上ならPRACTICAL。赤字から黒字への転換、単年400%超の異常値は
BORDERLINEとし、赤字継続やCAGR不足はFAILとする。

参考：

- IBD, 20 Rules for Your Investment Success: https://shop.investors.com/images/promotional/20-Rules_102808.pdf
- William O'Neil + Co. EPS Rank: https://www.williamoneil.com/about-us/legal/oneil-proprietary-rating-and-rankings

Yahooの財務値は非公式経路のため、取得できてもPRACTICALでありSTRICTにはしない。
分割後の連続性は財務諸表側で再表示されたEPSを優先するが、全銘柄で完全保証はできない。

取得状態は `COMPLETE / PARTIAL / INSUFFICIENT / FAILED`、データ忠実度は
`STRICT / PRACTICAL / LOW_CONFIDENCE / PARTIAL / N/A` で保存する。通信・認証・タグ欠損・
履歴不足・値の矛盾は固定Reason Codeとして診断DBへ保存し、既存Signal Snapshotは変更しない。
Historical Signalを再評価する場合は、Signal基準日より後の公表日を持つ財務値を除外する。
公表日を取得できないYahoo値も、基準日より後の会計年度は採用しない。

## Strategy別Pivot

全PivotはT日の判定時にT-1までのOHLCVだけで算出する。認識済みsetupは
`setup_registry` に保存し、最大90営業日は同じ `setup_id` / Pivotを再利用する。

| Strategy | Structure Pivot | 認識できない場合 |
|---|---|---|
| Minervini | 60/30/15日収縮を満たすVCP最終収縮上限 | 20-day High PROXY |
| Qullamaggie | Prior Move後、10〜60日のConsolidation上限 | 20-day High PROXY |
| CAN SLIM | 30日・値幅15%以内のFlat Base上限 | 50-day High PROXY |
| Weinstein | 60日Stage 1 BaseのResistance上限 | 126-day High PROXY |
| Darvas | 20日Box Top（上限接触2回以上） | 20-day Box High PROXY |
| Connors | Breakout Pivotを適用しない | N/A |

`Structure / PRACTICAL` はOHLCV上で構造条件を満たしたPivot、`Lookback Proxy / PROXY` は
安定した構造認識ができず期間高値へ戻したPivotを表す。

## BREAKOUT / Consensus

BREAKOUTは `前日終値 <= 固定Pivot < 当日終値` とStrategy固有の出来高条件を満たす
「突破イベント」。突破後5営業日はRECENT BREAKOUTとしてBREAKOUT状態を保持する。
Pivotから8%超はEXTENDED、突破後にPivotを3%超下回るとFAILEDとする。

全体BREAKOUTはMinervini、Qullamaggie、CAN SLIM、Weinstein、Darvasのうち2手法以上が
BREAKOUTであることを必須とする。1手法BREAKOUT＋1手法WATCH、または複数WATCHかつ
高ConfluenceはBREAKOUT WATCH。Connorsは独立したMean Reversion枠でConsensus 5票に含めない。

## Confluence / Coverage / Confidence

- Confluence: REQUIREDに明確なFAILがなく、TRIGGERが成立した順張りStrategy数（0〜5）
- Coverage: 全条件のうちN/Aでなく実際に評価できた条件の割合
- Confidence: CoverageとSTRICT / PRACTICAL / PROXYの構成からHIGH / MEDIUM / LOWを表示
- Pivot Fidelity: Consensus内で2手法以上がStructure PRACTICALならPRACTICAL、期間高値中心ならPROXY

ランキングは State → Breakout Strategy Count → Confluence → Coverage → Confidence →
Pivot Fidelity → Momentum → Liquidity の辞書順であり、加重100点方式ではない。

## Liquidity

20営業日の `close × volume` 平均を通常時の売買代金として、VERY HIGH（10億円以上）、
HIGH（5億円以上）、GOOD（1億円以上）、LOW（3,000万円以上）、VERY LOW（3,000万円未満）
に分類する。当日売買代金と20日平均の比率をTrading Value Ratioとして併記する。

Liquidityは売買執行上の注意表示とランキング最終順位にのみ使用し、Strategy、BREAKOUT、
Consensus、Momentum、ConfidenceをFAILへ変更しない。LOW / VERY LOWはスリッページ、
急変動、Exit時の不利約定に注意が必要な水準として警告する。

## Signal保存

新規テーブルは既存DBを削除せず追加される。

- `setup_registry`: Strategy別の固定Pivotとsetup_id
- `signal_snapshots`: Signal発生時のImmutable Snapshot
- `signal_history`: Signal後の日次Observation
- `annual_eps`: 年次EPS履歴

`signal_id = setup_id + strategy_version` とし、同じsetupを毎日別Signalとして保存しない。
Snapshotは `INSERT OR IGNORE` のため、後日の閾値変更や財務補完で書き換わらない。
Snapshotには20日平均売買代金、Liquidity Level、従来のliquid判定、当日売買代金、
Trading Value Ratioを保存する。Historyにも当日売買代金、Ratio、Liquidity Levelを保存する。
Historyは0、1、5、10、20営業日を含む毎回の観測を保存し、Consensus State、Breakout Count、
各Strategy State、Return、Benchmark Relative Return、MFE、MAE、Failed Breakout、1R/2R/Stop到達を記録する。

Versionは `logic_version`、`strategy_version`、`threshold_version`、`schema_version` の4種類を保存する。
Experimental導入後の新規Core Snapshotには、比較用として `experimental_version`、
`experimental_alignment`、`experimental_combination`、3手法のStateも同時保存する。
導入前Snapshotは遡及補完しない。既存のCore列、Signal ID、History、Control membershipは更新しない。

## Control Group / Baseline

Market Benchmarkだけでは「相場全体が上がったためSignalも上がった」ケースと、スクリーナー固有の
銘柄選択能力を分離しにくい。そのため、Signal発生日Tの同一分析Universeから次の対照群を固定する。

1. **Market**: 従来どおり1306 ETFをTOPIX代理系列として使用する。
2. **Random**: Signal銘柄以外から20銘柄を抽出する。`signal_id + selection_version`のSHA-256を
   seedにするため、Universeの入力順や再実行でメンバーは変化しない。
3. **Matched**: BREAKOUT / BREAKOUT WATCHを除外し、流動性、Momentum、市場区分、JPX規模区分、
   株価水準の距離が小さい順に最大8銘柄を選ぶ。時価総額の無料一括データがないため、JPX規模区分を
   Market Cap帯の代理にする。

`control_members`はSignal発生日に`INSERT OR IGNORE`で固定し、後日選び直さない。
`control_group_id`と`signal_id`で結合でき、選択時点の価格、Momentum、売買代金、市場・規模区分、
Versionも保存する。導入前の過去Signalは当時のUniverseを復元できないため、未来情報を避ける目的で
遡及的なControl選択を行わない。

## Control Performance / Excess Return

`control_history`はSignalと同じ基準日から、各Controlの1・5・10・20営業日Return、Benchmark
Relative Return、MFE、MAEを同じ式で記録する。`performance`では各horizonについて次を出力する。

- `excess_vs_market`: Signal Return − 1306 ETF Return
- `excess_vs_random`: Signal Return − Random Control平均Return
- `excess_vs_matched`: Signal Return − Matched Control平均Return
- Random / Matchedは平均と中央値、サンプル数も併記する

## Validation Summary

`summary.csv` / `summary.json`はVersionを跨がず、horizon別に全Signal、初期State、Breakout数、
Strategy Combination、Momentum Bucket、Liquidity Level、Pivot Fidelity、Market Regimeを集計する。
平均・中央値・上昇率・各Excess Return・MFE・MAE・標準偏差・Standard Error・95%信頼区間を出力する。

Sample Strengthは設定値に従い、30未満を`INSUFFICIENT`、30〜99を`PRELIMINARY`、100〜299を
`MODERATE`、300以上を`STRONGER_SAMPLE`とする。p値や単一指標から自動的に「有効」と判定せず、
結果を用いた閾値の自動最適化も行わない。

## 公開Export

GitHub Pagesの次の安定URLへ毎営業日自動出力する。

- `/validation/index.json`: 件数、期間、Version、ファイル一覧
- `/validation/signals.csv` / `signals.json`: Signal Snapshot
- `/validation/signal_history.csv` / `signal_history.json`: State・Consensus時系列
- `/validation/performance.csv` / `performance.json`: 1/5/10/20日後Return等

Performanceには `initial_trading_value_20d`、`initial_liquidity_level`、
`initial_trading_value_ratio` と、各観測時点のLiquidity関連列を含める。
- `/validation/controls.csv` / `controls.json`: Signal発生日に固定したControl membership
- `/validation/control_performance.csv` / `control_performance.json`: Control銘柄ごとのForward Performance
- `/validation/summary.csv` / `summary.json`: Signal vs Market / Random / MatchedのVersion別集計

CSVは1列1意味、UTF-8 BOM、日付はISO `YYYY-MM-DD`、リターン単位はpercent、
欠損は空欄、StateはEnum表記で統一する。`signal_id` / `control_group_id`で結合できる。

GitHub Actions cacheが失われても、直前公開版の `validation/state.json` を次回実行時に
再取り込みする。`state.json`にはControl membershipとHistoryも含める。API Key、Token、
ローカルPath、個人情報は公開しない。

## Look-ahead / Survivorshipの制約

- PivotとBREAKOUTにはT-1までの価格だけを使用する。
- Signal Snapshotの財務値は当時DBに保存済みの値だけを使う。
- ControlはSignal発生日の同一Universeと同日価格だけで選び、未来のReturnをMatchingに使用しない。
- 現在のUniverseから導入前SignalのControlを遡及選択しない。
- EDINETの公表日は保存するが、Yahoo年次EPSは正確な公表日を取得できない場合がある。
- 現在のJPX銘柄一覧を母集団にするため、上場廃止銘柄を含む完全なSurvivorship Bias除去は未対応。
- Random / Matched Control導入後も、現在Universeを使用するSurvivorship Biasは完全解消しない。
- TOPIXは1306 ETFの無料代理系列。配当・追跡誤差により公式指数と差が生じる。
- Yahoo Financeは非公式経路で、補正仕様・可用性を保証できない。

この制約はExportの解釈時に考慮し、閾値の自動最適化や勝率だけの評価は行わない。

## Earnings Momentum v2の比較軸

Experimental SummaryはVersionを混在させず、EarningsについてState、Coverage（50%未満、50–74%、
75–99%、100%）、Fidelity、EPS/Sales加速、営業利益成長、Turnaroundを集計する。Signal Snapshotには
四半期Raw値から算出したEPS・売上・営業利益YoY、前四半期YoY、加速度、Source、Fidelity、最新期間、
公表日を保存する。1/5/10/20営業日のMarket、Random、Matched Control比較は従来どおり継続する。

`/data/quarterly_diagnostics.json`とCSVには銘柄別Coverage、取得Source、比較可能期数、欠損理由、
試行Sourceを出力する。Yahooの公表日不明値は取得日以前の過去Signalへ利用しないため、厳密な過去
Backfill用途には使用できない。

## 診断schema 2.0

`validation/index.json`のAnnual EPS集計は、4期完全取得を`complete_4y`、3期以上の利用可能数を
`usable_3y_plus`、3期のみを`partial_3y`、3期未満を`insufficient_under_3y`として分離する。
旧`before_complete`／`after_complete`および`*_fixable`は意味が曖昧なため新規出力から削除した。
Source別試行結果、Fidelity別件数、未試行・試行後未解決を別集計とし、診断分母とランキング分母の
差は`universe`と`excluded_from_ranking`で明示する。Signal・History・Performance・Control本体の
export schemaは3.1のままであり、この移行では変更しない。

## 固定入力による公開前ゲート

CIは固定したDB相当の価格・財務入力、基準日、乱数seedからCoreとExperimentalを再計算し、
Signal / History / Performance / Control / Rankingの正規化ハッシュを照合する。時刻項目だけは
除外するが、順位、判定、売買シナリオ、対照群の構成が変われば失敗する。GitHub Pages公開は、
この比較と全pytestの両方が成功した場合にのみ実行する。

基準値更新は仕様変更として扱う。`python scripts/fixed_input_regression.py --update`の実行前に差分を
確認し、意図した変化以外が含まれないことをレビューする。更新後は通常モードを再実行して同じ
ハッシュになることを確認する。
