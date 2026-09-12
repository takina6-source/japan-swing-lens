# Japan Swing Lens — Phase 2A State Machine Design Goal

## 0. Goal

Stable Setup Identity Designで確定した`core_setup_uid`を単位として、Japan Swing Lensの複数日状態を安全に追跡するPhase 2A State Machineを**設計する**。

次を明文化する。

- 観測事実と派生状態の境界。
- setup phaseの状態集合と全遷移。
- `LINK / MINT / AMBIGUOUS / NO_SETUP`のidentity decision規則。
- WATCH、BREAKOUT、FAILED、RETRY_WATCH、REBREAKOUT、失効・新setupの意味。
- 欠測、古い市場日、同日再run、scope離脱、version変更時の扱い。
- run完全性、最小観測保存、event履歴、durable seed、移行・rollback。
- 後続Implementation Goalが曖昧なく実装できる論理schema、擬似コード、固定fixture、受け入れ条件。

このGoalは**設計専用**である。product code、DB schema、workflow、公開成果物を変更しない。設計結果を人間がレビューし、明示的に承認するまでPhase 2Aを実装しない。

## 1. Approval and Fixed Prerequisites

### 1.1 Setup identity approval

2026-09-12のユーザー指示「Phase 2A State Machine Design Goalを作成して」を、setup identity設計のHuman Review Gateを通過し、本Goalを作成する承認として扱う。

次の契約を変更せず使用する。

1. Core setupの永続主キーは`core_setup_uid`。
2. formatは`csu1:<4桁code>:<SHA-256先頭160bit>`。
3. UIDはimmutable birth requestから一度だけ発行し、日次属性から再計算しない。
4. identityの正本はdurable ledger。
5. Pivot、state、手法集合、logic/strategy/threshold versionはUID材料にしない。
6. Pivot変更はrevision、手法加入・離脱はmembership履歴。
7. observation、signal/event、setup identityは別ID。
8. identity decisionは`LINK / MINT / AMBIGUOUS / NO_SETUP`。
9. CoreとExperimentalは別namespace。
10. 旧964件は全件`AMBIGUOUS`であり、自動的に新identityへ統合しない。
11. cutover後だけを正確なlineage保証範囲とする。
12. GitHub Actions cacheだけをidentity ledgerの正本にしない。

参照:

- `docs/SETUP_ID_DESIGN.md`
- `docs/SETUP_ID_PHASE2A_HANDOFF.md`
- `docs/SETUP_ID_DESIGN_RESULT.md`

このGoalでidentity formatやlegacy分類を再設計しない。問題が見つかった場合はState Machine側で妥協せず、setup identity設計へ差し戻す。

### 1.2 Runtime Verification facts

次を実測済みの固定事実として扱う。

| Fact | Result |
|---|---|
| 現行`主要500+Growth` scope | 965銘柄 |
| 最新公開ranking/detail | 964銘柄 |
| scopeとの差 | 8303 SBI新生銀行。価格履歴184本で最低200本未達 |
| 2026-09-10基準detail | 960銘柄 |
| 2026-09-09基準detail | 4銘柄 |
| snapshot error | 1件。ただし件数だけで原因復元不能 |
| DBの970/2/968/964 | 日次母集団減少ではなく、version差と同一run内の銘柄別基準日混在 |
| 既存signal追跡 | 185銘柄・247 signalのみ。全銘柄履歴ではない |

参照: `docs/PHASE2A_RUNTIME_VERIFICATION.md`

## 2. Absolute Principles

1. State Machineの単位は銘柄コード単独ではなく`core_setup_uid`。
2. 同一銘柄に複数setupが同時存在できる。
3. Coreの当日stateはOBSERVED_FACT、`current_phase`はDERIVED_STATEとして別フィールドにする。
4. state、event、identity decisionを同じenumやIDへ押し込めない。
5. 状態遷移に使った観測・Pivot revision・閾値version・理由を再現可能にする。
6. 新しい市場日の有効観測がない限り、市場状態を遷移させない。
7. 欠測、stale、fetch失敗、scope離脱をFAILEDやEXPIREDと推定しない。
8. 前日ではなく「同じsetupの前回受理済み観測」と比較する。
9. 未来情報を使わず、T日の判断はT時点で利用可能な事実だけで行う。
10. 同じ入力・同じledger・同じrule versionのreplayは同じ結果にする。
11. 同日再run、並行run、途中失敗でeventやidentityを二重発行しない。
12. threshold変更を過去状態へ暗黙に遡及適用しない。
13. full candidate、detail、chart、全価格履歴、財務原票を日次複製しない。
14. 現行Core判定・ランキング・Experimental・Research結果をこの設計で変更しない。
15. 新しい外部API、LLM判断、手動判断を通常runの必須依存にしない。
16. 原因不明を一般化したFAILEDへ丸めず、`UNKNOWN / AMBIGUOUS`を保持する。

## 3. Scope

### In Scope

- setup phaseの状態集合と意味。
- 状態とeventの分離。REBREAKOUT等を状態にするかeventにするかの比較・決定。
- 全許可遷移、禁止遷移、guard、優先順位、reason code。
- `LINK / MINT / AMBIGUOUS / NO_SETUP`の生成規則。
- active/inactive/expiredと、同銘柄内の複数setup選択。
- Pivot revision継続と新setup分岐の判断規則。
- FAILED→RETRY_WATCH、再BREAKOUT、失効に必要な閾値の設計。
- 既存閾値と新規閾値の区別、version管理、境界値。
- Full Screening Scopeを起点にしたrun manifestと銘柄別観測status。
- 日付、営業日、同日再run、欠測、out-of-order、scope離脱・復帰の意味。
- 最小観測fact、最新state、transition event、identity decisionの論理schema。
- atomic write、idempotency、durable seed、replay/rebuild。
- legacy/cutover、shadow、rollback、Downstream互換。
- 固定fixture、容量見積り、後続Runtime BenchmarkとImplementation Goalへの申し送り。

### Out of Scope

- product code、DB migration、workflow変更。
- git push、GitHub Pages公開、本番run。
- Stable Setup Identity format・hash方式の再設計。
- 旧964件の遡及identity付与。
- Coreの6手法、Consensus、ランキング、trade planの変更。
- 新しい投資手法や総合スコア。
- Phase 2BのUI鮮度表示、通知、メール、Morning Brief文章生成。
- Phase 3の成績評価・対照群分析・採用判断。
- 新市場データ取得、財務欠損修復。

## 4. Concepts to Keep Separate

設計書で最低限、次を別概念として定義する。

| Concept | Meaning |
|---|---|
| Run status | workflow全体の完全性。`COMPLETE / PARTIAL / FAILED`候補 |
| Observation status | 銘柄データを当runで使えるか |
| Core observed state | 現行Coreが当日返したstate |
| Setup phase | 複数日履歴からState Machineが導く状態 |
| Transition event | phaseが変わった出来事 |
| Market event | BREAKOUT、Pivot breach、recovery等の事実/派生event |
| Identity decision | LINK / MINT / AMBIGUOUS / NO_SETUP |
| Setup revision | 同一UID内のPivot・根拠版 |
| Strategy membership | 各手法setupとCore setupの期間付き関係 |

Coreの`FAILED`、Validationの`failed_breakout`、State MachineのFAILED phaseを同義として扱わない。それぞれの定義・採用可否・変換根拠を表にする。

## 5. Required Processing Order

1run内の順序を次の4段階として設計する。

```text
A. Run / observation quality classification
       ↓ current and usable only
B. Setup identity resolution decision
       ↓ LINK or MINT only
C. Per-core_setup_uid transition evaluation
       ↓ accepted transition/events
D. Atomic persistence and publish eligibility
```

- `AMBIGUOUS`と`NO_SETUP`はsetup phaseを遷移させない。
- stale・missing・failed observationはB/Cへ渡さず、品質事実だけ保存する。
- Dが失敗した場合、identityだけ、stateだけ、eventだけが残らないtransaction境界を定義する。
- 公開可否とstate commit可否を混同しない。公開失敗でledgerを巻き戻すか否かを明記する。

## 6. Universe and Run Completeness Contract

母集団はsnapshot candidateではなく、**そのrun開始時に確定したFull Screening Scope**を起点にする。

### 6.1 Required run manifest

- `run_id`、開始・終了時刻、commit、logic/strategy/threshold/state-machine/schema version。
- `expected_market_date`とその決定元。
- `scope_name`、master source date、scope member hash。
- `scope_total`。
- `price_history_eligible_total`。
- `observed_total`。
- `current_market_date_total`、`stale_market_date_total`。
- `insufficient_history_total`、`fetch_failed_total`、`analysis_failed_total`。
- `identity_link_total`、`identity_mint_total`、`identity_ambiguous_total`、`no_setup_total`。
- `transition_total`、`unchanged_total`。
- `ledger_seed_hash`、seed schema/version/count。
- 構造化error集計と銘柄別error参照。
- `run_status`と、その判定reason codes。

`scope_total = 各銘柄status件数`等の保存時invariantを定義する。Annual EPS診断件数を母集団総数の代理にしない。

### 6.2 Observation status

最低限、次を区別する。最終名称は設計で確定する。

- `CURRENT`
- `NO_NEW_MARKET_OBSERVATION`
- `STALE_MARKET_DATE`
- `INSUFFICIENT_PRICE_HISTORY`
- `FETCH_FAILED`
- `ANALYSIS_FAILED`
- `OUT_OF_SCOPE`
- `IDENTITY_AMBIGUOUS`
- `LEDGER_UNAVAILABLE`

8303はscope行を持つ`INSUFFICIENT_PRICE_HISTORY`であり、無言で消さない。2026-09-10の4銘柄は当日遷移を発火させず、無取引・配信遅延・fetch失敗を証拠がある範囲で分ける。原因を取得できなければ`UNKNOWN_STALE_REASON`を許可する。

## 7. State Model Design

### 7.1 Candidate semantics to cover

設計は少なくとも次の意味を表現できなければならない。

- setup形成中。
- Pivot接近・突破待ち。
- 初回突破。
- 突破後の継続観測。
- Pivot割れまたは突破失敗。
- 同一setupで再び突破候補へ戻った状態。
- 同一setupでの再突破event。
- setupの失効・終了。
- 判断不能または観測停止。

候補名は`FORMING / WATCH / BREAKOUT / POST_BREAKOUT / FAILED / RETRY_WATCH / REBREAKOUT / EXPIRED / UNKNOWN`。これをそのまま採用することは要求しない。

### 7.2 Required design comparison

次の2案以上を比較し、1案を採用する。

1. **Phase-heavy model**: REBREAKOUT等も持続stateにする。
2. **Event-centered model**: 持続phaseを最小化し、BREAKOUT/REBREAKOUTをeventとして別保存する。

比較軸:

- 同じ状態を何日保持するかの明確さ。
- event重複防止。
- Morning Briefの「何が変わったか」の説明容易性。
- Research/Validationとの結合。
- replay、version変更、欠測時の安全性。
- 保存量と実装複雑度。

### 7.3 Exact state specification

採用した各stateについて次を表にする。

- machine-readable enum。
- 日本語表示名。
- 意味と不変条件。
- entry条件。
- exit条件。
- terminalか再開可能か。
- 許可されるidentity decision。
- 必要なobserved facts。
- 欠測時の保持方法。
- UI表示でCore observed stateと混同しない名称。

## 8. Transition Matrix

すべての`from × event/guard × to`を列挙する。暗黙のelse遷移を作らない。

最低限、次を固定fixtureで扱う。

- 初回観測→FORMING/WATCH。
- WATCH→BREAKOUT。
- BREAKOUT→継続phase。
- BREAKOUT→Pivot breach→FAILED。
- FAILED→RETRY_WATCH。
- RETRY_WATCH→REBREAKOUT event。
- RETRY_WATCH→再FAILED。
- 任意active phase→EXPIREDまたはCLOSED。
- 同一setup内Pivot revision。
- 新setupのMINTと旧setupの扱い。
- stale/missing day。
- gap後の復帰。
- scope離脱・復帰。
- 同日再run。
- out-of-order observation。
- version変更。
- 同一銘柄の複数setup。

特に次の系列を完全に再現する。

```text
BREAKOUT → FAILED → WATCH相当 → WATCH相当 → BREAKOUT
```

最後のBREAKOUTが初回ではなく同一`core_setup_uid`のREBREAKOUTであること、途中のFAILEDを失わないこと、欠測が挟まる場合の判定差を示す。

禁止遷移はreason付きでrejectするか、no-opとして監査行を残すかを決定する。

## 9. Identity Resolution Rules

Stable Identityのdecision interfaceに対し、Phase 2Aが何を返すかを設計する。

### 9.1 LINK

- 同じcodeの既存UIDだけを候補にする。
- どの継続事実・revision・membershipなら一意にLINKできるかを定義する。
- state一致だけ、Pivot価格一致だけ、legacy ID一致だけではLINK証明にしない。
- 複数候補なら優先順位を勝手に付けず、明示規則がなければAMBIGUOUS。

### 9.2 MINT

- 新setupと判断する必要条件。
- cutover初回の`CUTOVER_BOOTSTRAP`。
- `origin_slot`の安定した採番。辞書順、strategy membership、価格順等の候補を比較し、入力順に依存しない規則を決める。
- 同日再run・並行runのmint idempotency。

### 9.3 AMBIGUOUS / NO_SETUP

- 同一か新規か証明できない場合はAMBIGUOUS。
- setup構造自体が観測されない場合はNO_SETUP。
- どちらも既存setupをFAILED/EXPIREDへ遷移させる証拠にしない。
- 後日解決したときの訂正方法と、過去行を上書きするかappend-only correctionにするかを決める。

## 10. Pivot Continuity and Revision Boundary

現在の最寄りPivotは日々採用手法が変わり得る。State Machineは「今日の最寄りPivot」だけを過去setupへ適用しない。

設計で決定する。

- setupに固定するtracking Pivotと、日次observed primary Pivotの区別。
- Pivot revisionを許可する条件。
- revision前後のFAILED/recovery判定でどのPivotを使用するか。
- adjusted price改訂や株式分割時の扱い。
- structure/PRACTICALとlookback PROXYの継続性差。
- 1setup内に複数strategy Pivotがある場合の基準。
- revisionが新setup MINTを要求する境界。

数値閾値を採用する場合は、State Machine専用configとして名前・単位・境界包含・根拠・versionを明記する。

## 11. FAILED / RETRY_WATCH / REBREAKOUT

### 11.1 FAILED source selection

次を比較し、State Machineで採用する定義を1つまたは明示的な組合せとして決める。

- Core observed `FAILED`。
- 手法別`FAILED`。
- 初回固定tracking Pivotに対するValidation `failed_breakout`。
- State Machine tracking Pivotに対する新しい派生判定。

「過去にBREAKOUTしていないのにFAILED」「別Pivotに対するFAILED」を防ぐguardを定義する。

### 11.2 RETRY_WATCH

最低限、次の設計判断を行う。

- 同じ`core_setup_uid`であること。
- 先行BREAKOUTとFAILEDの存在。
- tracking Pivotの継続性。
- 回復価格の基準と境界。
- 必要な連続観測数または営業日数。
- 失敗後の最大有効期間。
- 出来高・手法合致数を必要条件にするか。
- 欠測期間のカウント方法。

### 11.3 REBREAKOUT

- 初回BREAKOUTと区別するevent contract。
- 同じsetup内で何回発生可能か。
- event IDのidempotency key。
- RETRY_WATCHを経由しない直接回復を許可するか。
- 再FAILED後の繰返し回数・終了条件。

## 12. Threshold Design

既存値を無条件に再利用しない。まず現行意味をinventory化する。

| Existing config | Current meaning |
|---|---|
| `pivot.recent_breakout_days = 5` | Coreがrecent BREAKOUTを保持する営業日本数 |
| `pivot.extended_above_pivot_pct = 8` | CoreのEXTENDED判定 |
| `pivot.failed_below_pivot_pct = 3` | Core/ValidationのPivot割れ判定に利用 |
| `pivot.setup_max_age_days = 90` | 実装上はcalendar dayではなく価格履歴本数ベース |
| `tracking.max_sessions = 20` | 既存signal追跡上限 |

設計成果物には次を含める。

- 再利用する既存閾値と、同じ意味である根拠。
- 新規State Machine専用閾値。
- 単位がcalendar dayかtrading sessionか。
- `>` / `>=`、`<` / `<=`の境界。
- null・非有限・非正価格時のfail-closed動作。
- 候補値、採用値、却下値、判断理由。
- sensitivity検証が必要な値。
- threshold version変更時の既存state扱い。

データ根拠が不足する値は「暫定値」と「要検証」を明示する。設計Goal内で、都合のよい成功例に合わせて閾値を調整しない。

## 13. Time and Replay Semantics

最低限、次を定義する。

- `observed_at`、`analysis_date`、`expected_market_date`、`detected_at`、`effective_date`。
- T日のPivotはT-1までのOHLCVから作る現行look-ahead防止を維持。
- 同じ`analysis_date`の再runを新しい市場日として数えない。
- 同日再runの優先版、supersede、audit保持。
- analysis dateが古い観測ではstateを遷移させない。
- gapを営業日数に含めるか、観測数で数えるか。
- out-of-order入力をquarantineするか、別replay namespaceで再構築するか。
- historical replayとlive stateを同じテーブルで混ぜない方法。
- rule version変更時に新lineageを作るか、明示migrationするか。

状態変化日は市場で実際に起きた日と、システムが初めて検出した日を分ける。欠測区間内の正確な発生日を推定しない。

## 14. Minimal Persistence Contract

### 14.1 Run manifest

Section 6の件数・version・hash・status・error参照だけを保存する。

### 14.2 Universe observation status

scope全銘柄について、run ID、code、status、analysis date、latest price date、history count、reason codesを保存する。分析対象外も1行持つ。

### 14.3 Accepted market observation

許可リスト候補:

- run ID、observation UID、code、analysis date、observed_at。
- close。
- Core observed state、5トレンド手法state、Connors state。
- aligned/breakout count。
- observed primary Pivotの価格・strategy・type・basis・fidelity。
- tracking Pivot revision参照。
- legacy総合/手法setup ID。
- logic/strategy/threshold version。
- observation quality status、reason codes。
- Core UID nullable、identity resolution status、state-machine version。

rank、name、plan、全conditions、chart、財務原票は状態遷移に必要性を証明できなければ保存しない。

### 14.4 Current setup state

- Core UID、current phase、phase entered observation/date。
- latest accepted observation。
- current tracking Pivot revision。
- 必要最小限のprior event参照。
- state-machine version。
- optimistic concurrency/version number。

### 14.5 Transition event

- event UID、Core UID、from/to phase。
- trigger/event type。
- effective/detected date。
- observation UID、Pivot revision。
- rule/threshold version、reason codes、evidence hash。
- idempotency key、created_at。

append-onlyを基本とし、訂正はcorrection/supersede eventで表現する。最終schemaは設計成果物で確定する。

## 15. Event ID / Idempotency

setup UIDとは別にtransition/event IDを設計する。

最低限、次を満たす。

- 同じsetup、同じevent type、同じ有効観測、同じrule versionの再runで同じeventになる。
- 初回BREAKOUTと2回目以降のREBREAKOUTを区別する。
- 同日に異なるsetupで発生したeventが衝突しない。
- correction eventを元eventへ結合できる。
- 短縮hash衝突を検出する。
- signal_idとのdual-reference期間を定義する。

event発生条件はTransition Matrixと完全に一致させる。

## 16. Atomicity / Durable State

設計でtransaction境界と復元順序を決める。

- identity decision、identity mint/link、revision、state update、event insertを一貫してcommit。
- run manifestを`STARTED`からterminalへ更新するタイミング。
- 途中失敗runの観測を次runのprevious accepted stateにしない。
- durable ledger/state seedのschema、hash、row count、producer runを検証。
- seed欠損・破損時に全件MINTや初期state再生成を行わない。
- GitHub Actions cacheは性能補助であり正本ではない。
- publish前にreferential integrityと件数invariantを検証。
- publish失敗時でも確定済みstateの再run重複を防ぐ方法。

新DB製品は導入せず、既存SQLiteと静的JSON公開の構成を前提にする。

## 17. Versioning

最低限、次を別々に保存する。

- `logic_version`
- `strategy_version`
- `threshold_version`
- `identity_version`
- `state_machine_version`
- `observation_schema_version`
- `event_schema_version`
- `ledger_seed_version/hash`

version変更時に、既存phaseを維持、migration、replay、別lineageのどれにするかを変更種別ごとに決める。現在のconfigを過去観測へ遡及適用しない。

## 18. Downstream Compatibility

次との関係を表で確定する。

- Core snapshot/detail。
- Morning Brief Phase 1。
- `signal_snapshots / signal_history`。
- Validation / Control。
- Experimental。
- Research。
- Committee export。
- 将来のMorning Brief What Changed表示。

必須条件:

- 既存signal/control/research履歴を書き換えない。
- legacy signal IDと新event UIDを同義にしない。
- Phase 1 Morning Briefの現契約を壊さない。
- State Machine derived phaseをCoreランキングへ混入させない。
- Experimental identity/stateをCore setup phaseへ自動統合しない。
- 新stateがResearchへ渡る場合、発生時点versionとevent provenanceを保持する。

## 19. Migration / Cutover / Rollback

設計成果物に実行前提ではない計画を含める。

1. schema追加のみ。
2. identity ledger/state storeのdurable seed経路を先に確立。
3. cutover runで新identityを`CUTOVER_BOOTSTRAP`として発行。
4. bootstrap時点より前のphase/eventを推定生成しない。
5. 初期phaseの根拠を`BOOTSTRAP_OBSERVED`として記録。
6. shadow writeで新stateを計算するが既存UI/Researchへ影響させない。
7. 複数runでidempotency、欠測、安全性を確認。
8. 人間承認後にWhat Changed等のconsumerへ接続。

Rollback:

- feature flagでstate machine write/readを停止。
- 新テーブル・artifactは削除せずread-only凍結。
- 旧Core/Validation経路を維持。
- 既存履歴を新phaseへ物理更新しないため逆migrationを不要にする。
- cutover後eventをlegacy signalへ捏造変換しない。

## 20. Capacity and Retention Design

964～965銘柄、年間約250runを基準に、選択schemaの実容量を見積もる。

- run manifest。
- scope全件status。
- accepted observation。
- current state。
- event/revision/membership。
- index、SQLite page overhead、JSON公開artifact。

既存概算145～289MB/年をそのまま採用せず、新schemaのフィールド長とevent発生率を明示して再計算する。full candidate複製時の約907MB/年案は選択肢から除外する。

次のDesign後Runtime Benchmarkで、固定入力約1,000行を隔離DBへ保存し、容量、transaction時間、seed復元時間を測定する計画を作る。無期限保持、圧縮、年次archive、公開履歴件数も設計する。

## 21. Design Verification Fixtures

本番コード・DB・外部取得を使わず、合成fixtureと保存済み読み取り入力で設計を検証する。

最低限、次のfixtureを機械可読JSONで定義する。

### Normal sequences

- FORMING→WATCH→BREAKOUT。
- WATCH継続。
- BREAKOUT後継続。
- BREAKOUT→FAILED。
- BREAKOUT→FAILED→RETRY_WATCH→REBREAKOUT。
- 2回目のFAILED→RETRY_WATCH→REBREAKOUT。
- EXPIRED/CLOSED後の新setup MINT。
- Pivot revisionを伴う同一setup LINK。
- 同一銘柄の複数setup並行。

### Boundary sequences

- 各価格率・営業日数閾値の直前、等号、直後。
- 価格null、Pivot null、0、NaN相当を入力拒否。
- 1手法だけ/複数手法の境界。
- same-day rerun、並行run、同一event retry。
- rule version変更。

### Missing and disorder

- 4銘柄のような前日基準観測。
- 8303のような履歴不足。
- fetch/analysis error。
- scope離脱・復帰。
- 1日/複数日のgap。
- out-of-order observation。
- ledger seed欠損・hash不一致。
- identity候補0件/1件/複数件。
- PARTIAL run後の正常run。

### Look-ahead and replay

- T日判定にT+1情報が入らない。
- 同じ入力、seed、versionのreplayでstate/event/hash一致。
- 途中commit失敗後のretryで孤立行・重複eventなし。

各fixtureに、input observations、previous state、identity decision、expected phase、expected events、no-op/reject、reason codesを記載する。

## 22. Deliverables

1. `docs/PHASE2A_STATE_MACHINE_DESIGN.md`
   - 採用モデル、却下案、Decision Summary。
   - state定義、図、Transition Matrix、guard優先順位。
   - identity resolution規則。
   - Pivot continuity、FAILED/retry/rebreakout設計。
   - threshold表、時間・欠測・replay規則。
   - logical persistence schema、atomicity、versioning。
2. `docs/examples/phase2a-state-machine/transition-fixtures.json`
   - 正常、境界、欠測、replayの固定fixture。
3. run manifest / observation / current state / eventのJSON例またはschema案。
4. Downstream Compatibility Matrix。
5. Migration / Cutover / Rollback Plan。
6. 容量見積りとDesign後Runtime Benchmark Plan。
7. `docs/PHASE2A_IMPLEMENTATION_HANDOFF.md`
   - 実装順序、変更候補、禁止事項、テスト、未決事項。
8. `docs/PHASE2A_STATE_MACHINE_DESIGN_RESULT.md`
   - Acceptance Criteria対応、検証結果、Human Review項目。

## 23. Acceptance Criteria

- setup identity設計の12固定契約を維持している。
- state machineの単位が`core_setup_uid`である。
- run status、observation status、Core observed state、derived phase、eventを分離している。
- scope全965銘柄を起点に、分析不能銘柄もstatus行として追跡できる。
- 8303と日付遅延4銘柄を無言で除外・遷移していない。
- exact state listと各stateのentry/exit/invariantがある。
- 全許可・禁止遷移とguard優先順位が機械可読に定義されている。
- BREAKOUT→FAILED→RETRY_WATCH→REBREAKOUTを同じCore UIDで再現できる。
- 初回BREAKOUTとREBREAKOUT eventを区別している。
- Core/手法/ValidationのFAILED定義を区別し、採用根拠がある。
- tracking Pivotと日次primary Pivotを区別している。
- Pivot revisionと新setup MINTの境界を定義している。
- LINK/MINT/AMBIGUOUS/NO_SETUPを一意に決定または安全に保留できる。
- 同一銘柄の複数setupを表現できる。
- RETRY_WATCH、失効、再突破の閾値・単位・境界・versionが明示されている。
- stale/missing/scope離脱だけでFAILED/EXPIREDにならない。
- 同日再run、並行run、out-of-order、gap、version変更の扱いがある。
- look-aheadを防ぎ、実際の市場日と検出日を分離している。
- run manifestの件数invariantと構造化error契約がある。
- identity/state/eventを原子的・idempotentに保存できる論理設計がある。
- durable seedが欠損した際に全件再採番しない。
- 最小観測事実だけを保存し、full candidate/detail/chart/原票を複製しない。
- 964～965銘柄×250runの容量再見積りがある。
- fixtureが通常・境界・欠測・replayを網羅する。
- 既存Core、ranking、Validation、Research、Experimental、Morning Brief Phase 1を変更していない。
- product code、DB、workflow、公開成果物を変更していない。
- 実装前Human Review Gateがある。

## 24. Human Review Gate

設計完了時に、最低限次を人間へ提示する。

1. 採用state/eventモデルと状態図。
2. 全Transition Matrix。
3. FAILEDの採用定義。
4. RETRY_WATCH / REBREAKOUT / expirationの閾値と根拠。
5. Pivot revisionと新setupの境界。
6. identity LINK/MINT/AMBIGUOUS規則。
7. stale/gap/scope離脱・復帰の扱い。
8. same-day rerun・version変更・replay規則。
9. schemaと年間容量見積り。
10. cutover、durable seed、rollback。
11. 暫定閾値とDesign後Runtime Benchmarkで検証する項目。

承認されるまでPhase 2A Implementation Goalを作成・実行しない。

## 25. Required Sequence After This Goal

1. 本Goalに基づくState Machine設計。
2. 設計fixtureのオフライン検証。
3. Human Reviewと必要な設計修正。
4. 設計承認。
5. 固定入力約1,000行で容量・transaction・seed復元の限定Runtime Benchmark。
6. Benchmark結果を反映したPhase 2A Implementation Goal作成。
7. 別指示による実装・テスト。

この順序を省略せず、本Goalだけを根拠に実装・公開へ進まない。
