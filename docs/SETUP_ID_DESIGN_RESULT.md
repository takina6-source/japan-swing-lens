# Stable Setup Identity Design — Completion Result

完了日: 2026-09-12 JST

## Result

**DESIGN_COMPLETE / HUMAN_REVIEW_REQUIRED**

`docs/SETUP_ID_DESIGN_GOAL.md`に基づく設計と固定入力検証を完了した。Phase 2A State Machine Designは開始していない。

## Deliverables

| Deliverable | Location | Status |
|---|---|---|
| 採用・却下案、identity契約、logical schema | `docs/SETUP_ID_DESIGN.md` | COMPLETE |
| 現行依存関係図 | 同上 Section 2 | COMPLETE |
| Downstream Compatibility Matrix | 同上 Section 10 | COMPLETE |
| migration / rollback | 同上 Sections 12–13 | COMPLETE |
| 固定fixture・既知hashベクトル | `docs/examples/setup-identity/design-fixtures.json` | COMPLETE |
| 964銘柄分類 | `docs/examples/setup-identity/legacy-2026-09-03_vs_2026-09-10.json` | COMPLETE |
| Phase 2A handoff | `docs/SETUP_ID_PHASE2A_HANDOFF.md` | COMPLETE |
| Human Review Gate | Design Section 16 / Handoff | WAITING FOR USER REVIEW |

## Verification Evidence

- 既知mint vector: 4 / 4 hash・UID一致。
- relationship fixture: 18件、JSON parse成功。
- legacy classification: 964行、964 unique code。
- 旧総合setup ID変化: 964 / 964を再確認。
- classification: SAME 0 / DIFFERENT 0 / AMBIGUOUS 964。
- AMBIGUOUS理由: 250 + 105 + 461 + 148 = 964。
- JSON 2成果物は`jq empty`成功。
- 設計成果物の`git diff --check`成功。

旧964件をAMBIGUOUSとしたのは、二時点の構造類似だけでは同一setupを証明できず、durable lineage ledgerが存在しないため。新設計ではcutover後のMINT/LINK decisionを永続化し、日次属性から遡及推定しない。

## Scope Confirmation

本Goalで追加したのは設計Markdownと固定検証JSONだけ。

- product Python / JavaScript: 変更なし。
- SQLite DB / schema: 変更なし。
- workflow: 変更なし。
- 公開成果物: 変更・deployなし。
- Core判定・ランキング・閾値: 変更なし。
- State Machine、FAILED→RETRY_WATCH、REBREAKOUT、失効閾値: 未設計。

既存作業ツリーのdirty変更はそのまま保持した。

## Required Human Decision

Phase 2Aへ進む前に、次を承認または差し戻す。

1. 永続台帳 + immutable birth request hashのhybrid方式。
2. `csu1:<code>:<160bit>`形式。
3. Pivot/state/手法集合をUID材料から外すこと。
4. identity層はLINK/MINTを決めず、Phase 2A decisionを安全に記録する境界。
5. 旧964件を全件AMBIGUOUSとして自動統合しないこと。
6. cutover後だけを正確なlineage保証範囲とすること。
7. GitHub Actions cacheとは別にdurable ledger seedを必須にすること。

承認されるまでPhase 2A State Machine Design Goalを作成・実行しない。
