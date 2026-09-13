# Japan Swing Lens — FORMING MINT Gate Design Goal

作成日: 2026-09-13 JST

## 0. Goal

Phase 2A初回Cutoverで、CURRENT 964件中811件（84.1%）がMINTされ、そのうち745件が
`FORMING`へ入った実測を前提として、**新規FORMING setupへ`core_setup_uid`を発行する条件だけを
独立して再設計する**。

本Goalでは、次を曖昧さなく決定する。

1. 「追跡する価値がある形成中setup」の意味。
2. 相関の強い手法状態を独立した複数票として数えるか。
3. `SETUP FORMING`、`BREAKOUT WATCH`、`BREAKOUT`でMINT条件を分けるか。
4. 合致数、独立証拠層、Pivot距離、手法別品質をどのように組み合わせるか。
5. 新条件のversion境界と、既にMINT済みのUIDへの非遡及方針。
6. 後続Implementation Goalがそのままfixtureとコードへ落とせるboolean rule、reason code、
   受け入れ条件。

このGoalは**設計専用**である。product code、閾値設定、DB、seed、Release、workflow、Pagesを
変更しない。Gate Cへ進まない。設計結果を人間が承認するまで、新MINT条件を本番へ適用しない。

## 1. Fixed Audit Evidence

初回Cutover `smrun1:20260911:ae2c9fb95792da5856878c82` の同一入力再現で確認した次の値を、
設計の固定事実として扱う。

### 1.1 Run completeness and identity

| 項目 | 件数 |
|---|---:|
| Full Screening Scope | 965 |
| CURRENT | 964 |
| INSUFFICIENT_PRICE_HISTORY | 1 |
| LINK | 0 |
| MINT | 811 |
| AMBIGUOUS | 0 |
| NO_SETUP | 153 |

### 1.2 Persistent phase

| Phase | 件数 |
|---|---:|
| FORMING | 745 |
| WATCH | 59 |
| POST_BREAKOUT | 7 |
| FAILED / RETRY_WATCH / EXPIRED / CLOSED | 各0 |

### 1.3 FORMINGのaligned分布

| `aligned_trend_strategy_count` | 件数 | FORMING内比率 |
|---:|---:|---:|
| 2 | 140 | 18.8% |
| 3 | 331 | 44.4% |
| 4 | 210 | 28.2% |
| 5 | 64 | 8.6% |

「ちょうど2」だけへの集中ではない。FORMINGの81.2%は3手法以上がactiveである。

### 1.4 FORMING内の手法別active率

| 手法 | Active件数 | FORMING内比率 |
|---|---:|---:|
| Minervini | 732 | 98.3% |
| Qullamaggie | 730 | 98.0% |
| Darvas | 582 | 78.1% |
| Weinstein | 231 | 31.0% |
| CAN SLIM | 158 | 21.2% |

MinerviniとQullamaggieは730件で同時activeである。両者とDarvasはtrend、contraction、
price structure、Pivot等の入力を部分的に共有しており、単純な3票を独立した3証拠とみなせない。

### 1.5 Existing implementation behavior

現行`sm1/smt1`は次をすべて満たすCURRENT観測をMINTする。

```text
core_observed_state in {SETUP FORMING, BREAKOUT WATCH, BREAKOUT}
and aligned_trend_strategy_count >= 2
and close is finite and > 0
and observed_primary_pivot is finite and > 0
```

`aligned_trend_strategy_count`は、Minervini、Qullamaggie、CAN SLIM、Weinstein、Darvasのうち、
手法stateが`SETUP FORMING / BREAKOUT WATCH / BREAKOUT`のものを各1票として数える。
Connorsは数えない。Adapter再計算値とCore宣言値の不一致は0件であった。

現行コードは現行設計どおり動いている。したがって本Goalは集計バグ修正ではなく、
MINT eligibility semanticsとthreshold contractの再設計として扱う。

## 2. Absolute Principles

1. `core_setup_uid`は「ランキング上位」の印ではなく、複数日追跡するsetup identityである。
2. ただし、単一の緩い形成シグナルだけでFull Scopeの大半へUIDを発行しない。
3. 相関する手法を、根拠を示さず独立票として扱わない。
4. Core observed state、手法state、condition evidence、persistent phase、identity decisionを混同しない。
5. MINT eligibilityと既存UIDのLINK eligibilityを分離する。新条件不成立だけを理由に既存setupを
   FAILED、EXPIRED、CLOSED、NO_SETUPへ変換しない。
6. T日のMINTはT時点で利用可能な情報だけを使い、将来成績で閾値を後付けしない。
7. Top 20、rank、将来リターン、裁量判断を通常runのMINT必須条件にしない。
8. 無料データ構成で毎営業日再現可能な既存Core factだけを使用する。
9. 欠測やN/Aを自動的な合格票として扱わない。欠測の多い手法を有利にも不利にも暗黙変換しない。
10. 閾値変更はversion化し、過去runや既存UIDへ暗黙に遡及適用しない。
11. Candidate件数を任意の目標値へ合わせるためだけに閾値を調整しない。
12. 同じ入力・同じrule versionでは同じMINT/NO_SETUP結果になる。

## 3. Scope

### In Scope

- 新規Core setupのFORMING MINT条件。
- WATCH/BREAKOUT MINT条件との共通部分・差分。
- `aligned_trend_strategy_count`の意味と限界。
- 手法間の共有入力・相関を考慮した独立証拠の定義。
- state-sensitive threshold、evidence-family gate、二段階候補化の比較。
- boolean rule、評価順、境界値、reason code、監査field。
- `smt1`から新versionへ切り替える場合のversion境界。
- 既存811 UIDを削除・再採番しない移行方針の設計。
- 固定入力によるcounterfactual件数、標本、fixture、後続実装へのhandoff。

### Out of Scope

- product code、config、DB schema、seed、Release、workflowの変更。
- 初回Cutoverのやり直し、既存Release assetの削除・上書き。
- 既存UIDの削除、再採番、過去MINT decisionの改変。
- State MachineのT01–T26、qualified cross、FAILED、RETRY_WATCH、EXPIRED、CLOSEDの再設計。
- setup UIDのformat/hash、identity ledger構造の再設計。
- Core 6手法そのものの売買ルール変更。
- Coreランキング、Morning Brief、Experimental、Research、Validationのスコア変更。
- What Changed JSONのPages公開、Gate C承認。
- 新しい有料データ/APIの導入。

本Goal中にPhase遷移やidentity formatの変更が不可避と判明した場合、範囲を拡張せず、
別Design Amendmentが必要であると記録する。

## 4. Questions the Design Must Answer

1. FORMINGは何を満たした時点から、永続identityを持つべきか。
2. `SETUP FORMING`という同名stateでも、手法ごとに証拠強度が異なる問題をどう扱うか。
3. MinerviniとQullamaggieの同時activeを2票と数えてよい条件は何か。
4. Darvasを加えた3票が、同じcontraction/price structureの重複確認にすぎない場合をどう扱うか。
5. 手法数ではなく、Trend、Momentum/Quality、Consolidation、Pivot proximity等の異なる
   evidence familyを要求すべきか。
6. FORMINGだけを厳格化し、WATCH/BREAKOUTは現行条件を維持できるか。
7. Pivotまで遠いFORMINGを追跡する価値と、UID大量発行のコストをどう比較するか。
8. 財務N/Aの多いCAN SLIMを必須条件にせず、かつ欠測銘柄を過度に優遇しない方法は何か。
9. 新条件導入後、既存UIDはどのrule versionでLINK・監視を継続するか。
10. 新rule不成立になった既存FORMINGをどのように表示・配布対象外にするか。既存phaseを捏造変更せず
    表現できない場合、必要な別設計は何か。

## 5. Required Alternatives

最低限、次の5案を同じ固定入力で比較する。複数案の組み合わせも許可する。

### A. Count-only threshold

- FORMINGの最低合致数を3、4、5へ変更する案。
- 現行入力での参考通過数は、3以上671件、4以上335件、5のみ92件。
- 相関した票を増やすだけになる限界を明記する。

### B. State-sensitive threshold

- FORMINGは厳しく、WATCH/BREAKOUTは別の最低合致数とする。
- 例をそのまま採用せず、各stateで永続追跡を開始する合理性を説明する。

### C. De-correlated evidence-family gate

- 手法名の票数とは別に、共有入力を整理したevidence familyを定義する。
- 少なくとも異なる2 familyの成立を要求する案を検証する。
- 同じprice/contraction factを複数手法が使っても、同じfamilyの1証拠として扱う候補を含める。

### D. Strong-strategy plus corroboration

- 1つの厳格な形成条件と、別familyの補強条件を組み合わせる案。
- 特定手法だけを恒久的な必須条件にして、財務N/Aや銘柄特性による不公平を生まないか評価する。

### E. Two-stage observation before MINT

- FORMINGを一時候補として永続UIDなしで観測し、別日確認またはWATCH昇格時にMINTする案。
- これが現行identity/state schemaの範囲内か、別のprovisional identity設計を必要とするかを明示する。
- 現行範囲を超える場合は採用を強行せず、別Goalへ切り出す。

## 6. Evidence Independence Analysis

5手法について、最低限次の表を作る。

| 手法 | FORMINGに必要な主要条件 | 共有する入力 | 独自入力 | Pivot proximity必須性 | N/A影響 |
|---|---|---|---|---|---|

さらに、次を定量化する。

- FORMING内の手法別active率。
- 手法ペアごとの同時active件数、条件付き確率、Jaccard係数。
- 上位active combinationと件数。
- 同じunderlying factを共有する条件の対応表。
- 合致数を1増やしているが、新しい独立証拠を追加していない組み合わせ。

「著名投資家名が異なる」ことを、証拠の独立性の根拠にしない。

## 7. Required Counterfactual Evaluation

初回Cutoverの固定入力を変更せず、各代替案について最低限次を出す。

- MINT / NO_SETUP件数。
- Core state別MINT件数。
- aligned count別MINT件数。
- evidence family別の通過内訳。
- 現行811件から新規則で除外される件数と理由。
- 現行NO_SETUPから新規則でMINTへ逆転する件数。原則0でなければ理由。
- FORMING / WATCH / BREAKOUTの代表標本と境界標本。
- 財務N/A、低流動性、Proxy Pivot等の部分母集団で極端な偏りがないか。

可能なら、同じlogic/configで利用可能な複数market dateも比較する。ただし異なるversionや
不完全な過去artifactを同列に混ぜない。単一日しか検証できない場合は、その制約を明記する。

候補数の少なさ自体を成功基準にしない。目的は、異なる意味の証拠を持つ追跡可能なsetupを
再現可能に選ぶことである。

## 8. Required Decision Contract

採用案について、後続実装者が解釈せず実装できる形で次を確定する。

1. 入力fieldと許容値。
2. exact boolean expressionまたは決定表。
3. FORMING、WATCH、BREAKOUTごとの評価順。
4. N/A、missing、invalid、staleの扱い。
5. evidence familyの定義と、1 factを二重計上しない規則。
6. MINTとNO_SETUPのreason code。
7. 境界値がinclusiveかexclusiveか。
8. rule/threshold version名。意味変更を`sm1/smt1`のまま上書きしない。
9. observation artifactへ保存すべき最小監査field。
10. 同一入力replayの決定性とidempotency。

採用案が既存`CoreObservation`で表現できない場合、必要fieldを設計結果へ列挙するが、
このGoalではschemaやコードを変更しない。

## 9. Existing UID and Cutover Policy

既に公開seedへ記録された811 UIDを事実として保持する。設計結果は最低限、次を決定する。

- 過去のMINT decision、event anchor、UIDを削除・改変しない。
- 新ruleを過去観測へ適用して「MINTされなかったこと」にしない。
- 新ruleの適用開始market dateとversionを明示する。
- 既存UIDのLINKは、新規MINT gateとは別契約として扱う。
- 新rule不成立の既存FORMINGを継続監視するか、distribution対象から外すか、別Human Reviewが
  必要かを比較する。
- 新lineage/re-cutoverが必要か、同じ`live-sm1`でthreshold versionだけを上げられるかを判断する。
- Release assetを上書き・削除せず、rollback可能性を維持する。

既存UID処理が本Goalだけでは安全に決められない場合、Implementationへ進まず、専用Migration Goalを
先に要求する。

## 10. Required Fixtures and Future Tests

設計結果には最低限、次のfixtureを定義する。

1. FORMING、aligned=2だが同一familyだけ。
2. FORMING、aligned=2で異なるfamily。
3. FORMING、aligned=3だが同一underlying factに相関。
4. FORMING、複数の独立familyが成立。
5. WATCH、最低境界。
6. BREAKOUT、最低境界。
7. 有効Pivotなし、close不正、stale、N/A。
8. 財務N/Aだが価格・trend・structureの独立証拠あり。
9. 既存UIDへのLINK時に新MINT gate不成立。
10. 同一入力再runでdecisionが変わらない。

各fixtureに、期待する`MINT / NO_SETUP / LINK`、reason code、rule versionを付ける。

## 11. Deliverables

本Goalの実行成果物として、最低限次を作成する。

1. `docs/PHASE2A_FORMING_MINT_GATE_DESIGN.md`
   - 現状原因、代替案比較、採用案、exact rule、version、移行方針。
2. `docs/PHASE2A_FORMING_MINT_GATE_HANDOFF.md`
   - 後続Implementation Goal向けのfield、reason code、fixture、変更候補path、禁止事項。
3. 読み取り専用counterfactual report
   - 同一Cutover入力に対する各案の件数・分布・代表例。
4. Human Review Gate checklist
   - 採用条件、既存UID処理、version、Gate C保留を個別承認できる形にする。

設計成果物以外のproduct code、test code、config、DB、seed、workflow、public artifactは変更しない。

## 12. Acceptance Criteria

次をすべて満たしたときだけ、本Design Goalを完了とする。

1. 811 MINT / 745 FORMINGを再現し、母数と除外1件を説明している。
2. Adapter/集計バグではなく、現行定義どおりの結果であることを証拠付きで確認している。
3. 5案以上を同じ入力・同じ比較軸で評価している。
4. 手法名の数ではなく、underlying evidenceの重複を定量・定性の両方で評価している。
5. FORMINGのMINT条件をexact boolean ruleまたは完全な決定表で確定している。
6. WATCH/BREAKOUT条件を維持するか変更するかを明示している。
7. N/A、財務欠損、Proxy Pivotによる偏りを検討している。
8. 現行811 UIDを削除・再採番・遡及改変しない方針を示している。
9. 新versionの適用開始点とrollback境界を示している。
10. 後続実装に必要なfixtureとreason codeが揃っている。
11. Coreランキング、State Machine遷移、Pages、Release、Gate Cを変更していない。
12. 未解決事項を隠さずHuman Review Gateへ残している。

## 13. Human Review Gate

設計完了後、実装前にユーザーが少なくとも次を個別に承認する。

1. FORMINGの新MINT rule。
2. WATCH/BREAKOUTのMINT rule。
3. evidence familyの区分と相関票の扱い。
4. threshold/rule version。
5. 既存811 UIDの継続・配布・移行方針。
6. 新lineageまたはMigration Goalの要否。
7. 固定入力counterfactualの件数と代表例。
8. Gate Cを引き続き保留すること。

この承認はDesign Goal完了の確認であり、実装、push、Release変更、Cutover再実行、Gate C進行を
自動的には許可しない。

## 14. Stop Conditions

次のいずれかが発生した場合、推測で先へ進まず設計を停止して報告する。

- 同一Cutover入力を再現できない。
- Core宣言値とAdapter再計算値に未説明の不一致が見つかる。
- 採用案がsetup identity formatの変更を必要とする。
- 既存UIDを削除・再採番しなければ成立しない。
- 現行state schemaでは既存UIDの安全な扱いを表現できない。
- 無料データでは日次再現できないfieldが必須になる。
- 将来リターンを見て現在の閾値を選ぶlook-ahead設計になる。
- Gate C公開を先に行わなければ検証できない。

