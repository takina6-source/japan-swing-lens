# Phase 2A FORMING MINT Gate Design

Status: **PROPOSED — HUMAN REVIEW REQUIRED**  
対象run: `smrun1:20260911:ae2c9fb95792da5856878c82`  
設計日: 2026-09-13 JST

## 1. Decision Summary

現行`sm1/smt1/idr1`のCandidate gate実装は設計どおり動いている。しかし、手法stateの
`SETUP FORMING`は「その手法の必須条件が成立した」という意味ではない。特にMinerviniと
Qullamaggieは、共通の`contraction_widths`が成立すると、他の必須条件がFAILでも
`SETUP FORMING`を返し得る。この2 stateをそのまま2票として数えた結果、CURRENT 964件中
811件をMINTし、745件がFORMINGになった。

新規FORMING MINTには、手法state数ではなく次を要求する案を採用候補とする。

1. 必須条件が実際に確認できた`qualified active strategy`が2手法以上。
2. その必須条件の根拠が、Market / Trend、Stock Quality / Momentum、Entry Setupの
   3つの意味層すべてを覆う。

WATCH/BREAKOUTのMINT条件は現行のまま維持する。初回固定入力では59 WATCHと7 BREAKOUTが
全件維持される。新規則のcounterfactualは合計241 MINT、内訳はFORMING 175、WATCH 59、
BREAKOUT 7である。

この意味変更は`sm1/smt1/idr1`へ上書きしない。候補versionは次とする。

| Contract | Version |
|---|---|
| State Machine transitions | `sm1`（変更なし） |
| Setup identity format | `csu1`（変更なし） |
| Candidate threshold/evidence contract | `smt2` |
| Identity decision rule | `idr2` |
| Core observation fact contract | `smo2` |

## 2. Root Cause

### 2.1 Phase 2A adapter/aggregation

AdapterはCore detailの5手法stateを正しく読み、`SETUP FORMING / BREAKOUT WATCH / BREAKOUT`を
各1票として再計算していた。Core宣言値との差異reason codeは0件である。したがって、
Phase 2Aの読み違い、列ずれ、集計漏れではない。

### 2.2 Upstream state semantics

現行`pivot_state`は`pivot is None or not qualified`の場合でも、`forming=True`なら
`SETUP FORMING`を返す。MinerviniとQullamaggieは同じcontraction判定を`forming`へ渡すため、
trend、momentum、52週位置等の必須条件がFAILでも、両方が同時にFORMINGになり得る。

実例の1812 鹿島建設では、Core上のactive票はMinerviniとQullamaggieの2票だったが、
Minerviniの8必須条件とQullamaggieのmomentum/trend必須条件はFAILであり、共通する
contractionだけがPASSしていた。

これは「Core表示が必ず誤り」と断定するものではない。CoreのFORMINGは弱い早期観測ラベルとして
成立し得る。しかし、永続UIDのMINT票へ無条件に転用するには意味が緩すぎる。

## 3. Concepts

### 3.1 Raw active strategy

次の手法stateを持つ5トレンド手法。現行`aligned_trend_strategy_count`と同じ意味である。

```text
strategy_state in {SETUP FORMING, BREAKOUT WATCH, BREAKOUT}
```

### 3.2 Evaluated required condition

`role == REQUIRED`かつ`verdict != N/A`のcondition。verdictは次に正規化する。

| 表示 | 正規値 |
|---|---|
| `○` | PASS |
| `△` | WARN |
| `×` | FAIL |
| `N/A` | NA |

### 3.3 Qualified active strategy

手法`S`について次をすべて満たす場合だけtrueとする。

```text
raw_active(S)
and count(evaluated REQUIRED conditions of S) >= 1
and count(FAIL among evaluated REQUIRED conditions of S) == 0
```

WARNは境界内の観測事実として許容する。N/AはPASSへ変換せず、分母から除く。全必須条件がN/Aなら
qualifiedにしない。

### 3.4 Required evidence layers

qualified active strategyのevaluated REQUIRED conditionsのうちPASS/WARNとなったconditionから、
次の意味層を集合として作る。

- `MARKET_TREND` = `Market / Trend`
- `QUALITY_MOMENTUM` = `Stock Quality / Momentum`
- `ENTRY_SETUP` = `Entry Setup`

手法数ではなく意味の異なる証拠が存在することを確認するための集合であり、統計的独立性を
保証するものではない。同一conditionを複数手法が使用しても同じlayerは1回だけ数える。

## 4. Exact MINT Rule

### 4.1 Common validity gate

全stateで次を必須とする。

```text
observation_status == CURRENT
and close is finite and close > 0
and observed_primary_pivot exists
and observed_primary_pivot.price is finite
and observed_primary_pivot.price > 0
```

stale、missing、invalidはIdentity Decisionへ渡さず、既存のobservation quality処理へ戻す。

### 4.2 FORMING

```text
if core_observed_state == SETUP FORMING:
    MINT iff
        common_validity_gate
        and raw_aligned_strategy_count >= 2
        and qualified_active_strategy_count >= 2
        and required_evidence_layers contains MARKET_TREND
        and required_evidence_layers contains QUALITY_MOMENTUM
        and required_evidence_layers contains ENTRY_SETUP
    otherwise NO_SETUP
```

`raw_aligned_strategy_count >= 2`はqualified countから論理的に導けるが、Core宣言値との整合監査と
fail-closedのため明示的invariantとして残す。

FORMINGではPivot距離上限を追加しない。FORMINGは突破直前ではなく、追跡を開始する形成段階であり、
距離を要求するとWATCHとの意味が重複する。Pivot距離は保存し、将来のResearchで別検証する。

### 4.3 WATCH and BREAKOUT

```text
if core_observed_state in {BREAKOUT WATCH, BREAKOUT}:
    MINT iff common_validity_gate and raw_aligned_strategy_count >= 2
```

CoreのWATCH/BREAKOUTはPivot proximity/cross等のより強いtriggerを既に含むため、FORMING用の
3-layer gateを強制しない。固定入力では66件すべてが維持される。

### 4.4 Other Core states

`EXTENDED / FAILED / PULLBACK / NOT QUALIFIED`等は新規MINTしない。Connorsは引き続き
Core setup MINT票へ数えない。

## 5. Evaluation Order

1. observation qualityとmarket dateを検証する。
2. close/Pivotのfinite positiveを検証する。
3. Core stateを分類する。
4. 5手法のraw stateを正規化する。
5. 手法ごとにREQUIRED conditionをPASS/WARN/FAIL/NAへ正規化する。
6. qualified active strategyを決定する。
7. required evidence layer集合を作る。
8. state別MINT ruleを評価する。
9. `MINT / NO_SETUP`と全監査値・reason codeを同じtransactionへ保存する。

入力field欠落、未知verdict、未知layer、宣言countと再計算countの不一致は、quietなNO_SETUPではなく
structured rejectionとする。既存UIDがある場合は新MINT ruleをLINK判定より先に適用しない。

## 6. Alternatives Considered

| 案 | MINT | FORMING | 長所 | 不採用理由 |
|---|---:|---:|---|---|
| 現行smt1 | 811 | 745 | 早期候補を広く保持 | Full Scopeの84.1%。相関票と必須FAILを区別しない |
| Count 3以上 | 671 | 605 | 変更が小さい | 必須FAILのFORMINGを大量に残す |
| Count 4以上 | 335 | 274 | 件数を大きく削減 | 意味ではなく票数で調整。相関問題が残る |
| Count 5 | 92 | 64 | 非常に厳格 | 5手法すべてを暗黙必須化し、N/A偏りが強い |
| FORMINGのみ4以上 | 340 | 274 | WATCH/BREAKOUTを維持 | FORMING内の相関票を解消しない |
| Min/Q共有clusterを含む3 cluster | 341 | 275 | Min/Q二重票を抑制 | clusterが手法名ベースで、必須FAILを見ない |
| Strong strategy + corroboration | 155 | 89 | 厳格 | Min/Qを特権化し、銘柄特性への偏りが強い |
| WATCH/BREAKOUTまでMINTしない | 66 | 0 | false startが少ない | 形成過程を追跡するPhase 2Aの目的を失う |
| Qualified 2以上 | 246 | 180 | 必須FAILを除外 | 5件は意味層が2つしかない |
| **Qualified 2以上 + 3 layers** | **241** | **175** | 必須条件と異種証拠を両方要求 | 新しい観測factとversionが必要 |

採用候補は最後の案とする。候補数241を目標に選んだのではなく、現行condition contractから
「必須条件に反していない2手法」と「3つの異なる意味層」を要求した結果である。

## 7. Reason Codes

### MINT

- `MINT_ADVANCED_CORE_STATE`: WATCH/BREAKOUTの現行gate成立。
- `MINT_FORMING_REQUIRED_EVIDENCE_CONFIRMED`: FORMINGのqualified 2以上かつ3 layers成立。

### NO_SETUP

優先順位を固定する。

1. `NO_SETUP_CORE_STATE_INELIGIBLE`
2. `NO_SETUP_RAW_ALIGNED_LT_2`
3. `NO_SETUP_QUALIFIED_ACTIVE_LT_2`
4. `NO_SETUP_MISSING_MARKET_TREND_EVIDENCE`
5. `NO_SETUP_MISSING_QUALITY_MOMENTUM_EVIDENCE`
6. `NO_SETUP_MISSING_ENTRY_SETUP_EVIDENCE`

複数layerが不足する場合、primary reasonは上記順とし、全reasonを配列にも保存する。
invalid/malformedはNO_SETUP reasonへ丸めない。

## 8. Required Observation Facts

`smo2`では、既存のraw countに加えて最低限次を保持する。

- `mint_gate_rule_version`
- `qualified_active_strategy_count`
- `qualified_active_strategies`
- 手法別`raw_active / evaluated_required_count / required_fail_count / qualified_active`
- `required_evidence_layers`
- `mint_gate_result`
- `mint_gate_reason_codes`

condition原票全体をPhase 2Aへ複製しない。Adapterが同一runのCore detailから上記最小factを導出し、
入力hashへ含める。未知値や再計算不一致はfail closedする。

## 9. Existing 811 UID

1. 初回Cutoverで発行済みの811 UID、MINT decision、event anchor、Pivot revisionを変更しない。
2. `smt2/idr2`は明示した将来market dateからのみ新規MINTへ適用する。
3. 既存UIDのLINKはcandidate MINT gate不成立でも現行continuity ruleに従って継続できる。
4. 新条件不成立をFAILED、EXPIRED、CLOSEDへ変換しない。
5. 新lineageや再Cutoverは不要とする。identityの出生事実は有効であり、選択基準だけをversion化する。
6. ただし、smt1由来でsmt2を一度も満たしていないFORMING setupは、What Changed公開候補から
   `LEGACY_GATE_ONLY`として隔離する案を採用する。
7. `LEGACY_GATE_ONLY`を既存`distribution_eligible`だけで安全に表現できるかはImplementation前の
   Migration Designで確認する。phaseや過去decisionを上書きしてはならない。

このため、実装前に小さなMigration/Activation Goalを設け、適用開始日、既存UIDの監査field、
consumer除外規則を確定する。Gate Cはその完了と新ruleのproduction shadow確認まで保留する。

## 10. Bias and Data Availability

新ruleはCAN SLIMまたは年次EPSの存在を必須にしない。固定入力でAnnual Earningsは、現行811件の
COMPLETE 791 / PARTIAL 19 / INSUFFICIENT 1に対し、新候補241件ではCOMPLETE 236 / PARTIAL 5で
あった。これは明示条件ではなく他の必須証拠との共変動であり、複数日監査が必要である。

| 指標 | 現行811 | 新候補241 |
|---|---:|---:|
| 非流動性判定 | 313 (38.6%) | 59 (24.5%) |
| Proxy Pivot | 437 (53.9%) | 120 (49.8%) |
| Coverage中央値 | 92.5% | 92.5% |

流動性やPivot fidelityをMINT必須条件にはしない。これらは追跡価値とは別軸であり、必要なら
distribution/entry suitability側で扱う。

## 11. Version and Activation Boundary

- `sm1`と`csu1`は維持する。
- `smt2`と`idr2`を同じactivation market dateから使用する。
- `smo2`を同時に導入し、監査factがない状態で`idr2`を実行しない。
- activation date以前のrunを再計算・上書きしない。
- seed restore時は各UIDの出生rule versionと最新観測rule versionを区別する。
- `smt1`seedから`smt2`継続するmigrationを固定fixtureでrehearseする。
- 問題時は最後の検証済み`smt1`seedへrollbackし、同じ日付の異なる入力をmergeしない。

## 12. Human Review Gate

Implementation Goal作成前に、次を個別承認する。

1. qualified active strategyの定義。
2. FORMINGで3 evidence layersすべてを要求すること。
3. WATCH/BREAKOUTは現行条件を維持すること。
4. `sm1/csu1`維持、`smt2/idr2/smo2`導入。
5. 既存811 UIDを保持し、新規MINTへだけ非遡及適用すること。
6. `LEGACY_GATE_ONLY`のMigration/Activation GoalをImplementationより前に作ること。
7. 新ruleの複数market date production shadowが終わるまでGate Cを保留すること。

この承認は設計承認であり、実装、commit、push、Release変更、seed変更、Gate C進行を許可しない。

## 13. Known Limitations

- counterfactualは1 market dateであり、将来成績による有効性検証ではない。
- evidence layerは意味上の非重複を要求するが、統計的独立性を保証しない。
- Coreの弱いFORMING表示自体は変更しないため、UI上のFORMING数とPhase 2A MINT数は異なり得る。
- `LEGACY_GATE_ONLY`の永続表現は本設計だけでは確定していない。
- 新ruleは候補品質を改善する仮説であり、投資収益を保証しない。
