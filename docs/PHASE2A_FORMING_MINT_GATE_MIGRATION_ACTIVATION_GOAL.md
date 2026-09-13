# Japan Swing Lens — FORMING MINT Gate Migration / Activation Goal

作成日: 2026-09-13 JST

## 0. Goal

提案中のFORMING MINT rule `smt2/idr2/smo2`を、初回Cutoverで既に作成・公開された
`live-sm1` continuity seedへ**非破壊的に接続し、安全にactivationする方法を設計する**。

本Goalでは次を確定する。

1. 既存811 `core_setup_uid`の出生事実、phase、event、Pivot revisionを変更せず保持する方法。
2. 既存UIDを、現在の新rule適格性と出生時ruleを混同せず監査できる表現。
3. `LEGACY_GATE_ONLY`をpersistent phaseから分離して保存・配布判定する方法。
4. `smt1/idr1/smo1` seedから`smt2/idr2/smo2`へ進む原子的migration。
5. 新規MINTだけへ新ruleを適用し、既存UIDはcontinuity ruleでLINKする評価順。
6. schedule/pushからは起動できない手動activation protocol。
7. immutable Release seed、version compatibility、rollback、production shadow、Gate Cとの境界。
8. 後続Implementation Goalが解釈せず実装できるschema、擬似コード、manifest、fixture、
   Human Gate。

このGoalは**Migration / Activation設計専用**である。product code、test code、config、DB、seed、
Release、workflow、Pagesを変更しない。migrationやactivationを実行しない。commit/pushしない。
Gate Cへ進まない。

## 1. Approval State and Prerequisites

本Goalの作成指示は、Migration / Activation設計を作る許可であり、次を自動承認するものではない。

- FORMING MINT Gate DesignのHuman Review Gate通過。
- `smt2/idr2/smo2`の実装。
- 既存seedのmigration。
- GitHub Actions変更、Release asset追加、Pages公開。
- production activation、Gate C。

設計では次の提案を入力前提として使用するが、Human Reviewで変更された場合は本Goalの成果を
再評価する。

1. State Machine transitionは`sm1`を維持。
2. Setup identity formatは`csu1`を維持。
3. FORMINGはqualified active strategy 2以上かつ3 evidence layersを要求。
4. WATCH/BREAKOUTの新規MINT条件は現行を維持。
5. 新契約候補は`smt2/idr2/smo2`。
6. 既存811 UIDを削除・再採番・遡及改変しない。
7. 新ruleの複数market date shadowが終わるまでGate Cを保留。

参照:

- `docs/PHASE2A_FORMING_MINT_GATE_DESIGN.md`
- `docs/PHASE2A_FORMING_MINT_GATE_HANDOFF.md`
- `docs/PHASE2A_FORMING_MINT_GATE_COUNTERFACTUAL.md`
- `docs/PHASE2A_CUTOVER_RUNBOOK.md`

## 2. Fixed Production Evidence

初回production Cutoverについて次を固定事実として扱う。

| Fact | Value |
|---|---|
| State lineage | `live-sm1` |
| Run ID | `smrun1:20260911:ae2c9fb95792da5856878c82` |
| Market date | `2026-09-11` |
| Scope | 965 |
| CURRENT / INSUFFICIENT | 964 / 1 |
| MINT / NO_SETUP | 811 / 153 |
| FORMING / WATCH / POST_BREAKOUT | 745 / 59 / 7 |
| Identity ledger | 811 |
| Current states | 811 |
| Event anchors | 818 |
| Pivot revisions | 811 |
| Seed schema | `phase2a-seed-v1` |
| Operational schema | `phase2a-schema-v1` |
| Release tag | `phase2a-state-v1` |

同じ固定入力を新rule候補で評価すると、既存MINT 811件のうち241件が現在rule適格、570件が
旧ruleでのみMINTされたFORMINGとなる。内訳はFORMING 175、WATCH 59、POST_BREAKOUT 7。
現行NO_SETUPから新規MINTへ逆転する銘柄は0件。

この241/570は固定入力rehearsalのgolden evidenceであり、将来activation日の件数を固定する
目標値ではない。

## 3. Absolute Principles

1. Migrationは新しいCutoverではない。空ledgerから開始せず、最新の検証済みseedを必須とする。
2. `core_setup_uid`、MINT decision、origin observation、event UID、Pivot revisionを変更しない。
3. 過去の`smt1`判断を誤りとして消さず、どのruleで出生したかを監査可能にする。
4. 「現在の新rule適格性」はidentity、phase、transition eventとは別概念にする。
5. 新rule不適格をFAILED、EXPIRED、CLOSED、NO_SETUPへ変換しない。
6. 既存UIDはMINT gateではなくLINK continuity ruleを先に評価する。
7. MINT gateは非terminal候補がない新規setupだけに使用する。
8. stale、missing、invalid、N/Aだけを理由に`LEGACY_GATE_ONLY`を確定しない。
9. migrationは単一transactionまたはpromote前の完全な一時DBで行い、部分状態を残さない。
10. migration前seedとmigration後seedをimmutable assetとして別々に保持する。
11. GitHub Actions cacheを正本にしない。Release manifest列挙とhash検証を継続する。
12. 同じseed、同じactivation input、同じmanifestのreplayは同じ結果にする。
13. 古いrunnerが新schema seedを誤って選択しないversion compatibility gateを設ける。
14. Core Pagesの成功とPhase 2A migration失敗を分離する。
15. public What Changedへ接続する前に、migrationと新rule shadowを完了する。
16. 件数を合わせるために過去decisionや入力を修正しない。

## 4. Scope

### In Scope

- 既存UIDのbirth rule provenance。
- current gate assessmentと`LEGACY_GATE_ONLY`の論理model。
- persistent phaseとconsumer/distribution eligibilityの分離。
- seed/operational/public schemaのversion compatibility設計。
- migration command、transaction、idempotency、manifest、audit event。
- activation date、manual dispatch、preflight、dry-run、promotion。
- dual evaluationまたはshadow comparison期間。
- rollback、downgrade禁止、retention、Release asset selection。
- 既存811 UID、新規setup、LINK、MINT、terminal UID、欠測のfixture。
- Human Review Gateと後続Implementation Goalへのhandoff。

### Out of Scope

- FORMING MINT rule自体の再設計。
- State Machine T01–T26、phase、event semanticsの変更。
- setup UID format/hashとidentity ledger主キーの変更。
- Core 6手法、ランキング、trade plan、Validation、Experimental、Researchの変更。
- product code、DB schema、workflow、Release、Pagesの実変更。
- 初回Release assetの削除・上書き。
- 過去runの再計算、過去MINTの取消し。
- What Changed JSON公開、UI変更、Gate C承認。
- 新しい外部APIや有料データ。

## 5. Concepts to Keep Separate

設計結果では最低限、次を別field/recordとして定義する。

| Concept | Meaning |
|---|---|
| Birth rule provenance | UIDが最初にMINTされた`idr/smt/smo` versionとrun |
| Current gate assessment | 当該CURRENT観測が現在ruleを満たすか |
| Identity decision | LINK / MINT / AMBIGUOUS / NO_SETUP |
| Persistent phase | FORMING / WATCH / POST_BREAKOUT等 |
| Continuity status | CONTIGUOUS / PAUSED / SUSPENDED_GAP / TERMINAL |
| Distribution eligibility | What Changed等のconsumerへ出せるか |
| Migration status | schema/provenance migrationが完了したか |
| Activation status | 新ruleが新規MINTへ有効か |

`LEGACY_GATE_ONLY`をpersistent phaseへ追加しない。これは「出生は有効だが、現在の新規MINT ruleを
まだ満たしていない」というgate assessment/distribution状態である。

## 6. Existing UID Classification

最低限、次のassessment状態を比較して1案を採用する。

- `CURRENT_RULE_ELIGIBLE`
- `LEGACY_GATE_ONLY`
- `PENDING_CURRENT_OBSERVATION`
- `CURRENT_OBSERVATION_INVALID`
- `TERMINAL_PRESERVED`

分類規則の必須条件:

1. migration時点に同じmarket dateのCURRENT `smo2`観測がある場合だけ、新rule適格/不適格を判定する。
2. stale/missing/invalidなら`PENDING`または`INVALID`とし、`LEGACY_GATE_ONLY`へ推定しない。
3. WATCH/BREAKOUTでadvanced MINT ruleを満たす既存UIDは`CURRENT_RULE_ELIGIBLE`。
4. FORMINGでqualified 2 + 3 layersを満たす既存UIDは`CURRENT_RULE_ELIGIBLE`。
5. FORMINGで有効観測はあるが新rule不成立なら`LEGACY_GATE_ONLY`。
6. terminal UIDはphaseを維持し、再評価で再開しない。
7. assessmentは日次appendまたはversion付きlatest projectionとして保存し、過去値を上書きしない。

固定入力rehearsalでは最低限、`CURRENT_RULE_ELIGIBLE=241`、`LEGACY_GATE_ONLY=570`、
既存UIDの追加MINT=0、UID削除=0を要求する。

## 7. Required Storage Design Comparison

次の3案以上を比較し、1案を採用する。

### A. Existing `distribution_eligible` overwrite

current stateのbooleanを直接更新する案。単純だが、なぜ変わったか、どのruleで判定したか、
過去値を失う問題がある。原則として不採用候補とし、採用するならaudit eventを必須にする。

### B. Append-only gate assessment table

UID、observation、rule version、assessment、reason、evaluated_atを持つappend-only tableと、
latest projectionを作る案。phaseとidentityを変更せず履歴を保持できるため優先候補とする。

### C. Derived artifact only

DBへ保存せず毎run派生する案。seedから評価根拠を復元できない、欠測時に前回値を失う問題を評価する。

設計は、birth provenanceを既存run/decision参照から導出するか、専用immutable metadataへmaterialize
するかも比較する。過去に存在しなかったversion値を事実として捏造しない。

## 8. Required Migration Algorithm

設計結果は次の順序を擬似コードとtransaction境界で確定する。

```text
1. Enumerate immutable Release manifests.
2. Select the newest eligible smt1 seed at or before activation input date.
3. Verify archive SHA-256, manifest, schema, lineage, SQLite integrity and row counts.
4. Restore into a new empty temporary DB; never edit the downloaded seed in place.
5. Verify exact pre-migration counts and state hash.
6. Apply schema migration in one transaction.
7. Materialize/derive birth rule provenance without changing birth decisions.
8. Read same-run CURRENT smo2 observations.
9. LINK existing UIDs first under continuity rules.
10. Append current gate assessments for linked existing UIDs.
11. Apply idr2 MINT gate only when no eligible existing UID exists.
12. Run unchanged sm1 transitions.
13. Verify UID/event/Pivot invariants, schema and state hash.
14. Export a new seed and migration/activation manifest.
15. Restore the new seed into another empty DB and compare state hash/counts.
16. Upload immutable assets only after all local checks pass.
17. Re-download both assets and verify again before promotion.
```

失敗時は新assetを昇格せず、直前の`smt1`seedを正本として保持する。migration失敗を理由に空ledgerへ
fallbackしない。

## 9. Migration Manifest

手動承認対象となるmanifest schemaを設計する。最低限次を含める。

- manifest version、作成日時、operator approval reference。
- repository commit SHA。
- predecessor Release tag、asset name、asset SHA-256、market date、run ID、state hash。
- source/target operational schema、seed schema、`sm/smt/idr/smo/csu` versions。
- state lineageとidentity epoch。
- activation expected market dateとmarket session index。
- scope name/hash/count、Core config/input hash。
- automatic `CLOSED` enabled/disabled。
- dry-run UID/decision/phase/event/Pivot counts。
- gate assessment countsとreason counts。
- expected added/updated/deleted row counts。UID/decision/event deleteは0固定。
- public artifact disabled、Gate C blocked。
- manifest自身のSHA-256。

manifestのmarket date、scope、config、predecessorが実runと異なる場合、DB作成前にfail closedする。
実データへ合わせてequality checkを弱めず、新しいreviewed manifestを作る。

## 10. Activation Protocol

### Phase M0 — Local fixed-input rehearsal

- 初回Cutover seedと同一入力でmigrationを再現する。
- 241 eligible / 570 legacy-only、追加MINT 0、削除0を確認する。
- seed export/restore、same-input replay、rollback rehearsalを行う。

### Phase M1 — Dual-evaluation production shadow

- 現行`smt1`をauthorityとして継続し、`smt2`はprivate comparisonだけを生成する。
- 最低3つの異なるmarket dateで、旧/新MINT候補、既存UID assessment、reason内訳を比較する。
- dual evaluationはidentity/state/event/seedを変更しない。

### Phase M2 — Human Activation Gate

- implementation commit、staged diff、全pytest、fixed-input、M0/M1レポートを提示する。
- exact predecessor asset/hashとactivation manifest SHA-256を承認する。
- approvalは1回の手動activationだけを許可する。

### Phase M3 — Manual activation

- `workflow_dispatch`のみで起動する。
- schedule、push、通常continueからmigration modeを呼べないようにする。
- predecessorとmanifestの完全一致後だけ一時DBへmigrationする。
- 新しいimmutable seed/manifestを同じRelease authorityへ追加し、上書きしない。
- 再ダウンロード検証成功後だけ新seedを後続runの候補へ昇格する。

### Phase M4 — smt2 production shadow

- 最低3つの異なるmarket dateで`smt2/idr2/smo2`を継続する。
- Gate assessment、LINK/MINT/NO_SETUP、phase/event、seed continuityを監査する。
- What Changed JSONはまだPagesへ公開しない。

Gate CはM4完了後に別途承認する。M1の日数をM4へ流用しない。

## 11. Workflow and Permission Requirements

- top-level `permissions: {}`または同等のdeny-by-defaultを維持する。
- test/analyze/dual-evaluationは`contents: read`。
- Release uploadを行うactivation/promotion jobだけ`contents: write`。
- migration inputはbooleanだけでなく、manifest SHA-256、predecessor asset/hash、target versionsを要求する。
- migration modeとinitialize modeを同じflagへ統合しない。
- 通常scheduleはmigrationを実行せず、未対応schema seedを選択した場合はPhase 2Aだけfail closedする。
- Core Pages jobはPhase 2A migration失敗から独立させる。
- secrets、token、内部path、stack traceをRelease/public artifactへ含めない。

## 12. Compatibility and Seed Selection

設計は次を解決する。

1. `smt1`runnerが`smt2`seedを選ばないmanifest compatibility filter。
2. `smt2`runnerがmigration前`smt1`seedを通常continueとして誤使用しないgate。
3. activation専用runnerだけが承認manifest付きで`smt1 -> smt2`を変換できること。
4. 同じmarket dateの`smt1`と`smt2`assetが共存する場合の一意選択。
5. mutable latest pointerを使わず、version、date、run lineageで選ぶこと。
6. 最低5つの検証済みmarket dateを保持したままretentionすること。

## 13. Rollback Design

最低限、次の3時点を分ける。

### Before upload

一時DBとartifactを破棄し、既存`smt1`authorityを維持する。

### Uploaded but not promoted

新assetはimmutable audit evidenceとして残してよいが、`promotion_status=REJECTED`として通常選択から
除外する。assetを上書き・削除して失敗を隠さない。

### After smt2 promotion

- Phase 2A scheduleを停止し、Core Pagesは継続する。
- 後続`smt2`runが存在する場合、古い`smt1`seedへ無条件downgradeして後続stateを消さない。
- exact rollback manifest、対象seed、失われるrun、再開versionをHuman Gateで承認する。
- 必要なら同じlineageを破壊せず訂正event/forward fixを選び、過去assetをmergeしない。

rollback rehearsalはM0とM2の必須証拠にする。

## 14. Required Invariants and Tests

### Identity/history invariants

- 既存`core_setup_uid`集合がmigration前後で一致。
- 既存MINT decision、origin observation、created runがbyte-equivalent。
- 既存event UID/type/anchor件数が減少・変更しない。
- 既存Pivot revisionが減少・変更しない。
- migrationだけによる追加MINTは0。
- 既存UIDへのLINKはcandidate gate不成立でも継続可能。

### Assessment invariants

- CURRENTがないUIDをLEGACY_GATE_ONLYへしない。
- 同一UID/run/ruleのassessmentを二重発行しない。
- assessment reasonとinput observationを参照できる。
- `LEGACY_GATE_ONLY`でphaseを変更しない。
- fixed inputで241 / 570を再現する。

### Atomicity/idempotency

- schema migration途中失敗で元seed/正本を変更しない。
- manifest mismatchはDB作成前に停止。
- 同じactivation再実行はNO_OPまたはalready-promotedとして安全停止。
- 同日異入力、out-of-order、unsupported versionはfail closed。
- export/restore後state hash、row count、foreign key、integrityが一致。

### Isolation/security

- product DBを開かない。
- `public/`へDB、seed、rejection、内部assessmentを書かない。
- `engine.state_machine`から`engine.database`をimportしない。
- archive member allow-list、path traversal、symlink、hash tamperを拒否する。
- Release write permissionを限定する。

## 15. Required Deliverables

本Goalを実行する際は、最低限次を作る。

1. `docs/PHASE2A_FORMING_MINT_GATE_MIGRATION_DESIGN.md`
   - storage案比較、採用schema、classification、migration algorithm、compatibility、rollback。
2. `docs/PHASE2A_FORMING_MINT_GATE_ACTIVATION_PLAN.md`
   - M0–M4、Human Gate、workflow dispatch、promotion/rollback手順。
3. `docs/PHASE2A_FORMING_MINT_GATE_MIGRATION_HANDOFF.md`
   - field、table、manifest、reason code、fixture、候補path、禁止事項。
4. migration/activation manifestのJSON Schemaと非本番sample。
5. fixed-input migration rehearsal specification。
6. Human Review Gate checklist。

成果物は設計・schema・sample・fixture仕様だけとする。product code、workflow、本番seedを変更しない。

## 16. Acceptance Criteria

次をすべて満たしたときだけ、本Goalを完了とする。

1. 既存811 UIDを削除・再採番せずmigrationできる。
2. birth rule、current gate assessment、phase、distribution eligibilityを分離している。
3. `LEGACY_GATE_ONLY`をphase変更なしで表現できる。
4. stale/missing/invalidをlegacy-onlyと誤分類しない。
5. LINK-before-MINTの正確な評価順を定義している。
6. `smt1 -> smt2`を空ledgerなしで原子的に行う。
7. source/target runnerのseed compatibilityをfail closedで定義している。
8. fixed-inputで241 eligible / 570 legacy-only / 追加MINT 0 / UID削除0を要求している。
9. manual-only activationとexact manifest approvalを定義している。
10. immutable asset、再ダウンロード検証、retention、rollbackを定義している。
11. M1とM4を別々に3 market date要求している。
12. Core PagesとPhase 2A失敗を分離している。
13. What Changed/Gate Cを変更・公開していない。
14. 後続Implementation Goalに必要なfield、schema、fixture、候補pathが揃っている。

## 17. Human Review Gates

### Gate M-A — Design approval

1. FORMING MINT Gate Designが正式承認済みか。
2. append-only gate assessment model。
3. `LEGACY_GATE_ONLY`とdistribution policy。
4. same `live-sm1` lineageでversion migrationすること。
5. `smt2/idr2/smo2`とschema version境界。
6. M0–M4の段階公開とGate C保留。

### Gate M-B — Implementation approval

1. exact changed pathsとstaged diff。
2. 全pytest、fixed-input、security/isolation test。
3. M0 fixed-input migration/rollback evidence。
4. migration manifest schema/sample。
5. workflow permissionsとmanual-only gate。

### Gate M-C — Activation approval

1. M1の3 market date比較。
2. exact implementation commitとactivation manifest SHA-256。
3. predecessor Release asset/hash/state hash。
4. activation expected market date、scope/config/input hashes。
5. dry-run classification、row diff、rollback target。

Gate M-CでもGate Cは承認しない。M4完了後に別途判断する。

## 18. Stop Conditions

次のいずれかが発生した場合は設計または実装を停止し、Human Reviewへ戻す。

- FORMING MINT Gate Designが未承認または変更された。
- 既存UID、decision、event、Pivot revisionの変更が必要になる。
- `LEGACY_GATE_ONLY`をphaseへ混入しなければ表現できない。
- 同じlineage内でversion provenanceを一意に復元できない。
- predecessor seed/manifest/hashが一致しない。
- migrationが空ledger初期化や全件再MINTを必要とする。
- 古いrunnerが新seedを選択する可能性を排除できない。
- activation inputのmarket date、scope、configが承認manifestと異なる。
- fixed-input 241/570、追加MINT 0、削除0を説明できない。
- rollbackで後続state/eventを無言で失う。
- public What Changedを先に有効化しなければ検証できない。

