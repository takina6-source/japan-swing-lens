# 無料実用版 実装仕様と判定可能性

> Core `2026.08-v6-control-observer` のPivot、Consensus、Signal／Control追跡、Validation Exportは
> [`VALIDATION.md`](VALIDATION.md) を参照してください。
> Coreから独立した `2026.09-exp-v1` の3つのExperimental Strategyは
> [`EXPERIMENTAL.md`](EXPERIMENTAL.md) を参照してください。

## 取得経路

標準経路は次の3段構成です。

1. JPX公式Excelから東証の国内株式マスタを取得し、分析母集団を確定する
2. Yahoo Financeをバッチ取得し、調整済み日足をSQLiteへ差分保存する
3. 任意のEDINET API v2キーがあれば、直近の有価証券報告書等から財務値を補完する

初回は原則2年分、保存済み銘柄は直近10日分だけ取得します。1銘柄の失敗で全体を停止せず、
200営業日以上ある銘柄だけを分析します。JPXやYahooの通信に失敗した場合は、保存済みキャッシュへ
フォールバックします。

J-Quants Freeは12週間遅延データとの任意照合だけに使い、ランキングの現在株価には使いません。
有料契約は前提にしません。

## 分析範囲

| 画面の選択 | 内容 |
|---|---|
| 主要500 | JPXの規模区分がTOPIX Core30 / Large70 / Mid400 |
| 主要500+Growth | 主要500に東証Growth市場を追加（推奨） |
| 全上場銘柄 | JPXの国内株式全体。初回取得時間と欠損銘柄が増える |

## 条件の分類

| 条件群 | 無料構成での可否 | 実装 |
|---|---|---|
| OHLCV、移動平均、RSI、ATR、Momentum | Yahoo日足から計算可能 | 実装済み |
| 1/3/6/12か月騰落率、市場内Percentile | 同上 | 実装済み |
| TOPIX対比Relative Strength | Yahoo指数との比較 | 実装済み |
| EPS・売上成長、ROE、BPS | EDINET文書に該当値があれば可能 | 実装済み、欠損はN/A |
| Minervini Trend Template | 大半が計算可能 | 実装済み |
| VCP | 目視要素あり | 60/30/15日レンジ収縮のPROXY |
| Qullamaggie Breakout | 裁量要素あり | Momentum、収縮、Pivot、出来高のPROXY |
| CAN SLIM C/L/M | 日足＋EDINETから一部可能 | 実装済み |
| CAN SLIM A | EDINET＋Yahoo年次EPS | 3期以上を実データ判定、欠損はN/A |
| CAN SLIM I、製品・経営としてのN | 定性/保有データが必要 | N/Aまたは新高値PROXY |
| Weinstein Stage 2 | 週足・目視要素あり | 150日MAと抵抗線のPROXY |
| Darvas Box | 目視要素あり | 20日高安レンジのPROXY |
| Connors RSI(2) | 計算可能 | 実装済み |
| 決算予定 | 安定した無料一括経路なし | N/A |
| Turtle / Donchian | Yahoo OHLCVから計算可能 | Experimentalで実装済み |
| Earnings Momentum | EDINET / Yahoo財務キャッシュ | Experimentalで実装済み。Revision欠損はN/A |
| Sector Relative Strength | JPX 33業種＋Yahoo日足 | Experimentalで実装済み |

## ランキング

100点方式は使わず、次の辞書順で並べます。

1. Consensus State
2. Breakout Strategy Count
3. Confluence
4. Coverage / Confidence
5. Pivot Fidelity
6. 6か月Momentumの市場内Percentile
7. 20日平均売買代金

Connorsは上昇銘柄の短期押し目を扱うため、通常のbreakout不一致をマイナス評価しません。

## 売買シナリオ

- Breakout系: 最寄りPivotから+2%までを条件付きエントリー帯とする
- 追随警告: 現在値がPivotを5%超上回る場合
- 損切り: 各手法の支持線候補と、基準エントリーから最大7%下のうち近い方
- 利確: 1Rを一部利確候補、2Rを基本目安、+20%を伸長時の参考として併記
- Extended / Failed / Not Qualified: 価格を捏造せず「見送り」

各日の値は `trade_plans` テーブルへロジックVersion付きで保存します。

## 保存と限界

SQLiteには銘柄マスタ、日足、財務、API取得ログ、条件単位の判定、売買シナリオ、
ロジックVersionを保存します。`signal_snapshots` / `signal_history`に加え、固定した対照銘柄を
`control_members`、同じ基準日からの推移を`control_history`へ保存します。

Yahoo FinanceはJPX公式データではなく、仕様・可用性は保証されません。EDINETは法定開示中心のため
速報性がなく、タグや文書差によって財務値を抽出できない場合があります。取得できない条件は
不適合扱いせず、N/Aとして明示します。
