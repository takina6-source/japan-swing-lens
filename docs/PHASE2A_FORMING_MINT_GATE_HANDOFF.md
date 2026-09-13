# Phase 2A FORMING MINT Gate — Implementation Handoff

Status: **BLOCKED ON HUMAN REVIEW AND MIGRATION/ACTIVATION DESIGN**

## 1. Adopted Candidate Contract

詳細は`docs/PHASE2A_FORMING_MINT_GATE_DESIGN.md`を正本とする。

```text
COMMON = CURRENT and finite_positive(close) and finite_positive(primary_pivot.price)

QUALIFIED_ACTIVE(strategy) =
    strategy.state in {SETUP FORMING, BREAKOUT WATCH, BREAKOUT}
    and evaluated_required_count >= 1
    and evaluated_required_fail_count == 0

FORMING_MINT =
    COMMON
    and core_state == SETUP FORMING
    and raw_aligned_count >= 2
    and qualified_active_count >= 2
    and required_evidence_layers == {
        MARKET_TREND, QUALITY_MOMENTUM, ENTRY_SETUP
    }

ADVANCED_MINT =
    COMMON
    and core_state in {BREAKOUT WATCH, BREAKOUT}
    and raw_aligned_count >= 2
```

N/AはPASSではない。evaluated required conditionから除外し、全必須条件N/Aの手法はqualifiedにしない。
WARNは成立境界として許容する。unknown verdict/layerはrejectする。

## 2. Versions

| Contract | Required version |
|---|---|
| State transitions | `sm1` |
| Setup identity | `csu1` |
| Candidate threshold/evidence | `smt2` |
| Identity decision | `idr2` |
| Core observation | `smo2` |

旧versionを同名のまま意味変更しない。

## 3. Required Fields

`CoreObservation`または同等のimmutable factへ追加候補:

- `mint_gate_rule_version: str`
- `qualified_active_strategy_count: int`
- `qualified_active_strategies: tuple[str, ...]`
- `strategy_required_evidence: mapping[str, evidence]`
- `required_evidence_layers: tuple[str, ...]`
- `mint_gate_result: bool`
- `mint_gate_reason_codes: tuple[str, ...]`

手法別evidenceは最低限次を持つ。

- `raw_active`
- `evaluated_required_count`
- `required_fail_count`
- `qualified_active`
- `satisfied_required_layers`

Core detailの全conditionやchartをPhase 2A DB/seedへ複製しない。

## 4. Reason Codes

- `MINT_ADVANCED_CORE_STATE`
- `MINT_FORMING_REQUIRED_EVIDENCE_CONFIRMED`
- `NO_SETUP_CORE_STATE_INELIGIBLE`
- `NO_SETUP_RAW_ALIGNED_LT_2`
- `NO_SETUP_QUALIFIED_ACTIVE_LT_2`
- `NO_SETUP_MISSING_MARKET_TREND_EVIDENCE`
- `NO_SETUP_MISSING_QUALITY_MOMENTUM_EVIDENCE`
- `NO_SETUP_MISSING_ENTRY_SETUP_EVIDENCE`
- `INVALID_MINT_GATE_VERDICT`
- `INVALID_MINT_GATE_LAYER`
- `DECLARED_ALIGNED_COUNT_RECOMPUTED`

invalid reasonをNO_SETUPへ丸めない。

## 5. Candidate Change Locations

後続Implementation Goalで必要性を確認する候補であり、このhandoff自体は変更を許可しない。

- `engine/state_machine/models.py`
- `engine/state_machine/adapter.py`
- `engine/state_machine/guards.py`
- `engine/state_machine/identity_resolver.py`
- `engine/state_machine/config.py`
- `engine/state_machine/schema.py`
- `engine/state_machine/storage.py`
- `engine/state_machine/artifacts.py`
- `schemas/phase2a_*.schema.json`
- Phase 2Aのfixture/unit/integration tests
- Cutover/continuation runbook

`engine/strategies/*`と`engine/pivots.py`は本変更で修正しない。Core FORMING semanticsの変更が必要なら
別Goalへ切り出す。

## 6. Required Fixtures

| Fixture | Expected |
|---|---|
| FORMING、raw 2、両方とも必須FAIL | NO_SETUP / `QUALIFIED_ACTIVE_LT_2` |
| FORMING、qualified 2、Trend+Qualityだけ | NO_SETUP / `MISSING_ENTRY_SETUP_EVIDENCE` |
| FORMING、qualified 2、3 layers | MINT |
| FORMING、qualified 3、3 layers | MINT |
| WATCH、raw 2、valid price/Pivot | MINT |
| BREAKOUT、raw 2、valid price/Pivot | MINT |
| 全REQUIREDがN/Aのactive手法 | qualified false |
| REQUIREDにWARNのみでFAILなし | qualified true |
| REQUIREDにFAILが1件 | qualified false |
| unknown verdict/layer | structured reject |
| existing UID、current gate不成立 | LINK可能、phase不変 |
| same input/same version retry | NO_OP、MINT増加0 |

1812 鹿島建設型のfixtureを固定する。共通contractionだけでMinervini/Qullamaggieがraw FORMINGでも、
両手法の必須条件FAILによりMINTしないことを確認する。

## 7. Migration/Activation Blocker

Implementation前に別Goalで次を確定する。

1. 既存811 UIDの`origin_mint_rule_version`記録方法。
2. `LEGACY_GATE_ONLY`をphase改変なしで表す方法。
3. 既存UIDをLINKし続ける際の最新gate status保存方法。
4. What Changed consumerからの非破壊的な除外方法。
5. `smt1`seedから`smt2`へのactivation market date。
6. rollback seedとversion compatibility。

既存UID、decision、event、Pivot revisionを削除・再採番しない。Migration方針が決まるまで
Implementation GoalとGate Cへ進まない。

## 8. Verification Required After Future Implementation

- 全既存pytestとfixed-input regression。
- 新規MINT gate fixture全件。
- 初回固定入力counterfactual: 241件（FORMING 175 / WATCH 59 / BREAKOUT 7）。
- 現行NO_SETUPから新MINTへの逆転0件。
- existing UID LINK regression。
- 同日同入力NO_OP、同日異入力fail closed。
- seed export/restore state hash一致。
- `engine.state_machine`から`engine.database`へのimport禁止維持。
- product DBと`public/`へのshadow書込み拒否維持。
- 複数の異なるmarket dateでproduction shadowを再実施。

241はgolden countとして入力fixtureの再現性確認に使うが、一般的な候補数目標としてhard-codeしない。

## 9. Prohibited Shortcuts

- `aligned >= 3`だけへ変更して完了としない。
- Core rank/Top 20をMINT条件にしない。
- CAN SLIMやAnnual EPSの存在を全銘柄へ必須化しない。
- N/AをPASSに変換しない。
- 同一layerの複数手法を複数の独立layerとして数えない。
- 既存smt1 UIDを削除・再MINTしない。
- `sm1/smt1/idr1/smo1`の意味を無言で上書きしない。
- Gate Cを同時に進めない。
- Goal承認だけをpush・Release変更・本番実行の承認と解釈しない。
