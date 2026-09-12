# Japan Swing Lens — Phase 2A State Machine Design

設計日: 2026-09-12 JST
状態: **DESIGN APPROVED / LIMITED RUNTIME BENCHMARK COMPLETE / IMPLEMENTATION NOT STARTED**
対象: cutover後に発行する`core_setup_uid`。旧964件への遡及適用は行わない。

## 1. Decision Summary

Phase 2Aは**event-centered model**を採用する。

- 永続phaseは`FORMING / WATCH / POST_BREAKOUT / FAILED / RETRY_WATCH / EXPIRED / CLOSED`の7つ。
- `BREAKOUT`と`REBREAKOUT`は一時的なphaseではなくappend-only event。
- 現行Coreの日次state、観測品質、identity decision、永続phase、eventは別フィールド・別契約にする。
- State Machineの主語は銘柄コードではなく`core_setup_uid`。
- `tracking_pivot`はsetupが追跡する基準、`observed_primary_pivot`は当日のCore表示値として分ける。
- 初回または再突破は、同じtracking Pivotに対する価格crossと2手法以上の突破を同時に満たす場合だけ確定する。
- FAILEDは、同じUIDで確定済みBREAKOUT/REBREAKOUTがあり、終値がtracking Pivotを3%より大きく下回ったときだけ確定する。
- FAILED後、Pivot以下3%以内へ回復し、2手法以上が同じsetup候補を支持すれば`RETRY_WATCH`。
- 同じUIDで再び価格crossと2手法以上の突破を満たせば`REBREAKOUT_CONFIRMED`。
- 形成中は90市場session、突破後は最新BREAKOUT/REBREAKOUTから20市場sessionを暫定有効期間とする。等号日は有効。最初の超過CURRENT観測ではqualified crossを先に評価し、crossがなければ失効する。
- stale、欠測、fetch失敗、一時的scope離脱、identity ambiguityだけではphaseを変更しない。
- 上場廃止など構造化された恒久離脱の証拠がある場合だけ`CLOSED`へ終端させる。日数経過だけで恒久離脱とは推定しない。
- 通常runでは1銘柄1つの自動解決可能なactive setupに限定する。schemaは複数setupを表現できるが、候補を一意に選べなければ`AMBIGUOUS`にする。
- full candidate/detail/chart/財務原票は保存せず、状態判定に必要な最小事実だけを保存する。

すべての新閾値は`state_machine_threshold_version = smt1`で固定し、設計後benchmark・shadow runで検証する。値を変える場合は新versionを作り、既存履歴へ暗黙に遡及しない。

## 2. Fixed Boundaries

### 2.1 Identity contract

Setup Identity Designで確定した次を変更しない。

1. 主キーは`core_setup_uid`。
2. formatは`csu1:<4桁code>:<SHA-256先頭160bit>`。
3. UIDはimmutable birth requestから一度だけ発行する。
4. 正本はdurable ledger。
5. Pivot、phase、手法集合、各versionはUID材料ではない。
6. Pivot変更はrevision、手法変更はmembership履歴。
7. observation、event、setupのIDは別。
8. decisionは`LINK / MINT / AMBIGUOUS / NO_SETUP`。
9. CoreとExperimentalは別namespace。
10. 旧964件は全件`AMBIGUOUS`で、遡及LINKしない。
11. lineage保証はcutover後だけ。
12. Actions cacheを正本にしない。

### 2.2 Existing Core is not changed

Phase 2Aは既存Core判定を入力として読む。6手法、Consensus、rank、trade plan、Experimental、Research、Validationの算出内容は変えない。

| Existing fact | Current meaning | Phase 2A treatment |
|---|---|---|
| Core `BREAKOUT` | 5トレンド手法中、最低2手法がrecent breakout | observed fact。永続phaseにはしない |
| Core `BREAKOUT WATCH` | breakout/watchとconfluenceの組合せ | observed fact。`WATCH` entry候補 |
| Core `FAILED` | 5トレンド手法中、最低2手法が各自のPivotでFAILED | observed factのみ。Phase 2A FAILEDの直接トリガーにしない |
| Validation `failed_breakout` | signal側Pivotに対する追跡判定 | legacy validation fact。Phase 2Aの正本ではない |
| Core `PULLBACK` | Connors RSI2の別戦略 | trend setupのMINT/phase条件に使わない |
| Core `EXTENDED` | 複数手法がPivot上8%超 | observed fact/表示用。独立phaseにはしない |

## 3. Model Selection

| Axis | Phase-heavy model | Event-centered model |
|---|---|---|
| BREAKOUTを何日保持するか | 1日かCoreの5日かが曖昧 | eventは1回、phaseはPOST_BREAKOUTで明確 |
| 重複防止 | 同state再観測のdedupeが複雑 | observation + rule versionでeventを一意化 |
| Morning Brief | 「状態」と「今日変わったこと」が混ざる | phaseとWhat Changedを直接分けられる |
| FAILED履歴 | 再突破で上書きされやすい | event列として保持 |
| 欠測/replay | 一時stateの補完が必要 | 有効観測がなければno-op |
| 保存量 | phase snapshotは小さい | event分だけ増えるが最小factで許容 |
| 実装複雑度 | 一見小さいが期間意味が複雑 | event tableが必要だが規則は単純 |

採用理由は、日次Coreの`BREAKOUT`が最大5session残る一方、投資家が知りたいのは「今日初めて突破したか」「再突破か」だからである。

## 4. Separate Concepts

| Concept | Enum / ID | Authority |
|---|---|---|
| Run processing status | `STARTED / COMPLETE / PARTIAL / FAILED` | run manifest |
| Observation status | Section 8.2 | scope member row |
| State-stage blocker | `NONE / IDENTITY_AMBIGUOUS / LEDGER_UNAVAILABLE / VERSION_MISMATCH / INPUT_CONFLICT` | decision/transition stage |
| Core observed state | 現行Core enum | existing analysis output |
| Identity decision | `LINK / MINT / AMBIGUOUS / NO_SETUP` | Phase 2A resolver + identity ledger |
| Persistent setup phase | 6 phase enum | current setup state |
| Transition/market event | Section 5.2 | append-only event ledger |
| Continuity status | `CONTIGUOUS / PAUSED / SUSPENDED_GAP / AMBIGUOUS` | current setup state |

`continuity_status`はphaseではない。たとえば欠測時は`current_phase=WATCH`のまま`continuity_status=PAUSED`になり、FAILEDやEXPIREDを推定しない。

## 5. State and Event Contract

### 5.1 Persistent phases

| Enum | 日本語表示 | Invariant / entry | Exit | Terminal |
|---|---|---|---|---|
| `FORMING` | 形成中 | 初回突破eventなし。MINT時にcandidate gate成立、またはWATCHから接近条件を失った | WATCH、POST_BREAKOUT、EXPIRED | No |
| `WATCH` | 突破待ち | 初回突破eventなし。Core observed stateが`BREAKOUT WATCH` | FORMING、POST_BREAKOUT、EXPIRED | No |
| `POST_BREAKOUT` | 突破後を観測中 | 初回/再突破eventが最低1件、またはcutoverのBOOTSTRAP breakout anchorがある | FAILED、EXPIRED | No |
| `FAILED` | 突破後に基準割れ | 突破eventあり、終値がtracking Pivotの97%未満。未確認回復もここに保持 | RETRY_WATCH、POST_BREAKOUT、EXPIRED | No |
| `RETRY_WATCH` | 再突破待ち | BREAKOUT後にFAILEDがあり、終値がPivot以下かつ3%以内、aligned trend strategiesが2以上 | FAILED、POST_BREAKOUT、EXPIRED | No |
| `EXPIRED` | 追跡終了 | 有効期間超過をCURRENT観測で確認 | 同じUIDでは遷移しない。新候補は新UIDをMINT | Yes |
| `CLOSED` | 恒久終了 | 上場廃止、security code廃止、明示的terminal scope migration等の構造化証拠を確認 | 同じUIDでは遷移しない。訂正はappend-only correction | Yes |

`CLOSED`は構造化された恒久離脱証拠がある場合、全nonterminal phaseから入れる管理上の終端である。通常の価格guard表では省略しているが、評価優先順位は最上位とする。

表示では必ず「複数日追跡」と明示し、Core observed stateの日本語表示と隣接させても同じラベルにしない。

### 5.2 Events

| Event | Meaning |
|---|---|
| `SETUP_MINTED` | 新UIDと初期phaseを原子的に発行 |
| `BOOTSTRAP_OBSERVED` | cutover時点の観測だけから初期phaseを置いた。過去継続は主張しない |
| `WATCH_ENTERED` | FORMINGからWATCHへ初めて入った |
| `BREAKOUT_CONFIRMED` | 同一UIDの初回qualified cross |
| `FAILED_CONFIRMED` | 確定突破後の初回3%超Pivot breach。各failure cycleで1件 |
| `RETRY_WATCH_ENTERED` | failure cycleで初めて再突破待ちへ回復 |
| `REBREAKOUT_CONFIRMED` | 同一UIDの2回目以降のqualified cross |
| `PIVOT_REVISED` | 突破前にtracking Pivot revisionを切替 |
| `SETUP_EXPIRED` | 有効期間超過をCURRENT観測で確定 |
| `SETUP_CLOSED` | 恒久離脱の構造化証拠に基づきsetupを終端 |
| `CONTINUITY_RESUMED` | PAUSEDから安全に継続。phase transitionではないaudit event |
| `CORRECTION_RECORDED` | 過去eventを上書きせず訂正関係を追記 |

同じphaseに留まる通常観測はeventを発行しない。Core `EXTENDED`やConnors `PULLBACK`はaccepted observationに残せるが、上記eventへ自動変換しない。

## 6. Input Facts and Guards

### 6.1 Candidate gate

Core trend setup候補は次をすべて満たす。

```text
core_observed_state in {SETUP FORMING, BREAKOUT WATCH, BREAKOUT}
and aligned_trend_strategy_count >= 2
and observed_primary_pivot is finite and > 0
and close is finite and > 0
and observation_status == CURRENT
```

5トレンド手法はMinervini、Qullamaggie、CAN SLIM、Weinstein、Darvas。Connorsは数えない。候補gate不成立は既存UIDを失敗扱いにする証拠ではない。

### 6.2 Guard definitions (`smt1`)

```text
qualified_cross:
  previous_accepted_close <= tracking_pivot < current_close
  and breakout_trend_strategy_count >= 2

failed_breach:
  a prior BREAKOUT_CONFIRMED, REBREAKOUT_CONFIRMED,
    or cutover BOOTSTRAP breakout anchor exists
  and current_close < tracking_pivot * 0.97

retry_zone:
  a FAILED_CONFIRMED exists after the latest breakout event
  and 0 <= ((tracking_pivot - current_close) / tracking_pivot) * 100 <= 3
  and aligned_trend_strategy_count >= 2

pre_breakout_expired:
  market_session_index - minted_market_session_index > 90

post_breakout_expired:
  market_session_index - latest_breakout_market_session_index > 20
```

`previous_accepted_close`は同じUIDの前回CURRENT観測であり、単なる前日ではない。ただしgapがSection 9の自動LINK上限を超える場合、crossを推定せず`AMBIGUOUS`にする。

価格/Pivotがnull、非有限、0以下ならguardはfalseではなく`INVALID_TRANSITION_INPUT`としてC段階へ渡さない。JSONではNaNを許可しない。

### 6.3 Evaluation precedence

CURRENTかつLINK/MINTされた観測では、次の順序を固定する。

1. schema、finite price、version、seed、日付順を検証。失敗はreject/no transition。
2. 既存phaseがEXPIRED/CLOSEDなら同UIDへの全遷移をreject。
3. `PERMANENT_SCOPE_EXIT_CONFIRMED`があればCLOSED。市場状態guardより先に終端する。
4. CURRENTで安全にLINKでき、from phaseがFORMING/WATCH/FAILED/RETRY_WATCHなら`qualified_cross`を評価。過去breakoutが0件ならBREAKOUT、1件以上ならREBREAKOUT。
5. crossがなく、有効期間を**超過**していればSETUP_EXPIRED。これにより最初の超過CURRENT観測で成立した本物のcrossを消さない。
6. 有効期間内のPOST_BREAKOUTでは`failed_breach`を評価。
7. 有効期間内のFAILED/RETRY_WATCHでは`retry_zone`を評価。
8. 突破前phaseではCore WATCH/FORMING mappingを評価。
9. 許可された場合だけ、当日observed primary Pivotを次観測用tracking revisionにする。
10. 該当なしはphase unchanged。reason付きobservationだけ保存する。

突破当日は、突破前から有効だったtracking Pivotでcrossを判定し、そのPivotを突破後の固定基準にする。観測日の新Pivotで同日のgoalpostを動かさない。

cross優先は期限を無制限に延長する規則ではない。連続runならcrossがない最初の超過CURRENT観測でEXPIREDになる。gapが5sessionを超える観測はidentityがAMBIGUOUSになるためcross優先を使えない。gapが1–5sessionなら、eventの`detected_at`は復帰run、`effective_date_status=INTERVAL_CENSORED`として実際のcross日を断定しない。

## 7. Transition Matrix

`KEEP`はphase不変、`REJECT`は状態入力に採用せず監査reasonを残す。

| ID | From | Guard / event | To | Emitted event | Reason |
|---|---|---|---|---|---|
| T01 | none | MINT + candidate FORMING | FORMING | SETUP_MINTED | `NEW_CORE_SETUP` |
| T02 | none | MINT + Core WATCH | WATCH | SETUP_MINTED | `NEW_CORE_SETUP_WATCH` |
| T03 | none | MINT + qualified cross proven | POST_BREAKOUT | SETUP_MINTED, BREAKOUT_CONFIRMED | `NEW_SETUP_INITIAL_BREAKOUT` |
| T04 | none | CUTOVER + Core BREAKOUT、cross history不明 | POST_BREAKOUT | SETUP_MINTED, BOOTSTRAP_OBSERVED | `CUTOVER_POST_BREAKOUT_UNPROVEN` |
| T05 | FORMING | Core WATCH、not expired | WATCH | WATCH_ENTERED | `CORE_WATCH_OBSERVED` |
| T06 | FORMING/WATCH | qualified cross | POST_BREAKOUT | BREAKOUT_CONFIRMED | `INITIAL_QUALIFIED_CROSS` |
| T07 | WATCH | candidate gate成立、Core FORMING | FORMING | none | `MOVED_AWAY_FROM_WATCH` |
| T08 | FORMING/WATCH | age > 90 | EXPIRED | SETUP_EXPIRED | `PRE_BREAKOUT_SESSION_LIMIT` |
| T09 | POST_BREAKOUT | failed breach | FAILED | FAILED_CONFIRMED | `TRACKING_PIVOT_BREACH_GT_3PCT` |
| T10 | POST_BREAKOUT | neither failure nor expiry | KEEP | none | `POST_BREAKOUT_CONTINUES` |
| T11 | FAILED | retry zone | RETRY_WATCH | RETRY_WATCH_ENTERED | `RECOVERED_TO_RETRY_ZONE` |
| T12 | FAILED | qualified cross | POST_BREAKOUT | REBREAKOUT_CONFIRMED | `DIRECT_QUALIFIED_REBREAKOUT` |
| T13 | RETRY_WATCH | qualified cross | POST_BREAKOUT | REBREAKOUT_CONFIRMED | `QUALIFIED_REBREAKOUT` |
| T14 | RETRY_WATCH | failed breach | FAILED | none | `RETRY_LOST_BELOW_FAILURE_LINE` |
| T15 | RETRY_WATCH | retry zone no longer true、no cross | FAILED | none | `RETRY_CONDITION_LOST` |
| T16 | POST_BREAKOUT/FAILED/RETRY_WATCH | age > 20 from latest breakout | EXPIRED | SETUP_EXPIRED | `POST_BREAKOUT_SESSION_LIMIT` |
| T17 | any nonterminal | valid CURRENT, no rule matches | KEEP | none | `NO_PHASE_CHANGE` |
| T18 | EXPIRED/CLOSED | any input for same UID | REJECT | none | `TERMINAL_SETUP_UID` |
| T19 | any | stale/missing/error/out-of-scope | KEEP | none | observation status reason |
| T20 | any | out-of-order live input | REJECT | none | `OUT_OF_ORDER_OBSERVATION` |
| T21 | any | same-day exact replay | KEEP | none | `IDEMPOTENT_REPLAY` |
| T22 | any | same-day different input hash | REJECT | none | `SAME_DATE_INPUT_CONFLICT` |
| T23 | FORMING/WATCH | safe LINK + valid changed Pivot | KEEP/current mapping | PIVOT_REVISED | `PRE_BREAKOUT_PIVOT_REVISION` |
| T24 | POST_BREAKOUT/FAILED/RETRY_WATCH | observed primary Pivot changes | KEEP | none | `TRACKING_PIVOT_FROZEN_AFTER_BREAKOUT` |
| T25 | any | state-machine version differs without migration | REJECT | none | `STATE_MACHINE_VERSION_MISMATCH` |
| T26 | any nonterminal | structured permanent scope exit evidence | CLOSED | SETUP_CLOSED | `PERMANENT_SCOPE_EXIT_CONFIRMED` |

### 7.1 Forbidden transitions

- FORMING/WATCHからFAILED: prior breakoutがないため禁止。
- POST_BREAKOUTからFORMING/WATCH: 同じUIDでは突破前へ巻き戻さない。
- FAILED/RETRY_WATCHから初回BREAKOUT: 必ずREBREAKOUT event。
- EXPIRED/CLOSEDからactive phase: 同じUIDでは禁止。正当な新候補は新UIDをMINTする。
- stale/error/NO_SETUP/AMBIGUOUSを根拠にFAILED/EXPIRED: 禁止。
- post-breakout tracking Pivotの自動差替え: 禁止。
- OUT_OF_SCOPEの継続日数だけを根拠にCLOSED: 禁止。

禁止入力は`transition_rejections`監査行またはobservation reasonとして保存する。current stateは更新しない。

## 8. Run and Observation Quality

### 8.1 Run status

- `COMPLETE`: scope全件にterminal observation statusがあり、件数invariant、seed、transactionが正常。`INSUFFICIENT_PRICE_HISTORY`など説明済みの対象外を含んでも処理はCOMPLETEになり得る。
- `PARTIAL`: scope manifestは有効でcommitできるが、fetch/analyze/stale原因不明/identity ambiguous/構造化されていないupstream errorのため1件以上を遷移評価できない。
- `FAILED`: scope/seed/schemaが無効、transaction失敗、または結果を安全にcommitできない。FAILED runはprevious accepted observationにならない。

処理完了度とcoverageを分け、`coverage_status = FULL / DEGRADED / UNKNOWN`も保存する。965件中8303だけが履歴不足なら処理はCOMPLETE、coverageはDEGRADED。2026-09-10の4件のようにstale理由が特定できず、snapshot errorも件数だけならPARTIAL/UNKNOWNとする。

`publish_eligibility = ELIGIBLE / ELIGIBLE_DEGRADED / BLOCKED`も別フィールドにする。Phase 2A runがFAILEDなら新state artifactはBLOCKED、PARTIALなら明示的なquality付きでELIGIBLE_DEGRADED、COMPLETEならELIGIBLE。これは既存Core dashboardの公開可否を支配しない。Phase 2Aの公開が失敗してもCOMMITTED ledgerはrollbackしない。

### 8.2 Observation status

| Status | Transition eligible | Meaning |
|---|---:|---|
| `CURRENT` | Yes | analysis dateがexpected market dateと一致し入力妥当 |
| `NO_NEW_MARKET_OBSERVATION` | No | 休場・無取引を根拠付きで確認 |
| `STALE_MARKET_DATE` | No | 最新価格日がexpectedより古い |
| `INSUFFICIENT_PRICE_HISTORY` | No | 200本など現行最低本数未達 |
| `FETCH_FAILED` | No | price/fundamental取得失敗 |
| `ANALYSIS_FAILED` | No | Core分析失敗 |
| `OUT_OF_SCOPE` | No | 前run setupの銘柄が今回scope外。別監査行 |
| `INVALID_INPUT` | No | null/非有限/非正価格、schema違反 |

`scope_total`はCURRENTだけの件数ではない。run開始時の965件すべてにscope member行を1件ずつ作る。

`IDENTITY_AMBIGUOUS`と`LEDGER_UNAVAILABLE`は市場観測の品質ではないため、`observation_status`へ上書きしない。前者はCURRENT観測に付くidentity decision/blocker、後者はrun/state stage blockerとして別保存する。これにより「価格は正常に取得できたがidentityだけ判断不能」を区別できる。

## 9. Identity Resolution

### 9.1 Stable decision slots and ordering

現行Coreは銘柄ごとに1つのprimary candidateしか安定して提示しないため、MVPの自動slotは`core-primary`のみとする。入力配列順は使わない。将来複数slotを導入する場合は次でstable sortする。

```text
(namespace, code, structure_reference_date, pivot_type,
 normalized_pivot_price, sorted_supporting_strategy_codes)
```

同じsort keyが衝突すればsuffix採番をせず`AMBIGUOUS_SLOT_COLLISION`。価格順だけ、当日のrank、辞書のiteration順はorigin_slotにしない。

### 9.2 Resolver rules

同じcode以外のUIDは候補にしない。判定順は次のとおり。

1. observationがCURRENTでなければidentity decisionを作らず、品質reasonだけ保存する。
2. ledger不正ならB段階を開始せず`LEDGER_UNAVAILABLE` blocker。UIDはnull、遷移なし。
3. explicit durable slot mappingが1件: code、namespace、versionを検証し`LINK`。
4. explicit mappingなしで、同じcodeのnonterminal Core setupが1件、gapが5市場session以下: `LINK`。candidate gate不成立でも既存setup監視のためLINKできる。
5. nonterminal候補が複数で、explicit mappingがない: `AMBIGUOUS`。
6. nonterminal候補が0件、candidate gate成立: `MINT`。
7. nonterminal候補が0件、candidate gate不成立: `NO_SETUP`。
8. 最終accepted observationから6市場session以上のgap、scope復帰、またはlineage seedの空白がある: 自動LINK/MINTせず`AMBIGUOUS`。

5sessionは1取引週を意図した**暫定の連続性上限**。通常runの週末・祝日は市場session indexを増やさない。gap中に別setupが形成された可能性を排除できないため、6session以上を価格類似だけでLINKしない。

### 9.3 Multiple setups

schema、UID、membershipは同一codeに複数setupを許す。しかし現行`core-primary`入力だけではどのsetupか証明できないため、複数nonterminal候補時はexplicit durable slot mappingがない限りAMBIGUOUSとする。安い方・近いPivot・新しい方を勝手に選ばない。

### 9.4 MINT idempotency and correction

- mint requestは`observation_uid + decision_slot + identity_epoch + origin_slot + identity_version`で固定する。
- 同一requestは同じUIDを返し、並行insertはunique constraintで1件だけ成功させる。
- cutoverは`identity_epoch=cutover-<date>`、reason=`CUTOVER_BOOTSTRAP`。
- 後からambiguityが解決しても過去行を上書きしない。`IDENTITY_DECISION_CORRECTED`をappendし、superseded decisionを参照する。
- 過去eventの自動生成は別replay namespaceで人間承認後に行う。live ledgerへ無言でbackfillしない。

### 9.5 Strategy membership

- CURRENT観測の5トレンド手法だけをmembership候補にする。Connorsは別namespace/strategyでCore trend membershipへ入れない。
- stableな`strategy_setup_uid`を一意にLINK/MINTできた手法だけ、Core UIDとの期間付きmembershipを開始する。legacy strategy setup IDはalias/evidenceであり主キーにしない。
- CURRENT観測で明示的に支持が消えたmembershipは、そのobservationで`valid_to`を閉じる。後日同じstrategy setup UIDが戻れば新intervalを追加する。
- stale/missing/error観測ではmembershipを閉じない。
- join/leaveやstrategy集合の全入替えはCore UIDを変更せず、Core phaseを直接変更しない。breakout/aligned countは当日のobserved factsとしてguardに使用する。
- 同一strategyの候補が複数で一意に解決できなければ、そのmembershipだけAMBIGUOUSとし、Core UID候補選択の近道にしない。

## 10. Pivot Continuity

### 10.1 Two pivots

- `observed_primary_pivot`: 当日Coreが選んだ最寄りPivot。毎run保存でき、strategy/type/basis/fidelityを伴う。
- `tracking_pivot_revision`: Phase 2Aが同じsetupのfailure/recovery/cross判定に使う版付き基準。

MINT時はCURRENT観測のobserved primary Pivotをrevision 1とする。複数strategy Pivotの平均や最高値を新たに合成しない。

### 10.2 Revision rules

- 初回breakout前かつ安全にLINKできた場合、finite positiveなobserved primary Pivotの価格/type/basis/strategy/fidelityのいずれかがexactに変化すれば新revisionをappendする。
- phase評価は観測直前に有効だったrevisionで行い、突破がなければ新revisionを次回から有効にする。
- 一度BREAKOUT_CONFIRMEDまたはbootstrap POST_BREAKOUTになったらtracking Pivotを固定する。当日のprimary Pivot変更は観測事実として残すだけ。
- revisionはUIDを変えない。既存active setupがないときだけ新candidateをMINTできる。
- PRACTICAL/STRUCTURE/PROXYの変更はfidelity revision。PROXYをSTRUCTURE相当と推定しない。

### 10.3 Corporate actions

株式分割・調整後価格改訂で過去と現在のprice scaleが一致しない場合、通常transitionを止め`PRICE_BASIS_DISCONTINUITY`とする。調整係数、適用日、sourceが証明できる場合だけ、専用`CORPORATE_ACTION_REBASE` migrationでcloseと全tracking revisionを同じ係数へ変換する。通常Pivot revisionで吸収しない。

## 11. Failure, Retry, Rebreakout Decisions

### 11.1 FAILED source

採用するのはState Machine tracking Pivotに対する独自guardである。Core/手法別FAILEDはstrategyごとに別Pivotを見ており、Validation signalは別ID・別開始点なので、いずれもPhase 2A FAILEDの直接sourceにはしない。cutover時にCore BREAKOUTをCURRENT観測したsetupは、`BOOTSTRAP_OBSERVED`をbreakout cycle 1の左打切りanchorとして扱い、以後のtracking Pivot breachを評価できる。ただし初回BREAKOUT eventや実際の過去breakout日は生成しない。

`FAILED_CONFIRMED`には必ずprior breakout event UIDまたはbootstrap anchor UID、tracking revision、close、97% line、observation UIDを記録する。突破前FAILEDはschema/guardで拒否する。

### 11.2 Retry and repeated cycles

- RETRY_WATCH entryは1観測でよい。連続2日を要求すると欠測で遅れ、現行データだけで優位性が証明されていないため採用しない。
- volumeはRETRY_WATCH条件にしない。volumeは再突破時のCore手法別breakout countへ既に反映される。
- FAILEDからRETRY_WATCHを経由しないqualified crossもREBREAKOUTとして許可する。
- 同じUIDで再FAILED→retry→rebreakoutを複数回許可する。各rebreakoutで20session clockをリセットする。
- 回数上限は設けないが、20session超過またはterminal EXPIREDで終了する。
- Pivotより上へ戻ってもqualified crossを取り逃した場合はFAILEDのまま`RECOVERY_UNCONFIRMED`。後日crossを捏造しない。

## 12. Threshold Inventory and Decisions

| Config | Value | Unit/boundary | Decision | Rationale / validation |
|---|---:|---|---|---|
| existing `recent_breakout_days` | 5 | trading rows | observed Core stateだけに利用 | event dedupe期間には使わない |
| existing `extended_above_pivot_pct` | 8 | `> 8%` | phaseに不採用 | EXTENDEDは表示fact |
| `failure_below_tracking_pivot_pct` | 3 | close `< P*0.97` | existing値を同義で再利用 | strict breach。等号はFAILEDでない |
| `retry_watch_distance_pct` | 3 | `d=(P-close)/P*100`, `0 <= d <= 3` | new provisional | failure lineと対称。1/2/5%はsensitivity対象 |
| `candidate_min_aligned_strategies` | 2 | `>= 2` | consensus境界を再利用 | 1手法だけのnoiseでMINTしない |
| `breakout_min_strategies` | 2 | `>= 2` | consensus境界を再利用 | Core BREAKOUTの意味と一致 |
| `pre_breakout_max_sessions` | 90 | cross優先後、age `> 90`でexpire | existing値をtrading sessionとして明確化、provisional | calendar dayでない。60/90/120を検証 |
| `post_breakout_max_sessions` | 20 | rebreakout優先後、age `> 20`でexpire | existing tracking値を再利用、provisional | 20-session成果測定と一致。10/20/30を検証 |
| `auto_link_max_gap_sessions` | 5 | gap `<= 5` | new provisional | 1取引週。1/3/5/10を監査 |

境界例: Pivot 1000円では970円はFAILEDではなく、969.99円相当からFAILED。retry zoneは970円以上1000円以下。1000円から1000円超へcrossし、2手法以上のbreakoutがあると突破。

## 13. Time, Missing Data, and Replay

### 13.1 Time fields

- `analysis_date`: その銘柄の価格観測市場日。
- `expected_market_date`: runが期待する市場日。
- `observed_at`: sourceを取得したtimestamp。
- `detected_at`: engineがeventを確定したtimestamp。
- `effective_date`: eventを証明するCURRENT observationのanalysis date。
- `market_session_index`: benchmarkの取引日列による単調index。

T日のPivotはT-1までのOHLCVから作る現行規則を維持する。`effective_date`を欠測区間へbackdateしない。

### 13.2 Missing/stale/scope

- analysis dateがexpectedより古ければSTALEで遷移なし。
- missing、fetch/analysis error、8303の履歴不足はphase unchanged、continuity PAUSED。
- 一時的scope離脱はOUT_OF_SCOPE auditを残し、phase unchanged。復帰gapが5session超ならAMBIGUOUS。
- session ageはbenchmark calendarで計算するが、missing日の中間eventは生成しない。
- 長期gap後はphaseを自動EXPIREせずSUSPENDED_GAP。current observationで同一性を証明できないためである。
- 恒久離脱は日数ではなくstructured master evidenceで判定する。許可reasonは`DELISTED_CONFIRMED`、`SECURITY_CODE_RETIRED`、`CORPORATE_SUCCESSOR_CONFIRMED`、明示migration manifestの`SCOPE_POLICY_TERMINAL_REMOVAL`。単なるdynamic universe落選、取得不能、連続OUT_OF_SCOPEは証拠にならない。
- 証拠が確認できたrunで全nonterminal setupをCLOSEDにし、`SETUP_CLOSED`を1回だけ発行する。後継codeがある場合はrelationを保存するが、旧UIDを新codeへLINKしない。
- 誤った恒久証拠が後日訂正された場合、CLOSEDを直接reopenせず`CORRECTION_RECORDED`をappendする。再開が必要なら人間承認したmigrationまたは新UIDとする。
- What Changed/active setupのconsumerは`current runでin_scope=true`かつphaseがnonterminalなsetupだけを通常一覧へ出す。一時OUT_OF_SCOPE/SUSPENDED_GAPはledgerには残すが通常一覧から除外し、`SETUP_CLOSED`だけを発生日に一度通知できる。これによりghost setupが綛れ込まない。

### 13.3 Same-day and out-of-order

- canonical keyは`namespace + code + analysis_date + state_machine_version`。
- 同日input hashが同じ: idempotent no-op。
- 同日input hashが違う: 後着優先にせずconflictをquarantine。replay承認後にsupersede関係をappendする。
- live current stateより古いanalysis date: live tableへ適用せずreplay namespaceへ送る。
- 並行runはoptimistic versionとunique idempotency keyで片方だけcommit。敗者はseedを再読込して再評価する。

### 13.4 Version changes

- logic/strategy/threshold変更は新観測のprovenanceとして保存するが、UIDは変えない。
- `state_machine_threshold_version`変更は既存stateを同じ意味で継続できるかmigration manifestで明示する。無ければshadow lineageで計算し、live stateへ混ぜない。
- state enum/guard意味を変える`state_machine_version`変更はexplicit state migrationまたは新lineageが必須。
- historical replayは`replay_namespace`と`replay_run_id`を持ち、live current stateを直接更新しない。

## 14. Logical Persistence Schema

実装時の物理名は既存migration規約に合わせられるが、意味とunique keyは変えない。

### 14.1 `state_machine_runs`

Primary key `run_id`。Section 8のstatus、全version、expected date、scope hash/count、各observation/identity/transition count、structured error summary、seed hash/version/count、input hash、開始/終了時刻を保持する。

Invariants:

```text
scope_total == count(run_scope_members where in_scope = true)
scope_total == sum(all terminal in-scope observation statuses)
identity_link_total + identity_mint_total + identity_ambiguous_total
  + no_setup_total == count(identity_decisions for eligible current slots)
transition_total == count(events with is_phase_transition = true and producer_run_id = run_id)
terminal run cannot return to STARTED
```

### 14.2 `run_scope_members`

Key `(run_id, code)`。scope membership、observation status、analysis/latest price date、history count、reason codes、structured error referenceを全965件分保持する。

### 14.3 `accepted_observations`

Primary key `observation_uid`、unique `(namespace, code, analysis_date, state_machine_version, input_hash)`。必要最小限:

- run/code/date/timestamps/session index。
- close、previous accepted close。
- Core observed state、5 trend states、Connors state。
- aligned/breakout trend count。
- observed primary Pivot price/strategy/type/basis/fidelity/reference date。
- quality、legacy IDs、all input versions/input hash。
- decision slot/status、nullable Core UID、tracking revision reference。

rank/name/plan/conditions/chart/full price history/財務原票は入れない。

### 14.4 Identity and setup tables

- `core_setup_identity_ledger`: Setup Identity Designのimmutable birth/UID正本。
- `setup_identity_decisions`: observation slotごとのdecision、target、reason、evidence、supersede reference。
- `setup_pivot_revisions`: Core UID + revision no、effective observation、Pivot facts、active interval。
- `setup_strategy_memberships`: Core UID + strategy setup UID + valid interval。

### 14.5 `current_setup_states`

Primary key `(state_lineage, core_setup_uid)`。phase、continuity、`distribution_eligible`、phase entered observation/date、latest accepted observation、tracking revision、latest breakout anchor/failure/event refs、breakout cycle number、closure reason/evidence、mint/latest session index、state-machine/threshold version、optimistic `state_version`を持つ。これは再構築可能なcacheで、event/observation/identity ledgerが監査正本。

### 14.6 `setup_events`

Append-only。event UID、Core UID、type、from/to phase、effective/detected date、`effective_date_status`、nullable observation、Pivot revision、prior related event、versions、reason/evidence hash、idempotency key、producer run、correction/supersede referenceを保持する。市場eventはCURRENT observation必須。`SETUP_CLOSED`だけはobservationの代わりにstructured permanent-exit evidence referenceを必須にできる。

Event UID request:

```text
event_version + core_setup_uid + event_type + observation_uid
+ state_machine_version + occurrence_ordinal
```

SHA-256 full hashをunique保存し、表示UIDは`sev1:<code>:<160bit>`。同一full hashは同event、短縮部衝突かつfull hash相違はcommitを停止する。初回BREAKOUT ordinal=1、REBREAKOUTは2以上。legacy `signal_id`はnullable aliasで同義扱いしない。

### 14.7 Rejections and artifacts

`transition_rejections`はobservation、candidate UID、reason、rule versionを保持する。公開JSONは最新phaseと直近eventだけに限定し、全履歴の正本にしない。

JSON例は`docs/examples/phase2a-state-machine/contract-examples.json`に示す。

## 15. Atomicity and Durable Seed

1runは次を1 SQLite transactionでcommitする。

```text
validate seed and scope
→ insert run/scope/observations
→ insert decisions and mint/link ledger rows
→ insert pivot revisions/memberships
→ insert events
→ optimistic update current states
→ assert referential/count invariants
→ mark run COMPLETE/PARTIAL
→ COMMIT
```

例外時はROLLBACKし、FAILED run manifestだけを別の失敗記録transactionで残す。identityだけ、eventだけの孤立を許さない。previous accepted stateはterminal COMPLETE/PARTIALかつ`state_commit_status=COMMITTED`のrunだけから読む。

publishはDB commit後の別段階。publish失敗でも確定ledgerをrollbackせず、同じrun artifactを再生成する。これにより再MINT/再eventを防ぐ。

起動時にseed schema/version、SHA-256、row count、producer run、referential integrityを検証する。欠損・hash不一致なら全件MINTせずrun FAILEDまたはstate stage PARTIAL。Actions cacheは検証済みseedのcopyを高速化するだけで正本にしない。

## 16. Downstream Compatibility Matrix

| Consumer | Phase 2A read/write | Compatibility rule |
|---|---|---|
| Core snapshot/detail/ranking | writeなし | Phase 2A phaseをCore score/rankへ混入しない |
| Morning Brief Phase 1 | 当初readなし | 現契約維持。後の承認でlatest eventをoptional追加 |
| `signal_snapshots/history` | writeなし | legacy signal IDとevent UIDを統合しない |
| Validation / Control | 当初readなし | 既存結果不変。将来event cohortはversion/provenance付き別入力 |
| Experimental | 自動統合なし | Experimental namespace/identity/stateをCoreへLINKしない |
| Research | shadow後にoptional read | event effective date時点のversionと観測ref必須 |
| Committee export | Phase 1契約不変 | optional extension、欠損時fallbackを維持 |
| Morning Brief What Changed | 将来read | `setup_events`だけを差分sourceにしCore state反復を通知しない。current in-scope/nonterminalだけ通常表示し、SETUP_CLOSEDは一度だけ表示 |

## 17. Cutover, Migration, and Rollback

### 17.1 Cutover plan

1. additive schemaだけを作る。
2. durable identity/state seedの保存・検証経路を先に作る。
3. cutover CURRENT観測でcandidate gateを満たすものだけ`CUTOVER_BOOTSTRAP` MINT。
4. Core BREAKOUTならPOST_BREAKOUTをbootstrapできるが、crossを証明できない限りBREAKOUT eventを捏造しない。`BOOTSTRAP_OBSERVED`をbreakout cycle 1のanchorとし、20session clockはcutover sessionから開始する。
5. cutover以前の964件を新UIDへ遡及LINKしない。
6. shadow writeで数run実行し、既存consumerは読まない。
7. fixture、idempotency、scope 965 status、missing安全性、容量benchmarkを確認する。
8. 人間承認後だけconsumerを段階的に接続する。

### 17.2 Rollback

- feature flagでPhase 2A write/readを停止。
- 新table/artifactは削除せずread-only凍結。
- 旧Core/Validation/Morning Briefを継続。
- additive schemaなので逆migrationを必須にしない。
- 既存履歴を更新していないためlegacyへ変換し戻さない。
- rollback後の再開は最後の検証済みseed hashから行う。

## 18. Capacity and Retention

前提: 965銘柄、250run/年、最大241,250 scope rows/年。setup observationもworst caseで全銘柄CURRENTとする。

### 18.1 Pre-benchmark estimate (superseded)

次は設計時の机上見積りであり、2026-09-12のLimited Runtime Benchmarkによって棄却された。

| Data | Rows/year assumption | Estimated bytes/row | Annual estimate |
|---|---:|---:|---:|
| Run manifest/error summaries | 250 | 8,000 | 2.0 MB |
| Scope member status | 241,250 | 220 | 53.1 MB |
| Accepted minimal observation | 240,000 | 520 | 124.8 MB |
| Identity decision | 240,000 | 150 | 36.0 MB |
| Pivot/membership revision | 36,000 (15%) | 260 | 9.4 MB |
| Events/rejections | 24,000 (10%) | 340 | 8.2 MB |
| Current state/ledger | 2,000 max active/closed working set | 700 | 1.4 MB |
| SQLite page/free-space allowance | subtotalの約10% | — | 23.5 MB |
| **Superseded total estimate** | — | — | **約258 MB/年** |

SQLiteのTEXT UID、監査hash、JSON provenance、secondary index、page allocationを過小評価していた。full candidate複製の約907MB/年案は引き続き採用しない。

### 18.2 Measured planning envelope

固定1,000 scope rowsを10run、DELETE/WAL、event 0/10/25%、revision 0/15/50%の18scenarioで測定した。

| Measure | Result |
|---|---:|
| Representative: WAL / event 10% / revision 15% | **473.329MB/年** |
| All-scenario annual range | **435.482–539.931MB/年** |
| Representative incremental per 1,000-scope run | 1.962MB |
| Representative peak WAL + SHM | 7.593MB |
| Public latest + 30-day events | 0.575MB raw / 0.119MB gzip |

以後のcapacity baselineは473MB/年、planning envelopeは435–540MB/年とする。physical schemaの正規化で削減できても、再benchmarkするまで削減分を計画へ織り込まない。

Retention:

- Hot operational DBはrolling 12か月を目標にする。
- 古いrun/scope/observation/decisionは年単位のimmutable archiveへ移し、archiveを含め最低3年参照可能にする。
- identity ledger、current state、active Pivot/membership、lineageに必要なevent anchorはcompact durable seedとして無期限保持する。full history DBを毎run seedとして配布しない。
- GitHub Pagesには最新state、直近30市場日のevent、最新run qualityだけを公開する。実測0.575MB rawであり十分小さい。
- archive圧縮率は未測定なのでcapacityから差し引かない。

### 18.3 Limited Runtime Benchmark result

結果は`docs/PHASE2A_LIMITED_RUNTIME_BENCHMARK.md`と`docs/examples/phase2a-state-machine/runtime-benchmark-results.json`に固定した。

- 18scenarioのintegrity/foreign key検査PASS。
- representative transactionはofficial passでp50 27.554ms、p95 34.252ms。
- seed export/import、SHA-256、current state rebuildはPASS。rebuild hash一致。
- transaction途中失敗→retryで孤立行0、event/mint重複0。
- seed欠損/hash mismatchはfail closed、mint 0。

元見積りとの差が+83.46%だったため、上記capacity/retentionへ改訂した。final physical schemaでも同じbenchmarkを再実行し、435–540MB/年のrange外ならImplementation Reviewへ戻す。

## 19. Design Verification

機械可読fixtureは`docs/examples/phase2a-state-machine/transition-fixtures.json`。通常、閾値境界、欠測、disorder、replay、複数setupを含む。設計検証はproduct codeやDBを変更せず、JSON schema/invariantをオフライン確認する。

特に同じ`csu1:5901:...`で次を保持する。

```text
BREAKOUT_CONFIRMED
→ FAILED_CONFIRMED
→ RETRY_WATCH_ENTERED
→ RETRY_WATCH (継続)
→ REBREAKOUT_CONFIRMED
```

最後のevent ordinalは2であり、初回BREAKOUTを上書きしない。間に1session欠測があればphaseを保持し、次のCURRENT観測はgapが5以下の場合だけ同UIDへLINKする。6以上ならAMBIGUOUSでcrossを推定しない。

## 20. Human Review Gate

実装前に人間が承認または差し戻す項目:

1. event-centered modelと7phase（恒久離脱用CLOSEDを含む）。
2. State Machine独自FAILED定義。
3. 3% failure、3% retry、2手法、90/20/5 sessionの境界。
4. BREAKOUT/REBREAKOUTのcross条件とevent化。
5. 突破前Pivot revision、突破後freeze。
6. MVPの`core-primary` slotと複数候補AMBIGUOUS方針。
7. long gap/scope復帰をSUSPENDED_GAPにし、structured permanent-exit evidenceだけでCLOSEDにする方針。
8. crossをexpiryより先に評価し、最初の期限超過CURRENT観測の真のcrossを保持する方針。
9. same-day conflict quarantine、version migration規則。
10. 実測473MB/年、435–540MB/年range、12か月hot + 年次archive案。
11. cutover bootstrapでは過去eventを推定しないこと。

2026-09-12のユーザー指示「Limited Runtime Benchmarkに進んで」を、修正後設計の承認およびbenchmark実行承認として記録する。product implementation、DB migration、workflow、公開物の変更はまだ行わない。

## 21. Known Limitations and Deferred Decisions

- 3% retry幅、90/20/5 sessionは暫定。shadow historyでsensitivity検証が必要。
- 現行Coreは同一銘柄の複数構造slotを安定提供しない。schemaは複数を許すが、自動解決は安全側でAMBIGUOUSになる。
- dynamic scope落選と恒久離脱を区別するmaster reasonの実フィールドは実装前adapter auditで確認する。証拠がなければCLOSEDにせず、通常consumerから除外したSUSPENDED_GAPとして保持する。
- cross優先によって期限超過sessionに成立するevent数をshadow historyで監査し、expiry-firstとの差をHuman Reviewへ報告する。
- 株式分割の調整係数sourceはPhase 2A実装前に既存price pipelineで確認する。
- 休日calendarは新APIを導入せず、既存benchmark価格日のsession indexを使う。その欠損時はtransitionを停止する。
- cutover時にCore BREAKOUTだったsetupの真のbreakout日は不明。bootstrap後の成績利用では左打切りとして扱う。
