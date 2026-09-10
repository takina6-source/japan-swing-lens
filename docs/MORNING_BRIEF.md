# Morning Brief Phase 1 — Why Hot?

Coreの生成済みsnapshot/detailを、説明用のJSONへ変換する独立Adapterです。株価取得・DB接続・分析再実行・再ランキング・LLM呼出しはありません。

## 実行

リポジトリのPython環境で実行します（依存はpyproject.toml）。

```sh
python scripts/export_briefing.py
```

入力は `public/dashboard/data/snapshot.json` と選択銘柄の `data/details/{code}.json`、configは `config/thresholds.yaml`。出力は `public/dashboard/briefing/latest.json`。既定Top20、`--limit 0`で全候補、その他の非負整数で件数指定ができます。候補配列の順位を保持し、欠損detailの行も繰り上げません。

保存済み入力を別ディレクトリへ変換する例:

```sh
python scripts/export_briefing.py --dashboard-root public/dashboard --output docs/examples/morning-brief --limit 3
```

通常CLIではstrategy/threshold versionは未確認としてnullにし、`VERSION_UNVERIFIED`を返します。logic_versionの文字列が一致するだけで同じ設定のrunだったとは推定しません。

既存workflowはCore Export成功後、同一job/checkoutで次を実行します。

```sh
python scripts/export_briefing.py --same-run-config
```

`--same-run-config`は「このconfigでCore Exportを直前に完了した」という呼出側の明示的な保証です。古い単独成果物を読む際に付けないでください。configとsnapshotのlogic_version不一致やversion欠損があれば、この指定があっても未確認になります。

## 入力整合性の範囲

snapshotのcode/日付/件数、detailのcode/日付、共有Core項目・手法state・metrics.priceを照合します。不一致detailは利用せずsnapshot値だけを採用し、日付はnullにします。銘柄別as_ofが全体as_ofより古い場合は、古い観測としてその日付を保持します。

読み取りの前後でsnapshotの内容hashを比較します。入力の世代混在を減らす検査ですが、既存detailにはrun IDがないため、同じ値を持つ別runのdetailを完全に識別するものではありません。同一jobでの直列実行が通常運用の前提です。

hashはJSON内容をキー順で正規化したSHA256です。snapshot、選択detail、許可したconfig項目を記録します。detail不一致時も、照合した入力のhashは残します。config hashはversionとplanのR倍率のみで、config全文や秘密情報を出力しません。hashは同run生成の証明ではありません。

## 出力と意味

[JSON Schema](../schemas/morning_brief.schema.json)とJSON内の`definitions`を参照してください。

- `stocks`：順位、銘柄別日付、Core state、3種類の合致数、Pivot、plan、Momentum/流動性、品質、6手法の条件根拠。
- `strategies.*.conditions`：既存○/△/×/N/Aと日本語label・元用語・評価値・比較値。全detailや原票の複製ではありません。
- `issues`：全体と銘柄別の理由コード。N/Aは取得失敗と断定せず、構造的不足として区別します。
- `versions.status`：`SAME_JOB_ATTESTED`または`UNVERIFIED`。
- `generated_at`：Adapterの生成時刻。`snapshot_generated_at`、全体`as_of`、銘柄`analysis_date`と区別します。

`aligned_count`と`confluence`は別の既存値です。`breakout_count`の分母はConnorsを除く5手法。Momentumは分析母集団内の順位です。価格は調整済み日足終値、売買代金は終値×出来高の概算であり、リアルタイム価格や取引所の実売買代金ではありません。

Pivot距離は`(pivot/close-1)*100`で、正ならPivotが終値より上です。risk_per_shareは有効な帯に終値を収めたreference entryとstopの差です。現在サポートする前提は`pivot-consensus-v1`かつ同run config確認済みのplanで、既存1R/2R値と倍率の整合性も検査します。前提不明・不整合ではriskはnullです。entry/stop/targetは元値を転記し、欠損targetを作りません。

Coverageは条件充足率、Confidenceは既存品質評価です。`COVERAGE_INCOMPLETE`は100%未満という事実の表示で、新たな投資判定閾値ではありません。

## statusと失敗

| status | 意味 | CLI終了コード |
|---|---|---:|
| OK | 選択行・メタデータに診断事項なし | 0 |
| PARTIAL | 利用できる行があり、N/A・品質注意・未確認version等を併記 | 0 |
| UNAVAILABLE | 空/無効snapshot、入力読取失敗、変換失敗等。stocksは空 | 1 |

N/Aが多いCoreデータではPARTIALが通常起こり得ます。PARTIALは分析ジョブ失敗と同義ではありません。ログは件数・理由・時間だけで、例外本文やデータ全文は出しません。

Schema検証後、一時ファイルからlatestを原子的に置換します。変換失敗時は当runのUNAVAILABLEで旧latestを置き換えます。書込失敗時は古いlatestを削除し、削除も不能なら非ゼロ終了します。workflowではBriefステップの失敗を独立扱いにしてlatestを除去し、既存Core成果物の公開を続けます。除去も失敗した場合は公開ジョブが停止するため、古い成功ファイルの誤公開を避けられます。

この実装でpush/公開はしていません。workflowが公開環境へ反映された後の配置先は`briefing/latest.json`です。

## オフライン検証と出力例

```sh
python -m pytest -q tests/test_briefing.py tests/test_committee_export.py tests/test_export_web.py tests/test_observer.py
python scripts/fixed_input_regression.py
```

固定入力回帰は合成入力と一時DBのみを利用します。本番DBを変更せず、新規市場データを取得しません。

[出力例](examples/morning-brief/latest.json)は、**2026-09-03基準・2026-09-04生成の保存入力**から3銘柄を変換したものです。現在の株価情報として利用しないでください。configの対応未確認を保持するため、strategy/threshold versionと1株riskはnullです。

## Phase 2Aへの申し送り

Phase 2A設計直前にRuntime Verificationでscope965対保存964、日次970/2/968/964等の母集団差・部分run・欠測を確認します。その後、setup同一性とstate machineを設計します。

日次保存は**最小限の観測事実フィールドのみ**とし、full candidate/detail/チャート/原票の複製は除外します。Phase 1のTop20は説明対象件数であり、Phase 2Aの保存母集団ではありません。このAdapterには履歴やsetup同一性の仕様を追加していません。
