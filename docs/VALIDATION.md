# Swing Lens Observer / Validation仕様

## 目的

Swing Lensは、100点満点の予測器ではなく、5つのMomentum／Breakout系Strategyと
Connorsの押し目判定を記録する「スクリーナー兼観測装置」とする。Signal発生時の判定を
Immutable Snapshotとして残し、その後の価格・Consensus・各Strategy Stateを追跡する。

## CAN SLIM A

年次EPSは次の順で無料取得する。

1. EDINET API v2で取得できた有価証券報告書等の年次EPS
2. 不足銘柄をYahoo Financeの年次損益計算書で自動補完（1回30銘柄、強い銘柄優先）
3. 3期未満はN/Aとし、FAILへ変換しない

判定は、William O'Neil / IBDの「過去3年の利益成長率25%以上」を中心にする。
4期以上あり直近3回の前年比が各25%以上ならSTRICT、3期以上で黒字継続・減益なし・
3年CAGR 25%以上ならPRACTICAL。赤字から黒字への転換、単年400%超の異常値は
BORDERLINEとし、赤字継続やCAGR不足はFAILとする。

参考：

- IBD, 20 Rules for Your Investment Success: https://shop.investors.com/images/promotional/20-Rules_102808.pdf
- William O'Neil + Co. EPS Rank: https://www.williamoneil.com/about-us/legal/oneil-proprietary-rating-and-rankings

Yahooの財務値は非公式経路のため、取得できてもPRACTICALでありSTRICTにはしない。
分割後の連続性は財務諸表側で再表示されたEPSを優先するが、全銘柄で完全保証はできない。

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

## 公開Export

GitHub Pagesの次の安定URLへ毎営業日自動出力する。

- `/validation/index.json`: 件数、期間、Version、ファイル一覧
- `/validation/signals.csv` / `signals.json`: Signal Snapshot
- `/validation/signal_history.csv` / `signal_history.json`: State・Consensus時系列
- `/validation/performance.csv` / `performance.json`: 1/5/10/20日後Return等

Performanceには `initial_trading_value_20d`、`initial_liquidity_level`、
`initial_trading_value_ratio` と、各観測時点のLiquidity関連列を含める。

CSVは1列1意味、UTF-8 BOM、日付はISO `YYYY-MM-DD`、リターン単位はpercent、
欠損は空欄、StateはEnum表記で統一する。`signal_id` で3ファイルを結合できる。

GitHub Actions cacheが失われても、直前公開版の `validation/state.json` を次回実行時に
再取り込みする。API Key、Token、ローカルPath、個人情報は公開しない。

## Look-ahead / Survivorshipの制約

- PivotとBREAKOUTにはT-1までの価格だけを使用する。
- Signal Snapshotの財務値は当時DBに保存済みの値だけを使う。
- EDINETの公表日は保存するが、Yahoo年次EPSは正確な公表日を取得できない場合がある。
- 現在のJPX銘柄一覧を母集団にするため、上場廃止銘柄を含む完全なSurvivorship Bias除去は未対応。
- TOPIXは1306 ETFの無料代理系列。配当・追跡誤差により公式指数と差が生じる。
- Yahoo Financeは非公式経路で、補正仕様・可用性を保証できない。

この制約はExportの解釈時に考慮し、閾値の自動最適化や勝率だけの評価は行わない。
