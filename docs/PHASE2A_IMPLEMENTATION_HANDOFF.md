# Phase 2A State Machine — Implementation Handoff

作成日: 2026-09-12 JST
前提: `PHASE2A_STATE_MACHINE_DESIGN.md`のHuman Review承認後にだけ使用する。

## 1. Fixed Contract

実装は次を再解釈しない。

- 主キーは`core_setup_uid`。UID format/ledgerはStable Setup Identity Designに従う。
- event-centered model。永続phaseは7つ（恒久離脱用`CLOSED`を含む）、BREAKOUT/REBREAKOUTはevent。
- Core observed state、quality、identity decision、derived phase、eventを分離する。
- tracking Pivotとobserved primary Pivotを分離する。
- stale/missing/error/一時的scope離脱/AMBIGUOUSではphaseを変えない。structured permanent-exit evidenceがある場合だけCLOSEDにする。
- full candidate/detail/chart/財務原票を複製しない。
- cutover以前の964件を遡及LINKしない。
- Phase 2Aを既存rank、Core、Validation、Research、Experimental、Morning Brief Phase 1へ自動接続しない。

## 2. Required Sequence

### Gate 0 — Human review: COMPLETE

2026-09-12のユーザー指示により、設計書Section 20の11項目を承認済みとして記録した。

### Gate 1 — Limited runtime benchmark: COMPLETE

18scenarioを隔離SQLiteで測定し、runtime/integrity/recoveryはPASS。元の258MB/年を83.46%超えたため、計画を代表473MB/年、range 435–540MB/年、12か月hot + 年次archiveへ改訂した。詳細は`docs/PHASE2A_LIMITED_RUNTIME_BENCHMARK.md`。

### Step 1 — Pure domain layer

候補:

- `engine/state_machine/models.py`: enum、immutable observation/state/event value objects。
- `engine/state_machine/guards.py`: finite/date/version/gap/cross/failure/retry/expiry guards。
- `engine/state_machine/transitions.py`: T01–T26のpure evaluator。
- `engine/state_machine/identity_resolver.py`: decision object生成。UID発行自体はidentity ledgerへ委譲。
- `engine/state_machine/ids.py`: observation/event request hash。Setup UID algorithmは既存identity実装へ委譲。

I/O、SQLite、network、現在時刻をpure evaluatorへ入れない。全時刻、version、session indexは引数にする。

### Step 2 — Persistence and migration

候補:

- `engine/state_machine/storage.py`: repository、transaction、seed export/import、rebuild。
- `engine/database.py`: additive migration entryだけ。既存tableの意味やcolumnを書換えない。
- `schemas/phase2a_*.schema.json`: artifact contract。

設計書Section 14の論理tableとunique/check/foreign keyを実装する。SQLite `foreign_keys=ON`、transaction境界、optimistic `state_version`、full SHA-256 collision検査を必須にする。

### Step 3 — Adapter

候補:

- `engine/state_machine/adapter.py`: 既存Core snapshot/detail/configを最小観測へ変換。
- `engine/pivots.py`、`engine/analyzer.py`: 原則変更なし。必要な既存factはread-only adapterで読む。

Full Screening Scope 965を先に固定し、全codeのstatusを出す。候補964だけをループ起点にしない。Connorsをtrend countから除外する。銘柄masterの上場廃止・code廃止・後継code・scope removal reasonを監査し、構造化証拠がないOUT_OF_SCOPEをCLOSEDへ変換しない。

### Step 4 — Orchestrator and shadow output

候補:

- `engine/state_machine/service.py`: A品質→B identity→C transition→D atomic commit。
- `scripts/run_state_machine_shadow.py`: 明示的shadow command。
- `scripts/export_web.py`: Human approval後まで接続しない。初期実装ではshadow artifactを別pathに出す。

### Step 5 — Seed durability and workflow

workflow変更は実装の最終段階かつ別承認。Actions cacheとは別に検証可能なledger/state artifactを継承する。seed欠損で全件MINTするfallbackは実装しない。

### Step 6 — Consumer connection

複数shadow runと人間承認後だけ、latest phase/eventをoptional consumerへ接続する。既存契約の必須fieldにはしない。

## 3. Test Matrix

### Pure unit tests

- `transition-fixtures.json`の全scenarioをparameterize。
- T01–T26を最低1回ずつ通す。
- 970/969.99、90/91、20/21、gap 5/6、strategy 1/2の境界。
- null、0、負数、JSON上のNaN相当をfail closed。
- 初回BREAKOUT ordinal 1、REBREAKOUT ordinal 2以上。
- 突破前FAILED禁止、EXPIRED/CLOSEDから復帰禁止、post-breakout Pivot freeze。
- 最初の期限超過CURRENT観測でqualified crossがSETUP_EXPIREDより先に評価される。
- dynamic scope removalはPAUSED、structured delisting evidenceはCLOSED + SETUP_CLOSED 1件。

### Persistence tests

- decision/mint/revision/event/stateが1 transaction。
- 各insert直後のfailure injectionで孤立行0。
- same input retryでmint/event重複0。
- event short hash collisionはfull hash不一致で停止。
- current stateをevent/observation ledgerから同hashへrebuild。
- seed missing/hash mismatch/schema mismatchでmint 0。

### Adapter/run tests

- scope 965を固定し、8303相当をstatus行で保持。
- 960 current + 4 stale + 1 insufficientの件数invariant。
- stale 4件のphase/event不変。
- 一時OUT_OF_SCOPEは通常consumerから除外するがphaseを終端せず、恒久離脱証拠時だけCLOSEDにする。
- structured errorが銘柄またはstageへ紐づかない場合PARTIAL。
- Core BREAKOUTの5日継続がevent 1件だけ。
- T日のPivot evidenceがT-1以前。

### Version/replay tests

- same input/seed/versionでstate/event/evidence hash一致。
- same-day同hash no-op、異hash quarantine。
- out-of-orderはlive state不変、replay namespaceへ。
- sm1 stateへmigrationなしsm2を適用しない。

## 4. Acceptance Checks Before Shadow

1. Product Coreの既存test結果が変更前と一致。
2. 新fixture全件pass。
3. migrationはadditive。
4. scope/status/identity/event count invariantがpass。
5. seed破損時の全件MINTが0。
6. retry後のduplicate event/mintが0。
7. minimal observationにrank、plan、chart、full conditions、財務原票がない。
8. current state rebuild hashが一致。
9. final physical schemaの再benchmarkが435–540MB/年range内、または外れた理由とretention再改訂が承認済み。
10. shadow artifactを既存UI/Researchが読んでいない。

## 5. Explicitly Forbidden During Initial Implementation

- 旧964 legacy setupの新UIDへの自動LINK。
- codeだけ、state一致だけ、近いPivotだけによる複数候補の選択。
- stale/missingをFAILED、無取引や単なるOUT_OF_SCOPE継続をEXPIRED/CLOSEDとする処理。
- cutover Core BREAKOUTから過去breakout eventを推定。
- post-breakout tracking Pivotの当日primary Pivotへの差替え。
- current configで過去event/stateを暗黙再計算。
- SQLite transaction外でidentity/state/eventを個別保存。
- GitHub Actions cacheだけをseed正本にすること。
- Phase 2A phaseをranking scoreへ追加。
- Experimental setupをCore UIDへ自動統合。
- Human approval前のworkflow、GitHub Pages、本番DB変更。

## 6. Open Items Requiring Evidence, Not Convenience

| Item | Current proposal | Required evidence |
|---|---|---|
| Retry width | 3% | 1/2/3/5% sensitivity、rebreak率と再failure率 |
| Pre-breakout lifetime | 90 sessions | 60/90/120のsetup残存・noise |
| Post-breakout lifetime | 20 sessions | 10/20/30でValidation horizonとの整合 |
| Auto-link gap | 5 sessions | 実runのgap分布、false continuation review |
| Candidate support | 2 trend methods | 1/2/3でMINT数と後続成績 |
| Runtime footprint | representative 473MB/year、435–540MB range | final physical schemaで再測定。正規化削減を未実測で見込まない |
| Corporate action rebase | explicit migration | 既存Yahoo adjusted dataのscale/分割事例 |

これらを実装者が成功例に合わせて無断変更しない。変更案は新threshold versionと設計差分を先に提示する。

## 7. Artifacts to Produce in the Future Implementation Goal

- additive migrationとschema docs。
- pure state evaluator、identity resolver、storage、adapter、orchestrator。
- fixture-driven tests、failure-injection tests、replay tests。
- final physical schema benchmark report（今回のLimited Benchmarkをbaselineに比較）。
- shadow run quality report（scope/status/count/event/idempotency）。
- seed export/import/recovery手順。
- feature flagとrollback手順。
- consumer未接続の証明、または別承認されたoptional接続結果。

設計Human ReviewとLimited Benchmarkは完了した。このhandoffはproduct実装の直接承認ではない。次は別指示によるImplementation Goal作成、その後に実装する。
