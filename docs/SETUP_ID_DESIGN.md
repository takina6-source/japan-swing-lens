# Japan Swing Lens — Stable Setup Identity Design

設計日: 2026-09-12 JST

## 0. Decision Summary

**設計結果: REVIEW_REQUIRED**

採用案は、**永続identity台帳 + immutableな発行要求から作る決定的ID**のhybrid方式とする。

- Core setup identity: `csu1:<code>:<sha256先頭160bit>`
- Strategy setup identity: `ssu1:<code>:<strategy_slug>:<sha256先頭160bit>`
- identityは発行時に一度だけ作り、日次の価格、Pivot、state、構成手法から再計算しない。
- Pivotや根拠の変更は同一identity内のrevisionとして保存する。
- 手法の加入・離脱はmembership履歴として保存し、Core setup identityを変更しない。
- signal/event、日次observationはsetup identityとは別IDにする。
- 同一setupか新setupかを決める市場ルールはidentity層に置かない。後続Phase 2Aが`LINK`、`MINT`、`AMBIGUOUS`の決定をidentity層へ渡す。
- 判断できない観測にはIDを推定付与しない。
- 旧2時点964銘柄は、安定したlineage台帳が存在しないため全件`AMBIGUOUS`。構造一致は補助証拠として残すが自動統合しない。

本設計は、状態遷移やFAILED後の閾値を決めずに、IDの安定性・一意性・監査可能性だけを先に確定する。

## 1. Evidence

2026-09-03と2026-09-10の共通964銘柄を比較した。

### 1.1 Consensus実測

| 項目 | 件数 |
|---|---:|
| 比較対象 | 964 |
| 現行総合setup_id維持 | 0 |
| 現行総合setup_id変更 | 964 |
| consensus state維持 | 711 |
| consensus state変更 | 253 |
| 5手法すべてのPivot価格が同じ | 413 |
| 5手法のPivot価格・type・basisがすべて同じ | 355 |
| 上記構造とconsensus stateがともに同じ | 250 |

構造が全く同じ355銘柄でも総合IDはすべて変化した。日次の構成手法ID集合をhashする現方式は、継続identityとして成立していない。

### 1.2 Strategy別実測

| Strategy | ID維持 | ID変更 | start date維持 | Pivot維持 | type維持 | state維持 |
|---|---:|---:|---:|---:|---:|---:|
| Minervini | 0 | 964 | 0 | 580 | 836 | 795 |
| Qullamaggie | 0 | 964 | 0 | 719 | 896 | 800 |
| CAN SLIM | 0 | 964 | 0 | 810 | 929 | 701 |
| Weinstein | 8 | 956 | 8 | 748 | 917 | 814 |
| Darvas | 0 | 964 | 0 | 751 | 910 | 744 |

現行手法IDの材料であるrolling windowの開始日・形成日はほぼ全件で移動する。Pivot価格だけを固定しても、開始日が変わればIDは変わる。

既存`setup_registry`は970銘柄・4,850行に対して4,626種類の手法setup IDであり、銘柄をまたぐ衝突も存在する。またGitHub Actionsの継続状態は`data`ディレクトリのcache復元に依存する。cacheが失われた場合、現在値から同じIDを復元できる契約はない。

機械可読な銘柄別分類は `docs/examples/setup-identity/legacy-2026-09-03_vs_2026-09-10.json` に固定した。

## 2. Current Dependency Map

```text
strategy_pivot()
  └─ legacy strategy setup_id
       prefix | rolling setup_start_date | pivot_price
       └─ StrategyResult.setup_id
            ├─ setup_registry (code, strategyで上書き)
            └─ consensus_setup_id()
                 code | 当日のstrategy setup_id集合
                 └─ StockAnalysis.setup_id
                      └─ signal_id = setup_id : strategy_version
                           ├─ signal_snapshots / signal_history
                           ├─ controls
                           ├─ experimental core link
                           └─ research events / validation subjects
```

現方式ではPivot detectorの再計算がDownstreamのsignal lineage全体を分断する。

## 3. Identity Model

### 3.1 分離する6概念

| 概念 | 新しい論理名 | 意味 |
|---|---|---|
| Core Setup Identity | `core_setup_uid` | 1銘柄内の1つのsetup lineage |
| Strategy Setup Identity | `strategy_setup_uid` | 1手法が観測するsetup lineage |
| Setup Revision | `(core_setup_uid, revision_no)` | 同一setup内のPivot・根拠属性の版 |
| Strategy Membership | membership row | 手法setupとCore setupの期間付き関係 |
| Observation | `observation_uid` | 1run・1銘柄の観測 |
| Signal / Event | `event_uid` | BREAKOUT等の出来事。setupとは別 |

旧`setup_id`と旧`signal_id`は`legacy_*`として保持する。

### 3.2 不変条件

1. `core_setup_uid`のcodeは、そのidentityの全revision・membership・eventのcodeと一致する。
2. 発行済み`core_setup_uid`はstate、Pivot、手法集合、versionが変わっても文字列を変更しない。
3. 1 observationを複数setupへ関連付けられる。同一銘柄に複数setupが共存できる。
4. 1 strategy setupを同時に複数Core setupへ自動所属させない。必要なら`AMBIGUOUS`にする。
5. `event_uid`は`core_setup_uid`を外部キーとして参照できるが、同じ文字列を使わない。
6. identity解決不能時は`core_setup_uid = null`とresolution statusを保存する。
7. CoreとExperimentalは別namespace。文字列が似ていても同一entityではない。

## 4. ID Format and Canonicalization

### 4.1 Format

```text
Core:      csu1:5901:325c5cdb22b25fbce9947c515c55a237d822aaad
Strategy:  ssu1:5901:minervini:ef10e0f80439a6691294a52a06e78af56d55b1de
Revision:  csr1:5901:325c5cdb22b25fbce9947c515c55a237d822aaad:000001
```

- `csu1` / `ssu1` / `csr1`: namespaceとID schema version。
- code: 現行契約では4桁の文字列`^[0-9]{4}$`。数値化しない。
- 将来英数字コードへ対応する場合はcode schemeを変更し、黙って正規化規則を変えない。
- strategy slug: `minervini`、`qullamaggie`、`can_slim`、`weinstein`、`darvas`。表示名とは別の固定enum。
- suffix: canonical mint requestのSHA-256先頭40桁、160bit、小文字hex。

### 4.2 Canonical mint request

Coreの発行要求は次の許可リストだけをRFC 8785相当のcanonical JSONにする。

```json
{
  "code": "5901",
  "identity_epoch": "phase2a-cutover-1",
  "identity_schema_version": 1,
  "namespace": "CORE_SETUP",
  "origin_observation_uid": "obs1:run-001:5901",
  "origin_slot": "0"
}
```

要件:

- UTF-8、文字列はUnicode NFC、keyは辞書順、空白なし。
- null、未知key、floatを許可しない。
- 日付を含める場合は`YYYY-MM-DD`、時刻はUTCのRFC 3339に固定する。
- full SHA-256を`mint_request_sha256`としてDBに保存する。
- UID suffixはfull hashの先頭160bitを使う。
- state、Pivot価格、setup_start_date、formed_date、構成手法、logic versionをmint requestに含めない。

`origin_slot`は同一観測で複数setupを発行する場合の安定した局所番号であり、後続Phase 2Aが順序規則を定義する。identity層が価格順等を勝手に選ばない。

### 4.3 Collision

160bitのbirthday collision確率は100万IDで概算約`3.4e-37`。それでも確率をゼロとは扱わない。

- `setup_uid`と`mint_request_sha256`の両方にUNIQUE制約を置く。
- 同じfull hash・同じcanonical payloadはidempotent retryとして既存UIDを返す。
- 同じ短縮UID・異なるfull hashは`IDENTITY_TRUNCATION_COLLISION`でfail closed。
- 自動suffix延長や上書きはしない。schema versionを上げた明示的な再発行を要求する。
- 同じfull hash・異なるcanonical payloadは`IDENTITY_HASH_COLLISION`として停止する。

## 5. Alternative Analysis

| 案 | 長所 | 致命的な問題 | 判定 |
|---|---|---|---|
| 現在属性のcontent hash | DB不要、毎回再現可能 | rolling date・Pivot・手法集合で964/964変化 | 却下 |
| SQLite連番 | 単純、短い | codeを含まない、環境mergeと再seedで衝突 | 却下 |
| UUIDv4だけ | 安定、衝突確率が低い | retry時の二重発行を別keyで防ぐ必要、同一birth requestを再現不可 | 次点 |
| immutable birth requestのhashだけ | retry再現性が高い | ledgerなしでは後日のlinkを判断できない | 単独採用不可 |
| 永続台帳 + birth request hash | 発行idempotency、安定性、監査性を両立 | 台帳のdurable seedが必要 | **採用** |

重要なのはhash方式そのものではなく、**IDを日次属性から再計算せず、発行済み台帳を継続参照すること**である。

## 6. Identity Decision Interface

identity層は市場状態から同一setupを推論しない。呼び出し側から次のdecisionを受ける。

```json
{
  "observation_uid": "obs1:run-002:5901",
  "code": "5901",
  "decision": "LINK",
  "target_core_setup_uid": "csu1:5901:325c5cdb22b25fbce9947c515c55a237d822aaad",
  "origin_slot": null,
  "decision_rule_version": "phase2a-state-machine-pending",
  "reason_codes": ["EXPLICIT_LINEAGE_REFERENCE"],
  "evidence_refs": ["obs1:run-001:5901"]
}
```

decision enum:

- `LINK`: 既存identityへ観測を関連付ける。
- `MINT`: 新identityを発行して関連付ける。
- `AMBIGUOUS`: 推定せずidentityをnullのまま保存する。
- `NO_SETUP`: setup観測がない。identityを発行しない。

制約:

- `LINK`は同じcodeのidentityだけを許可する。
- `MINT`は一意な`origin_slot`と`identity_epoch`を要求する。
- 複数候補があり一意に決められない場合は`AMBIGUOUS`。
- identity層は`FAILED`、`WATCH`、価格差、経過日数からdecisionを生成しない。
- Phase 2Aはこのinterfaceのdecision生成規則を設計する。identity契約はその結果を安全に保存する。

この境界により、setup identity設計を状態遷移設計から独立させる。

## 7. Revision and Membership

### 7.1 Revision

revision対象:

- 採用Pivot価格・type・basis・fidelity。
- 構造の観測開始日・形成日。
- 根拠となるStrategy Setup Reference集合のsnapshot。
- 使用したlogic/strategy/threshold version。

revisionを作っても`core_setup_uid`は変えない。同じ内容hashならrevisionを増やさない。変更理由と元observationを必須にする。

新setupか同一setup内revisionかの市場判断はPhase 2A decisionであり、この設計では数値閾値を置かない。

### 7.2 Strategy Membership

手法の加入・離脱は期間付きmembership rowで表す。

- `member_from_observation_uid`
- `member_to_observation_uid` nullable
- `membership_reason_code`
- `decision_rule_version`

membership変更だけではCore UIDを変更しない。Strategy UIDも、明示的な`LINK`が続く限り日次のrolling dateから再発行しない。

## 8. Logical Persistence Schema

これは実装指示用の論理schemaであり、本GoalではDBを変更しない。

### 8.1 `core_setup_identities`

| Column | Contract |
|---|---|
| `core_setup_uid` | PK、format検証 |
| `code` | NOT NULL、UID内codeと一致 |
| `identity_version` | `1` |
| `identity_epoch` | cutover/bootstrap区分 |
| `origin_observation_uid` | NOT NULL |
| `origin_slot` | NOT NULL |
| `mint_request_json` | canonical payload |
| `mint_request_sha256` | UNIQUE、64桁full hash |
| `provenance` | `LIVE` / `CUTOVER_BOOTSTRAP` / `MANUAL_MIGRATION` |
| `record_status` | `ISSUED` / `ALIASED` / `VOID`。投資stateではない |
| `created_at` | UTC timestamp |

### 8.2 `strategy_setup_identities`

`strategy_setup_uid` PK、code、strategy_slug、identity version、origin observation、origin slot、full mint hash、provenance、created_atを持つ。codeとstrategyをUIDに含める。

### 8.3 `core_setup_revisions`

PKは`(core_setup_uid, revision_no)`。`revision_hash`、Pivot最小属性、evidence observation、各version、reason code、created_atを持つ。full candidateやchartを保存しない。

### 8.4 `core_setup_strategy_memberships`

PKは`(core_setup_uid, strategy_setup_uid, member_from_observation_uid)`。期間とreasonを持つ。同一code制約をapplicationとtransaction内で検証する。

### 8.5 `setup_identity_decisions`

PKは`observation_uid + decision_slot`。decision、target/minted UID、rule version、reason codes、evidence hash、created_atを保存する。`AMBIGUOUS`も監査可能な行として残す。

### 8.6 `legacy_setup_aliases`

旧namespace、旧ID、code、新UID nullable、mapping status、confidenceではなく証拠分類、reason、reviewer、created_atを持つ。

mapping status:

- `EXACT_1_TO_1`
- `PROVISIONAL_1_TO_1`
- `SPLIT_1_TO_N`
- `MERGED_N_TO_1`
- `UNRESOLVED_LEGACY_ID`

## 9. Transaction and Concurrency

発行処理:

1. canonical payloadを検証し、full SHA-256とUIDを算出。
2. `BEGIN IMMEDIATE`。
3. `mint_request_sha256`を検索。
4. 同payloadがあれば既存UIDを返す。
5. 同hash・異payload、または短縮UID衝突ならfail closed。
6. identity、initial revision、decisionを同一transactionでinsert。
7. commit後にのみUIDを呼び出し側へ返す。

途中失敗時は全rollbackし、identityだけを孤立させない。並行runの同じmint requestはUNIQUE制約で1件に収束する。

Actions cacheだけをidentityの正本にしない。Phase 2A実装では、前runのidentity ledgerを検証付きartifactとしてseedし、seed hash・schema version・件数をrun manifestへ記録する。seedが欠損・破損した場合は全銘柄を再採番せずrunを`IDENTITY_LEDGER_UNAVAILABLE`として停止またはPARTIALにする。

## 10. Downstream Compatibility Matrix

| Consumer | 現行参照 | 新規参照 | Migration rule |
|---|---|---|---|
| `setup_registry` | legacy strategy setup_id | strategy UID + Core UID + latest revision参照 | 旧列をdual-write期間だけ保持 |
| `signal_snapshots` | `signal_id`, legacy setup_id | event UID + Core UID FK | 既存行は書換えずalias参照 |
| `signal_history` | signal_id | event UIDを維持、Core UIDはevent経由 | 履歴の帰属を推定更新しない |
| Validation | signal_id | event UID + Core UID | event生成条件は別Goal |
| Control | signal_id | event UID、必要ならCore UID参照 | control group IDは既存eventに固定 |
| Experimental | experimental setup/signal ID | 独立namespace、任意のCore UID reference | 自動統合禁止 |
| Research | source signal + legacy setup | event UID + Core UID | 既存eventを別setupへ付替えない |
| Committee | legacy IDは実質補助 | Core UID/revisionを追加可能 | schema versionを上げてdual-read |
| Morning Brief Phase 1 | setup ID非依存 | 変更なし | 本設計で変更しない |
| Phase 2A observation | 未実装 | Core UID nullable + resolution status | 最小観測事実のみ |

signal/event IDの具体形式と発生条件はPhase 2A以降で決める。ここではsetup identityと別entityであることだけを固定する。

## 11. Legacy Classification Result

旧2時点だけではbirth record、membership履歴、欠測中の観測がない。構造一致は継続の可能性を示すが、同一性を証明しない。そのため自動legacy mappingは行わない。

| Classification | 件数 |
|---|---:|
| SAME | 0 |
| DIFFERENT | 0 |
| AMBIGUOUS | 964 |

AMBIGUOUS内の証拠bucket:

| Reason | 件数 |
|---|---:|
| 構造・consensus stateとも一致 | 250 |
| 構造一致、consensus state変更 | 105 |
| 構造変更、consensus state一致 | 461 |
| 構造・consensus stateとも変更 | 148 |

250件も`PROVISIONAL_1_TO_1`候補にはできるが、`EXACT_1_TO_1`にはしない。人間が個別レビューしない限り`UNRESOLVED_LEGACY_ID`を維持する。

これは新設計が将来分を追跡できないという意味ではない。cutover後は、最初の`MINT`と以後の明示的`LINK`を台帳に残すため、同一性を日次属性から遡及推定する必要がなくなる。

## 12. Migration Plan

1. **Schema-only**: 新テーブルを追加し、既存表・公開JSONは変更しない。
2. **Cutover bootstrap**: cutover runで観測されたsetupに`CUTOVER_BOOTSTRAP`として新UIDを発行する。旧9月3日/10日IDへ過去継続を主張しない。
3. **Shadow write**: legacy IDと新identity decisionを並行保存する。consumerは旧経路を継続。
4. **Shadow audit**: 二重発行、code不一致、孤立event、予期しない再採番を検査する。
5. **Dual read**: Committee/Research等に新UIDを追加するが、legacy IDも残す。
6. **Consumer cutover**: 明示承認後、継続追跡の主参照を新UIDへ切替える。
7. **Legacy freeze**: 新規legacy ID生成を止めるが、既存列・aliasは削除しない。

自動migration可能なのは、同一run内で新旧を同時に観測したcutover以後の1:1対応だけ。過去履歴は原則`UNRESOLVED_LEGACY_ID`。

## 13. Rollback Plan

- shadow/dual-write期間は旧read pathを残す。
- rollback時はconsumer feature flagをlegacy readへ戻す。
- 新identity表は削除せずread-onlyで凍結し、監査証拠を保持する。
- 旧signal/control/research行を新UIDへ物理的に書換えないため、rollbackで復元処理を不要にする。
- cutover後に発生した新eventはlegacy IDを捏造しない。新経路の停止期間として明示する。
- rollback前後でidentity数、decision数、revision数、孤立FK、code不一致、duplicate mint keyを照合する。

## 14. Fixed Design Verification

`docs/examples/setup-identity/design-fixtures.json`に期待関係を固定する。

検証する不変条件:

- 同じmint requestは同じUID。
- 別codeは同じ市場属性でも別UID。
- state、Pivot、手法membership、version変更は既存UIDの再計算材料にならない。
- Pivot変更は明示的LINK時だけsame identity + revision。
- 別構造はPhase 2AからMINT decisionを受けた場合だけnew identity。
- 欠測・複数候補・legacy不明はAMBIGUOUS。
- 並行発行はmint keyで1件へ収束。
- collisionはfail closed。
- legacy 1:1、1:N、N:1、unresolvedを区別する。

## 15. What This Design Does Not Decide

次は意図的な未決事項であり、Human Review後のPhase 2A State Machine Designへ渡す。

- どの市場観測を同一setupとして`LINK`するか。
- どの時点で`MINT`するか。
- setupのactive/inactive/expired。
- WATCH、BREAKOUT、FAILED、RETRY_WATCH、REBREAKOUTの遷移。
- FAILED後の経過日数、Pivotからの価格差、回復率、出来高条件。
- 欠測期間をまたぐlink許可条件。
- 同時に複数setup候補があるときの優先順位。

identity層はこれらを推測せず、Phase 2Aのdecisionを記録するだけにする。

## 16. Human Review Gate

Phase 2Aへ進む前に、次の7点を承認する必要がある。

1. hybrid方式を採用すること。
2. `csu1:<code>:<160bit>`形式。
3. state/Pivot/手法集合をID材料から外すこと。
4. Pivot変更をrevisionとして扱えること。
5. identity層とPhase 2A decisionを分離すること。
6. 旧964件を全件AMBIGUOUSとして自動統合しないこと。
7. cutover後のみ新UIDを正確に保証し、過去完全復元を主張しないこと。

承認されるまでproduction code、DB schema、workflow、公開成果物を変更しない。
