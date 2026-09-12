# Phase 2A State Machine Design Result

完了日: 2026-09-12 JST
結果: **DESIGN APPROVED / LIMITED RUNTIME BENCHMARK COMPLETE / IMPLEMENTATION NOT STARTED**

## 1. Outcome

Stable Setup Identity Designの`core_setup_uid`を単位に、複数日のsetup状態を追跡するPhase 2A State Machineを設計した。product code、DB、workflow、GitHub Pages、既存公開成果物は変更していない。

採用した中心判断:

- event-centered model。
- 永続phaseは`FORMING / WATCH / POST_BREAKOUT / FAILED / RETRY_WATCH / EXPIRED / CLOSED`。
- 初回`BREAKOUT_CONFIRMED`と2回目以降の`REBREAKOUT_CONFIRMED`はappend-only event。
- Core observed state、run/observation quality、identity decision、derived phase、eventを別契約にした。
- State Machine FAILEDは、同じUIDのbreakout anchorと固定tracking Pivotに対する3%超の下抜けで定義した。
- retry zoneはPivot以下0–3%、aligned trend strategiesは2以上。
- 形成中90session、突破後20session、auto-link gap 5sessionを暫定値とした。
- stale、履歴不足、error、一時的scope離脱、AMBIGUOUSではphaseを遷移させない。構造化された恒久離脱証拠だけでCLOSEDにする。
- 最初の期限超過CURRENT観測ではqualified crossをSETUP_EXPIREDより先に評価し、本物のbreakoutを失わない。
- cutover Core BREAKOUTは左打切りbootstrap anchorにできるが、過去BREAKOUT eventや実発生日を捏造しない。
- Limited Runtime Benchmarkにより容量planning baselineを473MB/年、rangeを435–540MB/年へ改訂した。full candidate複製は不採用。

## 2. Deliverables

| Deliverable | Path | Status |
|---|---|---|
| Main design / transition / schema / capacity | `docs/PHASE2A_STATE_MACHINE_DESIGN.md` | Complete |
| Machine-readable transition fixtures | `docs/examples/phase2a-state-machine/transition-fixtures.json` | Complete |
| Logical contract examples | `docs/examples/phase2a-state-machine/contract-examples.json` | Complete |
| Implementation handoff | `docs/PHASE2A_IMPLEMENTATION_HANDOFF.md` | Complete |
| Design result and review gate | `docs/PHASE2A_STATE_MACHINE_DESIGN_RESULT.md` | Complete |
| Limited Runtime Benchmark report | `docs/PHASE2A_LIMITED_RUNTIME_BENCHMARK.md` | Complete |
| Reproducible benchmark harness | `scripts/benchmark_phase2a_storage.py` | Complete |
| Fixed benchmark result | `docs/examples/phase2a-state-machine/runtime-benchmark-results.json` | Complete |

## 3. Offline Verification Results

実行した検査:

- 両JSONを`jq empty`でparse。
- T01–T26のtransition IDが26件・重複なし。
- transitionが参照するeventがevent enum内にあることを確認。
- scenarioが35件・ID重複なし。
- 965 scope count = 960 CURRENT + 4 STALE + 1 INSUFFICIENTを確認。
- identity count = CURRENT 960を確認。
- main designに採用model、7phase、4 identity decision、FAILED reason、same-day conflict、CLOSED、cross-before-expiry、容量、Human Review Gateが存在することを確認。
- 4成果物に対する`git diff --check`をpass。

結果: **PASS**

これは設計fixtureの整合検査であり、production state machineのtestではない。product実装はまだ存在しない。

## 4. Acceptance Criteria Mapping

| Goal criterion | Result / location |
|---|---|
| Setup Identity 12契約を維持 | Design 2.1 |
| 単位がCore UID | Design 1, 2 |
| run/observation/Core/phase/eventを分離 | Design 4, 8 |
| scope 965全件status | Design 8, 14; contract example |
| 8303・stale 4件を無言除外しない | Fixtures `insufficient-history-8303-like`, `stale-four-stocks-like` |
| exact state list/invariant/entry/exit | Design 5.1 |
| 全transition/guard precedence | Design 6.3, 7; fixture T01–T26 |
| breakout→failed→retry→rebreakout | Design 19; normal fixture sequence |
| initial/rebreakout event区別 | Design 5.2, 11, 14.6 |
| Core/strategy/Validation FAILED区別 | Design 2.2, 11.1 |
| tracking/observed Pivot分離 | Design 10 |
| Pivot revision/MINT境界 | Design 9–10 |
| LINK/MINT/AMBIGUOUS/NO_SETUP | Design 9 |
| 同一code複数setupを表現 | Design 9.3, logical schema |
| retry/expiry/rebreak閾値と境界 | Design 6, 11–12; boundary fixtures |
| missingだけでfailure/expiryにしない | Design 8, 13; missing fixtures |
| 恒久scope離脱の終端とghost表示防止 | Design 5.1, 7, 13.2, 16; permanent/dynamic scope fixtures |
| 期限超過日crossの評価順 | Design 6.3, 12; `qualified-cross-wins-on-first-over-limit-session` fixture |
| same-day/concurrency/order/gap/version | Design 13, 15; fixtures |
| look-ahead防止 | Design 13.1; `lookahead-prohibited` fixture |
| run count/error invariant | Design 8, 14.1; contract example |
| atomic/idempotent persistence | Design 14–15; `atomic-retry` fixture |
| seed欠損で全件再採番しない | Design 15; `ledger-seed-fail-closed` fixture |
| minimal observation only | Design 14.3 |
| annual capacity estimate | Design 18: 実測473MB/年、range 435–540MB/年 |
| normal/boundary/missing/replay fixture | 35 scenarios |
| downstreamを変更しない | Design 16 |
| no product/DB/workflow/public changes | Section 6 below |
| implementation前review gate | Design 20 |

## 5. Human Review Decision

次の11項目は、2点の設計修正確認後の2026-09-12ユーザー指示「Limited Runtime Benchmarkに進んで」により承認済みとして記録する。

1. event-centered modelと7phase（CLOSEDを含む）。
2. Core observed FAILEDではなく、tracking Pivot 3%超割れをPhase 2A FAILEDにすること。
3. retry 0–3%、2手法、形成90session、突破後20session、gap 5session。
4. 突破は価格crossかつbreakout手法2以上とすること。
5. tracking Pivotを突破前はrevision、突破後はfreezeすること。
6. 現行入力では自動slotを`core-primary`に限定し、複数候補をAMBIGUOUSにすること。
7. 6session以上のgap/scope復帰を自動LINKせずSUSPENDED_GAPにし、恒久離脱証拠だけでCLOSEDにすること。
8. 最初の期限超過CURRENT観測ではqualified crossをexpiryより先に評価すること。
9. cutover BREAKOUTを左打切りbootstrap cycle 1として扱うこと。
10. same-day異hashを後着優先にせずquarantineすること。
11. 容量とretention。benchmark前の258MB/年・3年online案は実測後に473MB/年・12か月hot + 年次archiveへ改訂。

暫定市場閾値はimplementation時に変更せず、shadow historyのsensitivity検証で評価する。容量・retentionはLimited Runtime Benchmark結果を反映済み。

## 6. Change Boundary Check

このGoalで追加・変更したのはdesign documentsとdesign-only JSON fixtureのみ。

- `engine/`: changeなし。
- `scripts/`: 隔離benchmark harnessだけ追加。product execution pathへの接続なし。
- `tests/`: changeなし。
- `data/*.db`, `momentum.db`: changeなし。
- `.github/workflows/`: changeなし。
- `public/`, `app/`, `dist/`: changeなし。
- git commit/push/deploy: 未実施。

既存dirty worktreeの他変更は保持し、編集していない。

## 7. Completed Benchmark and Required Next Step

固定約1,000行の隔離SQLiteを使うLimited Runtime Benchmarkは完了した。runtime/integrity/recoveryはPASS、元の容量見積りの不足を検出し設計へ反映した。

次は本結果を前提とするPhase 2A Implementation Goalの作成。別のユーザー指示があるまでproduction実装、DB migration、workflow、公開へ進まない。
