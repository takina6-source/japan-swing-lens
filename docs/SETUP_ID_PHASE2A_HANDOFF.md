# Stable Setup Identity — Phase 2A Handoff

## Status

**Human approval required before Phase 2A design starts.**

本書はsetup identity設計からPhase 2A State Machine Designへ渡す契約だけを記載する。状態遷移規則や投資閾値は含まない。

## Fixed Identity Contract

1. Core setupの永続主キーは`core_setup_uid`。
2. formatは`csu1:<4桁code>:<SHA-256先頭160bitの小文字hex>`。
3. UIDはimmutableなbirth requestから一度だけ発行し、日次属性から再計算しない。
4. 発行済みUIDの正本は永続identity ledger。
5. Pivot、state、手法集合、logic/strategy/threshold versionをUID材料に含めない。
6. Pivot・根拠変更は同一UID内のrevision。
7. 手法加入・離脱は期間付きmembership。
8. observationとsignal/eventはsetupとは別ID。
9. identity解決結果は`LINK`、`MINT`、`AMBIGUOUS`、`NO_SETUP`。
10. 判断不能時はUIDをnullにし、reason codeを保存する。
11. CoreとExperimentalは別namespace。
12. 現行legacy setup_idは新主キーにせず、aliasまたはunresolved参照として保持する。

## Required Input From Phase 2A

Phase 2Aは各observation・各setup slotについて、次のdecision objectをidentity層へ渡す。

| Field | Required | Meaning |
|---|---|---|
| `observation_uid` | Yes | runと銘柄を一意に指す |
| `code` | Yes | 4桁文字列 |
| `decision_slot` | Yes | 同時複数候補を区別 |
| `decision` | Yes | LINK / MINT / AMBIGUOUS / NO_SETUP |
| `target_core_setup_uid` | LINK時 | 同じcodeの既存UID |
| `identity_epoch` | MINT時 | cutover/bootstrap世代 |
| `origin_slot` | MINT時 | 同一観測内の安定slot |
| `decision_rule_version` | Yes | Phase 2A規則のversion |
| `reason_codes` | Yes | 機械可読な判断理由 |
| `evidence_refs` | LINK/MINT時 | 使用観測の参照 |

identity層はdecisionを再解釈しない。code整合性、一意性、idempotency、collision、transactionだけを強制する。

## Required Output To Phase 2A

identity層は次を返す。

- `resolution_status`
- `core_setup_uid` nullable
- `revision_no` nullable
- `strategy_setup_uids`
- `mint_request_sha256` nullable
- `identity_version`
- `identity_epoch`
- `reason_codes`
- `ledger_seed_hash`

## Questions Phase 2A Must Decide

1. どの観測事実なら既存UIDへ`LINK`できるか。
2. どの観測事実なら新UIDを`MINT`するか。
3. 複数候補時に一意選択するか`AMBIGUOUS`にするか。
4. 欠測後・scope復帰後のlink可否。
5. setupをactive/inactive/expiredとする規則。
6. WATCH、BREAKOUT、FAILED、RETRY_WATCH、REBREAKOUTの遷移。
7. Pivot変更が同一setup内revisionか新setupかを判断する市場ルール。
8. `origin_slot`の安定した採番順序。

これらをsetup identity層へ逆流させない。

## Phase 2A Observation Minimum

full candidateは保存しない。identity連携に必要な最小項目は次のとおり。

- run ID、observation UID、code、analysis date。
- Core stateと手法別state。
- close、採用Pivotの価格/type/basis/fidelity。
- 観測時のlegacy総合/手法setup ID。
- logic/strategy/threshold version。
- Core UID nullable、revision number nullable。
- resolution status、reason codes、decision rule version。
- price history本数、直近価格日、観測品質status。

## Durable Ledger Requirement

GitHub Actions cacheだけを正本にしない。

- 前run ledgerを検証付きartifactとしてseedする。
- seed schema version、SHA-256、件数、作成runをmanifestへ保存する。
- seed欠損・hash不一致時は全銘柄を再採番しない。
- `IDENTITY_LEDGER_UNAVAILABLE`または`IDENTITY_LEDGER_INVALID`としてrunを停止またはPARTIALにする。
- seedなしで全件MINTする動作を通常fallbackにしない。

## Legacy Boundary

2026-09-03対2026-09-10の964銘柄は全件`AMBIGUOUS`。新identityを遡及付与しない。

cutover runでは`CUTOVER_BOOTSTRAP`として新UIDを発行できるが、過去legacy IDとの関係はcutover時点の同時観測だけを1:1参照として保存する。cutover以前の継続を主張しない。

## Acceptance Gate For Phase 2A Goal

Phase 2A State Machine Design Goalは、次が人間に承認された後だけ作成する。

- hybrid ledger方式。
- UID format。
- identityとstate machineのdecision境界。
- legacy 964件の全件AMBIGUOUS。
- cutover以後だけを正確なlineage保証範囲とすること。
- durable ledger seedを必須にすること。

未承認の項目があれば、先に`docs/SETUP_ID_DESIGN.md`だけを修正する。
