# Experimental Strategies 並走検証仕様

## 目的とCoreとの分離

ExperimentalはCoreへ統合する前のObserver / Research Layerである。対象はTurtle / Donchian
Breakout、Earnings Momentum、Sector Relative Strengthの3手法。開始日は`2026-09-01`、
Versionは`2026.09-exp-v1`とする。30日後も自動停止せず、Versionを固定したまま観測を継続する。

CoreのMinervini、Qullamaggie、CAN SLIM、Weinstein、Darvas、独立Mean ReversionのConnorsには
手を加えない。Experimental AlignmentをCore Confluence、aligned_count、breakout_count、
Consensus State、順位、Confidence、Coverageへ加算しない。Core Snapshotに保存するExperimental列は
同日比較用の観測値であり、既存Core列やSignal IDを変更しない。

## 判定条件

閾値はすべて`config/thresholds.yaml`に固定し、自動最適化しない。変更する場合は新しい
Experimental Versionとして保存する。

### Turtle / Donchian Breakout

- 判定日Tの終値がT-1までの20営業日高値または55営業日高値を初めて上抜けば`BREAKOUT`。
- 終値が20日高値の2%以内であれば`BREAKOUT WATCH`。
- 過去に上抜け済みで10営業日安値を維持していれば`TRENDING`、割れば`FAILED`。
- 14日ATRをTurtleのN相当として保存し、ATR%、Breakout Price、Stop Distanceを記録する。
- 参考Stopは`現在値 - 2 ATR`。Volumeは記録するが必須条件にしない。
- 50MA、200MA、50MA > 200MAはTrend Stateとして保存する。CoreのTrend条件へは流用しない。
- setup_idはBreakout日またはWatch Pivotに固定するため、同じBreakoutを毎日新規保存しない。

### Earnings Momentum

- 直近EPS成長率25%以上、売上成長率20%以上、年次EPS成長が加速、Data Coverage 50%以上を
  同時に満たせば`STRONG EARNINGS MOMENTUM`。
- EPS成長率25%以上は`EARNINGS MOMENTUM`、プラス改善は`IMPROVING`、それ以外は`NEUTRAL`。
- 年次EPS履歴から直近の前年比成長と一つ前の前年比成長を比較し、EPS Accelerationを算出する。
- 売上、営業利益の前年同期比を利用可能な範囲で保存する。赤字から黒字は通常の成長率と分離し、
  `turnaround_flag`として記録する。前年比400%超は`anomaly_flag`を付ける。
- 安定した無料取得経路がないEarnings Revisionは推測せずN/Aのまま保存する。
- 株価Breakoutは条件にしない。財務値の出所と利用可能性に応じてFidelityとCoverageを記録する。

### Sector Relative Strength

- JPX公式上場銘柄一覧の東証33業種分類を使用する。
- 分析日までの各構成銘柄の1か月、3か月、6か月Returnを平均し、20%、30%、50%で加重する。
- 同日Universe内でSector Rankと0〜100 Percentileを算出し、6か月Returnから1306 ETFの
  同期間Returnを引いたSector Relative Strengthも保存する。
- Sector Percentile 90以上かつ個別株Momentum Percentile 80以上は`LEADING SECTOR`。
- Sector Percentile 80以上かつ個別株Momentum Percentile 80以上は`STRONG`。
- Sectorだけ、または個別株だけが強い場合は`SECTOR STRONG` / `STOCK STRONG`とし、
  Experimental Alignmentには加えない。

## Experimental AlignmentとCross Signal

Alignmentは次の陽性Stateの数（0〜3）である。

- Turtle: `BREAKOUT`または`BREAKOUT WATCH`
- Earnings: `STRONG EARNINGS MOMENTUM`または`EARNINGS MOMENTUM`
- Sector RS: `LEADING SECTOR`または`STRONG`

陽性手法名を`TURTLE+EARNINGS+SECTOR_RS`のようにCombinationとして保存する。
Core Signal発生日にはCore StateとExperimental Combinationを同じ時点でImmutableに関連付け、
`CORE_BREAKOUT+TURTLE`等を後から比較できる。Core Signalでない銘柄も、いずれかのExperimental
手法が陽性になった時点で独立Snapshotを保存する。

## Snapshot、Forward Performance、Control

`experimental_snapshots`は`experimental_signal_id`を主キーに`INSERT OR IGNORE`で固定する。
同じsetupとVersionを再実行してもSnapshotを上書きしない。`experimental_history`はSignal日から
0、1、5、10、20営業日を追跡し、Return、1306 ETF Relative Return、MFE、MAE、State、Failedを保存する。

各Experimental Signal発生日の同一Universeから、陽性銘柄を除外して次を固定する。

- Random: Signal IDとSelection Versionをseedに20銘柄を決定論的に選択。
- Matched: 流動性、Momentum、市場、JPX規模、株価水準が近い順に最大8銘柄を選択。

Control membershipも`INSERT OR IGNORE`で再選択しない。ControlはExperimental専用の
`experimental-control-v1`を使用し、Core Controlを変更しない。PerformanceとSummaryはStrategy、
State、Alignment、Combination、Core Crossごとに、1/5/10/20営業日の平均・中央値・上昇率、
Market / Random / Matched超過、MFE、MAE、標準偏差、標準誤差、95%信頼区間、Sample Strengthを出力する。
サンプル数から「有効」「無効」を自動判定しない。

## 公開Export

既存`/validation/`はCore専用のまま維持し、Experimentalは固定URL`/experimental/`へ分離する。

- `index.json`: 期間、件数、Version、実験開始日、経過営業日、ファイル一覧
- `signals.csv/json`: Immutable Experimental SnapshotとCore Cross
- `history.csv/json`: 日次StateとForward Observation
- `performance.csv/json`: 1/5/10/20営業日Performanceと各Controlとの差
- `controls.csv/json`: 固定Control membership
- `control_performance.csv/json`: Control銘柄ごとのForward Performance
- `summary.csv/json`: Versionを混在させない集計
- `state.json`: GitHub Actions cache消失時の継続用状態

既存公開版の`state.json`を毎回先に取り込み、GitHub Actionsが平日19:00（日本時間）に更新する。
開始日前のCore Signalや当時のUniverseを復元できない日について、Experimental StateやControlを
遡及生成しない。

## Data SourceとFidelity

- OHLCV、移動平均、ATR、個別・Sector Return: Yahoo Finance非公式経路。
- 上場銘柄、東証33業種、市場、規模区分: JPX公式月次一覧。
- EPS、売上、営業利益: EDINET API v2を優先し、既存Yahoo財務キャッシュで補完。
- Benchmark: 1306 ETFをTOPIX代理として利用。

EDINETは法定開示中心で速報性が低く、文書タグ差による欠損がある。Yahoo財務は正確な公表日を
保証できないため、取得時点のキャッシュだけを使いFidelityを下げる。欠損は不適合へ変換せずN/Aとする。

## Look-ahead / Survivorship Bias

- Donchian Highは必ずT-1まで、ATRと移動平均はTまでの値だけを使用する。
- EarningsはSignal時点でDBに保存済みの財務値だけを使用し、未来の発表を遡及適用しない。
- Sector Rank、個別Momentum、Control matchingはT時点までの価格と同日Universeだけを使用する。
- Forward Return、MFE、MAEをSignal判定やControl選択へ使用しない。
- 現在のJPX Universeを利用するため、上場廃止銘柄まで含む完全なSurvivorship Bias除去は未対応。
- 1306 ETFは公式TOPIXではなく、配当・追跡誤差がある。Yahoo Financeの継続性も保証されない。

この制約を踏まえ、約1か月後にTurtle、Earnings、Leading Sectorの単独優位性、Alignment別の差、
Core単独対Core + Experimental、Matched Control超過をVersion固定の観測データで判断する。
