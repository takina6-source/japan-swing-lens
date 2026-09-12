# Japan Swing Lens — Phase 2A State Machine Implementation Goal

作成日: 2026-09-12 JST
対象リポジトリ: `takina6-source/japan-swing-lens`
対象作業ツリー: `/Users/inadatakumi/Documents/Codex/2026-08-23/files-pasted-by-the-user-goal/outputs/momentum-swing-engine`
Goal種別: Product code implementation / local shadow verification
公開状態: このGoalだけを根拠とした本番DB適用、workflow接続、GitHub Pages公開は行わない

## 0. Goal

承認済みのPhase 2A State Machine設計を、既存Core分析を変更しない独立したevent-centered state machineとして実装する。

実装は、cutover後に発行する安定した`core_setup_uid`を単位として、日次のCore snapshot/detail/configから最小限の観測事実を読み取り、identity decision、phase transition、Pivot revision、event、run qualityをSQLiteへ原子的かつ再現可能に保存する。

このGoalの完了時点では、次の状態を目指す。

1. 7つの永続phaseとT01–T26がpure domain layerとして実装されている。
2. stable setup identity、observation、decision、event、current stateを保存するadditive schemaが実装されている。
3. Full Screening Scopeを先に確定し、全銘柄の観測statusを生成するadapterが実装されている。
4. 1 runを単一transactionで保存し、再実行・競合・seed破損・途中失敗にfail closedできる。
5. 既存product consumerから独立したshadow CLIと非公開artifactを生成できる。
6. fixed fixture、persistence、replay、seed、adapter、regression testがpassする。
7. final physical schemaでLimited Runtime Benchmarkを再実行し、容量・時間・整合性を記録する。
8. Product Core ranking、Validation、Experimental、Research、Morning Brief、公開dashboardの出力が変更されていない。

このGoalは設計のやり直しではない。実装者は、成功しやすいケースに合わせてphase、閾値、評価順序、identity semanticsを変更しない。

## 1. Fixed Inputs and Authority Order

実装判断のauthorityは次の順序とする。

1. `docs/SETUP_ID_DESIGN.md`または同等のStable Setup Identity Design最終成果物。
2. `docs/PHASE2A_STATE_MACHINE_DESIGN.md`。
3. `docs/PHASE2A_STATE_MACHINE_DESIGN_RESULT.md`。
4. `docs/PHASE2A_LIMITED_RUNTIME_BENCHMARK.md`。
5. `docs/PHASE2A_IMPLEMENTATION_HANDOFF.md`。
6. `docs/examples/phase2a-state-machine/transition-fixtures.json`。
7. `docs/examples/phase2a-state-machine/contract-examples.json`。

文書間に矛盾がある場合は、後から承認された明示的な修正を優先する。ただし、実装者判断で解釈を確定せず、差分と候補をCompletion Reportへ記録してHuman Reviewへ戻す。

### 1.1 Approved facts

- Persistent phaseは`FORMING / WATCH / POST_BREAKOUT / FAILED / RETRY_WATCH / EXPIRED / CLOSED`の7つ。
- BREAKOUTとREBREAKOUTはphaseではなくevent。
- State Machineの主語は銘柄コード単独ではなく`core_setup_uid`。
- UIDは`csu1:<4桁code>:<SHA-256先頭160-bitのlowercase hex>`形式。
- durable identity ledgerを正本とし、日次属性から既存UIDを再計算しない。
- 旧964件は全件AMBIGUOUSであり、cutover以前へ遡及LINKしない。
- Identity decisionは`LINK / MINT / AMBIGUOUS / NO_SETUP`。
- Threshold versionは`state_machine_threshold_version = smt1`。
- failure lineはtracking Pivotの3%超下。Pivot 1,000円なら970円ちょうどはFAILEDではなく、970円未満だけFAILED。
- retry zoneはtracking Pivot以下0–3%以内、aligned trend strategies 2以上。
- qualified crossは前回accepted close `<= Pivot`かつ当日close `> Pivot`、breakout trend strategies 2以上。
- pre-breakout lifetimeは90 market sessions、post-breakout lifetimeは20 market sessions、auto-link gapは5 market sessions以下。
- 最初の期限超過CURRENT観測ではqualified crossをexpiryより先に評価する。
- gap 6 market sessions以上ではcrossを推定せず、identityを安全に自動LINKしない。
- tracking Pivotはinitial breakout後に凍結する。
- Core BREAKOUTでcutoverする場合、`BOOTSTRAP_OBSERVED`をanchorにできるが、過去のBREAKOUT event/dateを捏造しない。
- stale、missing、error、insufficient history、一時的OUT_OF_SCOPE、AMBIGUOUSではphaseを変えない。
- `CLOSED`は構造化された恒久離脱証拠がある場合だけ使用する。
- current runでin-scopeかつnonterminalなsetupだけが将来の通常consumer表示対象。`SETUP_CLOSED`は発生日に一度だけ通知可能。
- same-day same hashはNO_OP、same-day different hashはquarantine、out-of-order live inputはreplay namespaceへ隔離する。
- state-machine version変更は明示的migration/shadow lineageなしに既存stateへ適用しない。
- 実測planning baselineは473MB/年、sensitivity envelopeは435–540MB/年。
- hot operational DBはrolling 12か月。古いrun/scope/observation/decisionは年次immutable archiveへ移し、archive込みで最低3年参照可能とする。
- identity ledger、current state、active Pivot/membership、lineageに必要なevent anchorはcompact durable seedとして無期限保持する。

### 1.2 Repository state

Goal作成時のHEADは`9aa5fe332b3fd3b89457ab80fed00cd9daa7434c`。

作業ツリーには既存の未コミット変更と多数の生成済みdashboard artifactが存在する。これらはユーザーの作業資産であり、このGoalの実装者は次を守る。

- 実装開始時に`git status --short`、関連ファイルのdiff、HEADを記録する。
- dirty stateをcleanにする目的でreset、checkout、stash、削除を行わない。
- Phase 2Aと無関係な変更をformat、修正、commit対象へ含めない。
- 変更が重なる既存ファイルでは、現在の作業ツリーをbaselineとして局所的に編集する。
- 自動生成された公開dataの全再生成は、このGoalでは行わない。
- Goal実行完了時にPhase 2A固有の変更一覧と、開始前から存在した変更を区別して報告する。

## 2. Non-Negotiable Principles

### 2.1 Additive and isolated

Phase 2Aは既存Core分析の後段に置くread-only adapterから入力する。既存のrank、state、score、condition、trade plan、Validation、Experimental、ResearchをPhase 2A都合で変更しない。

初期実装は明示的に指定されたSQLite pathだけを開く。通常のapp起動、`scripts/export_web.py`、既存`Database`初期化が、暗黙にPhase 2A tableを作成・更新してはならない。

### 2.2 Event-centered and auditable

Current stateは再構築可能なcacheである。監査の正本はidentity ledger、accepted observation、identity decision、Pivot revision、eventおよびrun metadataとする。

phaseだけを上書きして理由を失う実装は禁止する。

### 2.3 Minimal facts only

保存するのは状態遷移に必要な観測事実とprovenanceだけとする。full candidate object、full detail、chart series、rank、trade plan、全condition、財務原票を複製しない。

### 2.4 Fail closed

seed欠損、hash不一致、schema/version不一致、same-day conflict、順序逆転、identity ambiguity、非finite price、不正なmarket session indexでは、安全側に停止または隔離する。全件MINT、推測LINK、phase進行へfallbackしない。

### 2.5 Deterministic

同じinput、seed、config、versionから、同じdecision、state、event、evidence hashを生成する。pure evaluatorへnetwork、filesystem、現在時刻、SQLite connectionを持ち込まない。

### 2.6 No silent consumer connection

Phase 2Aのphase/eventを既存UI、ランキング、Morning Brief、Research、公開JSONへ接続しない。shadow artifactは公開pathの外へ出し、別の明示的Cutover／Deployment Goalまでconsumer未接続を維持する。

## 3. Scope

### 3.1 In scope

- State Machine packageと型定義。
- T01–T26のpure transition evaluator。
- `smt1` guard実装。
- Stable identity resolverとUID ledger連携。
- Minimal Core observation adapter。
- Additive SQLite schema、migration、repository。
- Run orchestrationとtransaction boundary。
- Idempotency、optimistic concurrency、rejection/quarantine、replay namespace。
- Pivot continuity、strategy membership、event生成。
- Durable seedのlocal export/import/verify/rebuild機能。
- Explicit cutover initialization command。
- Shadow-only CLIとprivate artifact schema。
- 既存fixed fixtureを用いたtest。
- Failure injection、seed corruption、replay、concurrency test。
- Final physical schema benchmark。
- 実装結果、benchmark、shadow readiness、既存consumer未接続の報告。

### 3.2 Out of scope

- GitHub Pagesへの公開。
- `update-dashboard.yml`へのPhase 2A run追加。
- GitHub Actions上の永続seed正本の方式決定・接続。
- Production DBまたは現在の`data/momentum.db`へのmigration実行。
- `scripts/export_web.py`からの自動実行。
- 公開dashboardへのphase/event UI追加。
- ランキング点数や合致数へのPhase 2A加点。
- Morning Brief What Changedへの接続。
- Validation/Research成果指標への接続。
- 旧964 setupのretroactive backfill/link。
- Experimental setupのCore UID統合。
- corporate actionによる過去stateの自動rebase。
- `smt1`閾値の調整。
- 12か月超historyの実削除またはarchive運用開始。
- 新しい外部サービス、認証情報、有料data sourceの導入。
- commit、push、release、deploy。

## 4. Required Implementation Sequence and Hard Gates

実装はGate順に進める。後続Gateを通すために前段の失敗を隠さない。

### Gate 0 — Preflight and change ownership

1. HEAD、dirty paths、Phase 2A関連既存artifactを記録する。
2. `AGENTS.md`等のrepository instructionがあれば読む。
3. 既存test commandとPython versionを確認する。
4. Product DBを開かずに作業できるtemp pathを確保する。
5. 既存変更とのoverlapを確認し、Phase 2A以外の変更を保存対象から除外する。

停止条件:

- Phase 2A実装に必要な既存ファイルが競合状態で意味を確定できない。
- 承認済み設計とfixtureの不一致を解消できない。
- test実行が本番DBや公開artifactを自動変更する構造になっている。

### Gate 1 — Pure domain layer

推奨module:

- `engine/state_machine/__init__.py`
- `engine/state_machine/models.py`
- `engine/state_machine/guards.py`
- `engine/state_machine/transitions.py`
- `engine/state_machine/ids.py`
- `engine/state_machine/identity_resolver.py`
- `engine/state_machine/config.py`

このGateではI/Oを実装しない。fixture全件をmemory上で評価し、T01–T26と境界値がpassしてから次へ進む。

### Gate 2 — Persistence and migration

推奨module:

- `engine/state_machine/storage.py`
- `engine/state_machine/schema.py`またはversioned SQL resource。
- 必要な場合だけ`engine/database.py`へ明示的opt-in entry pointを追加。

Migrationは任意の明示SQLite connection/pathに対して適用できる構造にする。既存`Database._init()`からの自動適用は禁止する。

Temp DBでschema、constraint、rollback、rebuild、migration idempotencyを確認するまでadapterへ接続しない。

### Gate 3 — Adapter and run quality

推奨module:

- `engine/state_machine/adapter.py`
- `engine/state_machine/service.py`

既存Core snapshot/detail/configを毎run読み、Full Screening Scopeを先に確定する。candidate listだけを母集団にしてはならない。

### Gate 4 — Seed and recovery

Local seed bundleのexport、hash、verify、import、current state rebuildを実装する。通常runはverified seedまたは既存verified DBを必要とする。空状態からの初期化は明示的cutover commandだけに限定する。

### Gate 5 — Shadow CLI and artifact

推奨script:

- `scripts/run_state_machine_shadow.py`

明示的`--db`、`--input`または既存Core artifact path、`--output-dir`、versionを受け取る。defaultで`data/momentum.db`や`public/dashboard`を選ばない。

### Gate 6 — Final physical benchmark

Prototype benchmarkをfinal schemaへ接続し、同じ18 scenarioまたは同等以上の固定負荷で再測定する。結果をJSONとMarkdownへ固定する。

### Gate 7 — Regression and readiness review

既存product test、新規test、change-boundary checkを実行する。公開workflow、公開artifact、本番DBが未変更であることを証明し、Implementation Resultを作成する。

## 5. Domain Contract

### 5.1 Required enums

少なくとも次をstring enumまたは同等の閉じた型として定義する。

- `SetupPhase`: 7 phase。
- `SetupEventType`: `SETUP_MINTED`, `BOOTSTRAP_OBSERVED`, `WATCH_ENTERED`, `BREAKOUT_CONFIRMED`, `FAILED_CONFIRMED`, `RETRY_WATCH_ENTERED`, `REBREAKOUT_CONFIRMED`, `PIVOT_REVISED`, `SETUP_EXPIRED`, `SETUP_CLOSED`, `CONTINUITY_RESUMED`, `CORRECTION_RECORDED`。
- `IdentityDecisionType`: `LINK`, `MINT`, `AMBIGUOUS`, `NO_SETUP`。
- `ObservationStatus`: `CURRENT`, `NO_NEW_MARKET_OBSERVATION`, `STALE_MARKET_DATE`, `INSUFFICIENT_PRICE_HISTORY`, `FETCH_FAILED`, `ANALYSIS_FAILED`, `OUT_OF_SCOPE`, `INVALID_INPUT`。
- `RunStatus`: `COMPLETE`, `PARTIAL`, `FAILED`。
- `CoverageStatus`: `FULL`, `DEGRADED`, `UNKNOWN`。
- `PublishEligibility`: `ELIGIBLE`, `ELIGIBLE_DEGRADED`, `BLOCKED`。
- `ContinuityStatus`: 少なくともnormal、paused、suspended gap、terminalを表現できる閉じた値。
- `DecisionAction`: transition、keep、reject、quarantine、replay routingを区別できる値。

Enum名をUI表示文言として直接流用しない。保存値と将来の日本語表示labelを分離する。

### 5.2 Immutable input value objects

最低限、次をimmutable value objectとして用意する。

- `RunContext`
- `ScopeMemberFact`
- `CoreObservation`
- `IdentityContext`
- `SetupState`
- `PivotRevision`
- `TransitionDecision`
- `SetupEvent`
- `PermanentExitEvidence`

各objectは、価格のfinite/positive、4桁code、ISO date/time、market session index、version文字列、enum値をconstructorまたはexplicit validatorで検査する。

### 5.3 No hidden time

`datetime.now()`やtodayをdomain layer内で呼ばない。`generated_at`、`observed_for_date`、`effective_date`、`market_session_index`はcallerから明示的に渡す。

## 6. Guard and Transition Requirements

### 6.1 Candidate gate

Current observationが次をすべて満たす場合にnew Core setup candidateとなる。

- observation statusがCURRENT。
- Core observed stateが`SETUP FORMING / BREAKOUT WATCH / BREAKOUT`のいずれか。
- aligned trend strategy countが2以上。
- closeとprimary Pivotがfiniteかつ正。

Connors系strategyはtrend support、breakout supportのcountから除外する。

Candidate gate不成立でも、既存nonterminal UIDの監視継続に必要な安全なLINKは可能。ただし新規MINTはしない。

### 6.2 Evaluation precedence

Pure evaluatorは必ず次の順序で判定する。

1. Schema、quality、version、seed、date/order validation。
2. 既存phaseがEXPIRED/CLOSEDなら同UIDへのtransitionをreject。
3. Structured permanent-exit evidenceがあればCLOSED。
4. CURRENTかつ安全にLINKでき、from phaseがFORMING/WATCH/FAILED/RETRY_WATCHならqualified cross。
5. crossがなく、session lifetime超過ならEXPIRED。
6. failure、retry、watch/forming movement、continuation。
7. どのruleにも該当しなければKEEP。

有効期限切れ日のcrossを落とすexpiry-first実装は禁止する。

### 6.3 Transition table

`docs/PHASE2A_STATE_MACHINE_DESIGN.md` Section 7のT01–T26を、一意な`transition_id`を返すexecutable contractとして実装する。

- Fixtureは各transition IDを最低1回通す。
- 複数ruleが真になるinputでもprecedenceにより1つのprimary transitionだけを返す。
- T23のPivot revisionは当日のtransition評価後に適用し、次回観測からtrackingに使う。
- Initial breakout後のobserved primary Pivot変更はtracking Pivotを更新しない。
- FAILEDはconfirmed breakoutまたはcutover bootstrap anchorがないsetupに発生させない。
- REBREAKOUTのordinalは2以上。
- EXPIRED/CLOSEDは同UIDでreopenしない。

### 6.4 Exact boundary tests

最低限、次をtable-driven testにする。

- failure: 970.00はfalse、969.99はtrue（Pivot 1,000）。
- retry distance: 0%、3%はinclusive、3%超はfalse。
- aligned/breakout strategies: 1はfalse、2はtrue。
- auto-link gap: 5 sessionsは対象、6 sessionsはAMBIGUOUS/SUSPENDED_GAP。
- pre-breakout age: 90は有効、91相当の最初の超過CURRENTでcross優先後expire。
- post-breakout age: 20は有効、21相当の最初の超過CURRENTでrebreakout優先後expire。
- previous close == Pivot、current close > Pivotはcross。
- previous close > Pivotの継続はcrossではない。
- stale dayに価格がcrossして見えてもeventを生成しない。
- gap >5の両端でcross条件を満たしてもeventを推定しない。

## 7. Identity Resolver Requirements

### 7.1 Stable UID

UID issuanceはStable Setup Identity Designのcanonical inputとalgorithmを単一実装へ集約する。short hash表示とfull SHA-256監査値を分け、short collision時にfull hash不一致なら停止する。

既存UIDはledgerから取得し、銘柄属性、Pivot、strategy membership、phase、versionの変化で再採番しない。

### 7.2 Resolver order

最低限、次の順でdecisionする。

1. ObservationがCURRENTでなければidentity decisionを作らず、品質reasonだけを保存する。
2. Ledgerが不正または未検証ならresolverを開始せず、`LEDGER_UNAVAILABLE` blocker、UID null、遷移なしとする。
3. Explicit durable slot mappingが1件なら、code、namespace、versionを検証してLINKする。
4. Gap 6 market sessions以上、scope復帰、lineage seedの空白がある場合は、自動LINK/MINTせずAMBIGUOUSとする。
5. Explicit mappingがなく、同一codeのnonterminal Core setupが1件だけで、accepted observation gapが5 market sessions以下ならLINKする。
6. Nonterminal候補が複数でexplicit mappingがなければAMBIGUOUSとする。
7. Nonterminal候補が0件でcandidate gate成立ならMINTする。
8. Nonterminal候補が0件でcandidate gate不成立ならNO_SETUPとする。

同じcode、近いPivot、同じCore stateだけを根拠に複数候補から選ばない。

### 7.3 Cutover

- 旧964件を新UIDへLINKしない。
- Cutover日のcurrent observationからforward-onlyでMINTする。
- Core FORMING/WATCHは対応phaseから開始する。
- Core BREAKOUTはPOST_BREAKOUTへbootstrapできる。
- Bootstrapでは`SETUP_MINTED`と`BOOTSTRAP_OBSERVED`を保存し、過去BREAKOUT eventを生成しない。
- Bootstrapのbreakout cycleは1、20-session clockはcutover sessionから開始する。
- Empty ledgerからの開始はoperatorが明示した`--initialize-cutover`とcutover manifestを必須にする。

## 8. Adapter and Observation Contract

### 8.1 Source hierarchy

Adapterはそのrunの既存Core snapshot、detail、configおよび必要なread-only DB factを読む。古い固定fixtureを本番入力の代わりにしない。

Core objectのfield欠損を推測値で埋めない。field source、source date、producer/versionをprovenanceへ残す。

### 8.2 Full scope first

1. 現行の`select_scope(db.load_securities(), scope)`と同じauthorityでFull Screening Scopeを確定する。
2. Scope全codeについて`run_scope_members`を1行ずつ作る。
3. Candidate/detailがないcodeもstatus/reasonを残す。
4. `universe_count`や保存candidate数をscope denominatorとして流用しない。

Runtime Verificationの965→964差で特定した8303相当の履歴不足は、母集団から黙って除外せず`INSUFFICIENT_PRICE_HISTORY`として保持する。

### 8.3 Minimal observation fields

Accepted observationには少なくとも次を持たせる。

- observation UID、run UID、code、observed market date/session index。
- observation statusとstructured reason。
- Core observed state。
- close、observed primary Pivot。
- aligned trend strategy identifiers/count。
- breakout trend strategy identifiers/count。
- relevant candidate gate boolean。
- source snapshot/detail/config versionsまたはhash。
- canonical input hashとprovenance reference。

保存禁止:

- full ranking row。
- rank/score。
- full strategy condition list。
- chart price series。
- full trade plan。
- full financial statement/raw EDINET response。
- descriptionやUI文言の複製。

### 8.4 Permanent exit evidence

`CLOSED`に使用できるevidence typeをclosed enumにする。

- `DELISTED_CONFIRMED`
- `SECURITY_CODE_RETIRED`
- `CORPORATE_SUCCESSOR_CONFIRMED`
- 人間承認された`SCOPE_POLICY_TERMINAL_REMOVAL`

単なるscope落選、missing candidate、連続OUT_OF_SCOPE、価格取得失敗、長期gapは恒久証拠ではない。実sourceに構造化fieldがなければCLOSEDを出さず、そのrunの通常consumer eligibilityだけfalseにする。

### 8.5 Run quality

Run status、coverage status、publish eligibilityを別fieldで保存する。

- 965件すべてを処理し1件だけ履歴不足なら、run処理はCOMPLETEになり得るがcoverageはDEGRADED。
- stale/error理由が個別銘柄またはstageに紐づかず、件数だけの場合はPARTIAL/UNKNOWN。
- FAILED runはstate/event commitを行わない。
- publish eligibilityは将来consumer用であり、このGoalでは常に公開処理を呼ばない。

## 9. Persistence Contract

### 9.1 Schema ownership

Phase 2A table名には既存tableとの衝突を避ける一貫したprefixまたは明確なnamespaceを使用する。Migration versionを専用tableで管理し、同一versionの再適用はNO_OP、異なるschema hashは停止する。

`PRAGMA foreign_keys=ON`をconnectionごとに確認する。transaction mode、journal mode、synchronous modeをtest/benchmark resultへ記録する。

### 9.2 Required logical records

少なくとも次を物理schemaへ写像する。

- state machine run。
- run scope member/status。
- accepted minimal observation。
- identity decision。
- Core setup identity ledger。
- strategy membership history。
- Pivot revision history。
- append-only setup event。
- current setup state cache。
- rejection/quarantine record。
- replay lineage/namespace。
- migration/version metadata。
- seed manifest metadata。

Run-levelで共通するproducer/config/state-machine/threshold versionは正規化してよい。ただし監査可能性を落とさず、削減効果はfinal benchmarkでのみ評価する。

### 9.3 Constraints and indexes

最低限、次をDB constraintとtestの両方で守る。

- UID、run、observation、eventのprimary key/unique。
- Setup eventのidempotency key unique。
- 同一lineage/setup/dateのaccepted observation conflict検出。
- Current state `(state_lineage, core_setup_uid)` unique。
- Event/observation/decisionから存在しないrun/setupへのforeign key禁止。
- Price/Pivotはnullable statusを除きfinite positiveをapplication validation。
- terminal phaseのreopen禁止。
- eventはappend-only。訂正は`CORRECTION_RECORDED`または明示migration。
- Lookupに必要なcode、latest date、setup UID、producer run、event effective dateへ最小限のindex。

不要な重複indexを増やさない。index削減はquery planとbenchmarkを確認して行う。

### 9.4 Atomic run transaction

1 runについて、次を単一transactionで処理する。

1. run metadata staging。
2. full scope status。
3. accepted observations。
4. identity decisionsと必要なMINT。
5. membership/Pivot revision。
6. events。
7. current state optimistic update。
8. run count/quality invariant検証。
9. runをCOMMITTEDへ変更。

途中例外では全rowをrollbackする。COMMITTED runだけがseed export、shadow artifact、rebuild対象になれる。

### 9.5 Idempotency and concurrency

- Canonical input hashをstable JSON serializationから作る。
- 同じrun/date/input/versionの再実行はNO_OP。
- Same-day different input hashは既存stateを変更せずquarantine。
- Event retryでduplicate eventを作らない。
- MINT retryでduplicate UID/ledger rowを作らない。
- `state_version`を使ったoptimistic updateでlost updateを検出する。
- 2 connectionが同一seedから同時更新を試みるtestを持つ。
- Busy/lock retryは上限付きで、semantic conflictをlock retryで上書きしない。

## 10. Pivot, Membership, and Event Evidence

### 10.1 Pivot

- Observed primary Pivotとtracking Pivot revisionを別field/tableで保持する。
- MINT時にtracking Pivot revision 1を作る。
- Initial breakout前の安全なLINK中にvalid primary Pivotが変化した場合、`PIVOT_REVISED`をappendする。
- 当日評価は旧tracking Pivotで行い、revisionは評価後に次回用として適用する。
- Initial breakout後はtracking Pivotを凍結する。
- 株式分割等でscale continuityが壊れた場合、自動rebaseせずquarantine/migration requiredにする。

### 10.2 Membership

Strategy membershipはphaseとは別履歴で保持する。追加・削除・支持継続を記録でき、Connors exclusionを明示する。同日同membershipの重複保存は避ける。

### 10.3 Event evidence

各eventは少なくとも次を参照する。

- event UID/type/version。
- Core setup UID。
- from/to phase。
- effective/detected dateとdate status。
- source observationまたはstructured permanent-exit evidence。
- tracking Pivot revision。
- related prior breakout/failure/event。
- breakout/failure cycle ordinal。
- transition ID、reason code、evidence hash。
- producer run、state-machine/threshold version。

市場eventはCURRENT observationを必須にする。`SETUP_CLOSED`だけはCURRENT価格観測の代わりにstructured permanent-exit evidenceを根拠にできる。

## 11. Seed and Recovery Contract

### 11.1 Seed contents

Compact durable seedには、次回runのidentity continuityとstate再開に必要なものだけを含める。

- Identity ledgerとcanonical/full hash。
- Current stateまたはそれを再構築する最小anchor。
- Active tracking Pivot revision。
- Current strategy membership。
- Latest accepted observation/date/session/hash。
- Latest breakout/failure/event anchorsとcycle number。
- Terminal UID registry。
- State lineage、schema、producer、state-machine、threshold version。
- Seed manifest、row count、table hash、whole-file SHA-256。

日次full history、full candidate、chart、rankingをseedへ含めない。

### 11.2 Local bundle

実装は少なくとも次を出力できる。

- Compact seed SQLiteまたは同等のdeterministic data file。
- Canonical manifest JSON。
- SHA-256 sidecar。

Exportは一時pathへ完全生成・検証後にatomic renameする。Import前にfile hash、manifest/schema/version、row count、SQLite integrity、foreign keyを検査する。

### 11.3 Recovery

- Seed missing、hash mismatch、manifest mismatch、schema mismatchではrunを開始しない。
- Import後にcurrent stateをledger/event/observation anchorからrebuildし、保存state hashと一致させる。
- 破損seedから空ledgerへfallbackしない。
- Explicit cutover initializationとnormal recoveryを同じcommand flagにしない。
- Full history DBを毎run seedとして持ち回らない。

### 11.4 Remote authority remains deferred

GitHub Actions cacheは正本にしない。専用branch、verified release/artifact、Pages外のdurable store等のproduction authorityは、このGoal後のCutover／Deployment Goalで選択する。

Remote seed方式が未決定でもlocal implementationは完了可能だが、scheduled production runはREADYにしない。

## 12. Replay, Version, and Correction

- Out-of-order inputをlive lineageへ適用しない。
- Replayは別`state_lineage`/namespaceで先頭から決定論的に構築する。
- Replay結果をliveへ自動mergeしない。
- Same `sm1/smt1`で同inputならstate/event/evidence hashが一致する。
- State-machineまたはthreshold version変更は新lineageか明示migrationを要求する。
- 旧version stateをcurrent configで暗黙再評価しない。
- Correctionはappend-onlyで元recordを参照し、過去eventをUPDATE/DELETEしない。
- 誤ったCLOSEDを直接reopenしない。Human-approved migrationまたは新UIDを必要とする。

## 13. Shadow CLI and Private Artifact

### 13.1 Required CLI safety

Shadow CLIは少なくとも次を明示指定可能にする。

- input artifact/root。
- output SQLite path。
- output artifact directory。
- run/effective date。
- state-machine/threshold version。
- verified seed inputまたはexplicit cutover initialization。
- dry-run/validate-only。

Safety requirements:

- Default pathで`data/momentum.db`を開かない。
- Default pathで`public/`配下へ書かない。
- Network callをしない。
- Production credentialを要求しない。
- Overwriteは同hash idempotent case以外で明示的に拒否する。
- Dry-runはDB/artifactを変更しない。

### 13.2 Private artifact schemas

`schemas/phase2a_*.schema.json`として、少なくとも次を定義する。

- run summary。
- latest setup state。
- recent event。
- rejection/quarantine summary。
- seed manifest。

Artifactはcompactで、将来consumerがinternal DBを直接読む必要をなくす。ただしこのGoalでは`artifacts/phase2a-shadow/`等の非公開pathにのみ出す。

### 13.3 Artifact invariants

- JSON schema validation pass。
- Current in-scope/nonterminal filter用fieldを持つ。
- CLOSED eventは一度だけ識別可能。
- OUT_OF_SCOPE/SUSPENDED_GAPをactive listへ混ぜない。
- Raw error stack、local absolute path、token、credentialを含めない。
- 30 market days相当のevent windowを生成可能。

## 14. Test Requirements

### 14.1 Pure domain tests

- `transition-fixtures.json`全scenario。
- T01–T26 coverage、transition ID重複なし。
- Section 6.4の全境界。
- null、0、負数、NaN/Infinity相当のfail closed。
- terminal invariant。
- breakout/failure/retry/rebreakout multi-cycle。
- cross-before-expiry。
- Pivot freezeとrevision timing。
- CLOSED structured evidence requirement。

### 14.2 Identity tests

- UID canonicalizationとknown vectors。
- Same canonical inputのsame UID。
- Full hash collision guard。
- Single safe candidate LINK。
- 5/6 session boundary。
- Multiple candidate AMBIGUOUS。
- Candidate gate MINT/NO_SETUP。
- Legacy 964をretroactive LINKしないfixture。
- Cutover BREAKOUT bootstrapにBREAKOUT eventがない。

### 14.3 Persistence tests

- Fresh migration、再migration NO_OP、schema hash mismatch stop。
- Foreign key/integrity pass。
- 全insert stage直後のfailure injectionとorphan 0。
- Retry後COMMIT 1回、同一repeat NO_OP。
- Event/mint duplicate 0。
- Same-day different hash quarantine。
- Optimistic state conflict。
- Append-only event/correction。
- Current state rebuild hash一致。

### 14.4 Adapter and run tests

- 965 scope membersを先に作り、candidate 964でも965 status rowsを保持。
- 960 current + 4 stale + 1 insufficient等の固定distribution。
- stale 4件のphase/event不変。
- 一時OUT_OF_SCOPEでphaseはterminalにならない。
- Structured delistingでCLOSED + SETUP_CLOSED 1件。
- Core BREAKOUTが5日継続してもeventは1件。
- Pivot evidenceのlook-ahead禁止。
- Run status、coverage、publish eligibilityの独立性。
- Structured errorがcode/stageへ紐づかない場合PARTIAL/UNKNOWN。

### 14.5 Seed and replay tests

- Seed export/import roundtrip。
- Missing、truncated、hash mismatch、schema mismatch、version mismatchでMINT 0。
- Rebuild state hash一致。
- Replay namespaceがlive不変。
- Same seed/input/versionで全hash一致。
- Old versionへのnew evaluator適用拒否。

### 14.6 Product regression tests

既存Core、database、export、workflow contract、Validation、Research、Morning Brief testを実行する。Phase 2A未接続のため、既存公開snapshot/detailのschemaと意味が変わってはならない。

大量の既存生成artifact差分があるため、単純なworktree全体diffではなく、Phase 2A実装開始前baselineとの対象file比較も行う。

## 15. Final Physical Schema Benchmark

### 15.1 Required workload

`scripts/benchmark_phase2a_storage.py`をfinal repository/storage implementationへ接続または同等のproduction-schema benchmarkへ置き換える。

- 約1,000 scope rows/run。
- 995 CURRENT、4 STALE、1 INSUFFICIENT。
- 10 transactions/scenario。
- DELETE/WAL。
- event率0/10/25%。
- Pivot revision率0/15/50%。
- failure injection、retry、NO_OP、seed roundtrip、rebuild。

### 15.2 Required outputs

- 全scenarioのp50/p95。
- Seeded DB、10-run incremental、annualized 965×250run。
- WAL/SHM peak。
- Table/index attribution。
- Integrity/foreign key errors。
- Public-equivalent compact artifact raw/gzip size。
- Seed export/hash/import/integrity/rebuild timing。
- Prototype benchmarkとの差分理由。

### 15.3 Capacity gate

- 435–540MB/年内: planning envelope PASS。
- 540MB/年超: 実装を隠してacceptせず、schema/retention reviewへ戻す。
- 435MB/年未満: PASS候補だが、監査fieldやindexを落としていないことを確認する。
- Compressionやarchive削減は実測なしにcapacityへ算入しない。

12か月hot retentionの削除処理自体はこのGoalで実行しない。将来archiveへ必要なrun/date index、manifest、read contractだけを用意する。

## 16. Required Verification Commands and Evidence

Completion Reportには、環境に合わせて実際に実行したcommand、exit code、件数を記載する。最低限のevidenceは次。

1. New pure/fixture test result。
2. Persistence/failure injection test result。
3. Adapter/run quality test result。
4. Seed/replay/version test result。
5. Existing product regression result。
6. JSON schema validation result。
7. Final physical benchmark result。
8. Temp DB `integrity_check`と`foreign_key_check`。
9. Same-run retryのNO_OP、duplicate mint/event 0。
10. Missing/corrupt seed時のmint 0。
11. Product DBを開いていない証拠。
12. `public/dashboard`とworkflowへPhase 2Aを接続していないdiff確認。

Testが環境依存で実行不能な場合、未実行をPASSと書かず、command、阻害要因、代替検証、残リスクを記録する。

## 17. Acceptance Criteria

次をすべて満たしたときだけImplementationをCOMPLETEとする。

1. 承認済み7phaseとevent-centered modelが実装されている。
2. T01–T26とfixed fixtureが全件passする。
3. `smt1`の3%/3%/2 strategies/90/20/5 sessionsが変更されていない。
4. Cross-before-expiryが境界testで証明されている。
5. CLOSEDはstructured permanent-exit evidenceだけで発生する。
6. 旧964 setupへのretroactive LINKがない。
7. Cutover BREAKOUTに架空のBREAKOUT event/dateがない。
8. Full scopeがcandidateより先に確定し、全scope memberにstatusがある。
9. Stale/missing/error/out-of-scope/ambiguousでphase/eventを進めない。
10. Minimal observationにfull candidate/chart/rank/plan/財務原票がない。
11. Identity/observation/decision/Pivot/event/stateが原子的に保存される。
12. Retryと同一再実行でduplicate UID/eventがない。
13. Same-day conflictとout-of-order inputがlive stateを変更しない。
14. Seed missing/corrupt時に全件MINT fallbackが起きない。
15. Seed roundtripとcurrent state rebuild hashが一致する。
16. Final physical schema benchmarkがcapacity gateを通る。
17. Existing product regressionがpassする、または開始前からのfailureを証拠付きで分離できる。
18. Existing ranking/Validation/Experimental/Research/Morning Brief output contractを変更していない。
19. Shadow artifactが公開pathにない。
20. Production DB、workflow、GitHub Pages、remote serviceを変更していない。
21. Implementation Resultと次段階のHuman Review checklistが作成されている。

## 18. Explicitly Forbidden

- Design/fixtureをtestに合わせて書き換える。
- 旧964件への自動LINK/backfill。
- Codeだけ、state一致だけ、近いPivotだけによる推測LINK。
- Candidate rowsだけをFull Screening Scopeとして扱う。
- Stale/missing/errorをFAILEDやEXPIREDに変える。
- OUT_OF_SCOPEの日数だけでCLOSEDにする。
- 期限判定をqualified crossより先にする。
- Gap >5からBREAKOUT/REBREAKOUTを推定する。
- Pre-breakout未確定のsetupをFAILEDにする。
- Post-breakout tracking Pivotを当日primary Pivotに追随させる。
- Core BREAKOUT bootstrapから過去event/dateを捏造する。
- Current configで過去event/stateを暗黙再計算する。
- Transaction外でidentity、state、eventを別々に確定する。
- Seed異常時に空ledgerや全件MINTへfallbackする。
- GitHub Actions cacheだけをstate正本にする。
- Full history DBを毎run seedとして配布する。
- Phase 2Aをranking score、UI、Morning Briefへ自動接続する。
- 通常app/export起動でPhase 2A migrationを暗黙実行する。
- 本番`data/momentum.db`、workflow、公開artifactをこのGoalで更新する。
- 既存dirty changeをreset、checkout、削除、まとめてcommitする。

## 19. Stopping Conditions

次のいずれかが起きたら、安全な局所診断まで行って実装を停止し、Human Reviewを求める。

1. Stable Identity DesignとPhase 2A Designが実装不能な形で矛盾する。
2. T01–T26の同一inputに複数primary resultが残る。
3. Existing Core artifactからFull Scope、market session index、前回accepted closeを安全に得られない。
4. Permanent-exit evidenceの実fieldが存在せず、CLOSEDを自動判定する必要があると判断された。
5. Product DBを変更しないとtest/shadowを実行できない。
6. Seed破損時の全件MINTを防げない。
7. Atomic failure testでorphan、partial state、duplicate eventが残る。
8. Final schemaが540MB/年を超える。
9. Phase 2A未接続にもかかわらず既存ranking/public contractが変わる。
10. Dirty worktreeの既存変更と衝突し、ユーザー資産を安全に保持できない。

Structured permanent-exit sourceが未整備の場合は、CLOSED detectionをdisabled/fail closedにしたまま他のImplementationを完了してよい。ただしAcceptanceの自動CLOSED testはsynthetic structured evidenceで通し、実運用CLOSEDはNOT READYと明記する。

Remote durable seed authorityが未決定であることはlocal implementationの停止条件ではないが、scheduled production cutoverの明示的blockerである。

## 20. Deliverables

### 20.1 Code

- `engine/state_machine/` package。
- Versioned additive schema/migration。
- Explicit shadow service/CLI。
- Local seed export/import/verify/rebuild。
- Private artifact serializers and JSON schemas。

### 20.2 Tests and fixtures

- Pure transition tests。
- Identity tests。
- Persistence/failure injection/concurrency tests。
- Adapter/run tests。
- Seed/replay/version tests。
- Product regression evidence。

### 20.3 Reports

- `docs/PHASE2A_IMPLEMENTATION_RESULT.md`。
- Final physical schema benchmark Markdown。
- Machine-readable benchmark JSON。
- Shadow readiness report。
- Seed recovery runbook。
- Phase 2A固有changed-file list。

既存の設計文書とfixtureはreferenceとして保持し、Implementation都合で履歴を上書きしない。訂正が必要なら別のdesign amendmentを作る。

## 21. Completion Report Template

`docs/PHASE2A_IMPLEMENTATION_RESULT.md`には次を含める。

1. Outcome: COMPLETE / PARTIAL / BLOCKED。
2. Start/end HEAD、dirty baseline、Phase 2A changed paths。
3. Implemented moduleと責務。
4. Design/fixture traceability matrix。
5. Schema/version/table/index一覧。
6. Scope/current/stale/insufficient/error/decision/event count。
7. T01–T26 coverageと境界test結果。
8. Atomicity/idempotency/concurrency結果。
9. Seed export/import/corruption/rebuild結果。
10. Final physical benchmarkと473MB baseline比較。
11. Existing product regression結果。
12. Product DB/workflow/public consumer未接続の確認。
13. Known limitationsとNOT READY項目。
14. Cutover前Human Review checklist。
15. 次に実行すべきGoal。

## 22. Human Review Gate After Implementation

実装後、次を人間が確認するまでworkflowや公開へ進まない。

1. T01–T26が設計通りである。
2. Cross-before-expiryとCLOSED evidence条件が守られている。
3. 965 scopeとcandidate数を混同していない。
4. Minimal observationが過剰保存になっていない。
5. Seedがidentity continuityに十分で、full history DBではない。
6. Seed破損・同日conflict・out-of-orderがfail closedする。
7. Final capacityが許容範囲である。
8. Existing product outputが不変である。
9. Shadow artifactの内容とsizeが妥当である。
10. Structured permanent-exit sourceの実運用availability。
11. GitHub Actions上のdurable seed authority候補。
12. Cutover日、rollback point、operator手順。

## 23. Required Next Steps After This Goal Is Executed

このImplementation Goalが完了しても、自動的に本番運用へ移行しない。次は以下の順序とする。

1. Implementation Resultとfinal benchmarkをHuman Reviewする。
2. 保存済みCore inputを使った複数日のlocal shadow runを実行し、event/identity/expiry差分を確認する。
3. `smt1` sensitivityは値を変えず、観測結果だけを報告する。
4. GitHub Actionsで使うdurable seed正本の方式を決定する。
5. **Phase 2A Cutover / Deployment Goal**を別途作成する。
6. そのGoalでworkflow、seed取得/保存、rollback、初回cutover、private/public artifactを接続する。
7. 複数のscheduled shadow runとHuman approval後に、必要ならMorning Brief/Research/UI consumer Goalを別途作成する。

推奨する次のGoal名は、`Japan Swing Lens — Phase 2A Cutover and Durable Seed Deployment Goal`。

## 24. Final Authorization Boundary

この文書はPhase 2A Implementationの作業仕様である。

このGoalを実行する指示があった場合、ローカル実装、隔離SQLite migration、test、benchmark、private shadow artifact生成までは許可される。

次は許可されない。

- git commit / push。
- GitHub Actions workflowの稼働変更。
- GitHub Pages公開。
- Production DB migration。
- Remote durable seed作成・更新。
- Existing consumerへの接続。

それらはImplementation ResultのHuman Review後、別の明示的なCutover／Deployment指示を必要とする。
