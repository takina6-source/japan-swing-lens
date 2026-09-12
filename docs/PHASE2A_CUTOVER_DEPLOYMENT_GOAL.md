# Japan Swing Lens — Phase 2A Cutover / Deployment Goal

作成日: 2026-09-12 JST

## 0. Goal

ローカルで完成・検証済みのPhase 2A State Machineを、既存の日本株分析、
ランキング、Validation、Experimental、Research、Morning Brief Phase 1を壊さず、
GitHub Actions上で日次継続運用できる状態へ段階的に切り替える。

本Goalで実現するのは次の4点である。

1. 前営業日のidentity/stateを検証可能なseedとして次回runへ引き継ぐ。
2. seed欠損・破損・同日競合・順序逆転時はfail closedし、全件MINTしない。
3. 初回cutoverと通常継続runを明確に分離し、手動承認なしに再初期化しない。
4. 複数回のproduction shadow確認後だけ、最小限のWhat Changed JSONを公開する。

このGoalはランキングロジック、売買シナリオ、Core判定、Experimental、Research、
Validationの評価方法を変更するものではない。

## 1. Confirmed Starting Evidence

- State Machine `sm1`、threshold `smt1`、identity `csu1`、decision rule `idr1`。
- Operational schema `phase2a-schema-v1`。
- Seed schema `phase2a-seed-v1`。
- Phase 2A既存suite: 79 passed。
- isolation test追加後の全体pytest: 248 passed。
- `engine.state_machine`から`engine.database`への直接・間接importは禁止され、自動testで固定済み。
- CLIは本番DB pathと`public/`配下へのshadow DB/output書込みを拒否する。
- Final physical benchmark: 約392–497MB/年、代表約429MB/年、capacity gate PASS。
- 既存964候補を過去へ遡って新UIDへLINKしない。
- Cutover時点のBREAKOUTは`BOOTSTRAP_OBSERVED`であり、架空の過去breakout eventを作らない。
- Structured permanent-exit masterは未接続のため、実運用の自動`CLOSED`はdisabled/fail closedのままとする。

## 2. Repository and Deployment Boundary

現在の作業ツリーは大規模なdirty stateであり、Phase 2A関連ファイルも未追跡である。
さらに、このcheckoutにはGit remoteが設定されていない。したがって、実装・公開時は
次を必須とする。

1. 公開先を`takina6-source/japan-swing-lens`としてread-only確認する。
2. remoteの追加、commit、pushは別途の明示的な実行指示を受けてから行う。
3. Phase 2A changed-file manifestとCutoverで追加・変更したpathだけを明示的にstageする。
4. `git add .`、`git add -A`、既存dirty変更の一括commitを禁止する。
5. 可能ならclean checkout/worktreeへ承認済みpathだけを移して公開候補を作る。
6. 公開前にstaged diffと対象path一覧を提示し、無関係な変更が0件であることを確認する。

本Goal文書の作成だけではcommit、push、GitHub Actions実行、Pages公開を許可しない。

## 3. Durable Seed Authority

### 3.1 採用方針

GitHub Actions cacheを正本にしない。`phase2a-state-v1`という専用GitHub Releaseの
immutable assetを、Phase 2A continuity seedのremote authorityとして使用する。

各成功runは少なくとも次を保存する。

- `phase2a-seed-YYYY-MM-DD-<run_id>.tar.gz`
- 同assetのSHA-256、market date、run ID、schema/version、row countを持つmanifest
- 各assetと対になる検証用manifest（mutableなlatest pointerは作らない）

Seed bundleは`seed.sqlite`、`seed-manifest.json`、`seed.sha256`だけを含む。
Full candidate、chart、rank、trade plan、財務原票、product DBは含めない。

GitHub Release assetが公開repository上で閲覧可能になることを、実装前Human Gateで
明示承認する。公開不可と判断された場合は、同じ契約を満たすprivate durable storeを
別途選定し、このGoalを勝手に代替方式へ変更しない。

### 3.2 Retention and rollback

- 日付・run ID付きassetを上書きせず追加し、最低5営業日分の検証済みimmutable seedを保持する。
- 継続時は公開Release内のmanifestを列挙・検証し、対象market date以前で最新のassetを決定する。
- rollbackは過去assetを明示指定して新しいlineageでreplayするのではなく、同じlive lineageの
  継続点として使用可能かを日付・hash・versionで確認してから行う。
- rollback seedより後の既存live stateを暗黙mergeしない。
- retention削除は正常seedが最低5世代存在することを確認した後だけ行う。
- Release/API障害時はcacheへfallbackせず、当日のPhase 2A runだけをSKIPPED/BLOCKEDにする。

## 4. Workflow Architecture

既存`.github/workflows/update-dashboard.yml`へPhase 2A専用step群を追加する。
Core分析成功後、同一runのsnapshot/detail/config/scopeからのみ入力を作る。

処理順序を固定する。

1. 全pytest 248件以上とfixed-input regressionを通す。
2. Core Web Exportを完了する。
3. Coreと同じrunのFull Screening Scope、snapshot、details、config hash、
   expected market date、market session indexを確定する。
4. `production-shadow`段階では公開path外のrunner tempへ出力する。
5. 通常runはremote authorityの検証済みmanifest群から最新適格seedを選び、SHA-256、schema、SQLite integrity、
   foreign key、row count、lineage、version、market dateを検証する。
6. 新規の空DBへseedをrestoreする。product `data/momentum.db`は開かない。
7. Phase 2A runを1回実行する。
8. COMMITTEDまたは同一inputのNO_OPだけを成功とする。
9. run-summary、artifact schema、DB integrity、duplicate UID/event、state hashを検証する。
10. 次回用seedをexportし、ローカルtest restore後のstate hash一致を確認する。
11. immutable release assetをuploadして再download・再検証する。
12. 再downloadしたassetとmanifestの一致を検証し、次回の列挙対象として確定する。
13. 公開許可段階では、seed更新成功後にだけ最小public artifactをPages artifactへ含める。

Phase 2A失敗は既存Coreランキングの生成を壊してはならない。ただし失敗したPhase 2A artifactや
新seedは公開・昇格せず、直前の成功seedと直前の公開What Changedを保持する。

## 5. First Cutover Protocol

初回cutoverはscheduleやpush eventから開始してはならない。
`workflow_dispatch`の明示入力でのみ実行可能にする。

必須入力:

- `phase2a_mode=initialize`
- 承認済みcutover manifestのSHA-256
- `identity_epoch=phase2a-cutover-YYYY-MM-DD`
- expected market date
- operator confirmation

Cutover manifestには最低限、repository commit SHA、Core logic/config hash、scope name、
scope member hash/count、expected market date、market session index、全Phase 2A version、
`CLOSED`自動判定disabled、承認者確認時刻を記録する。

初回runでは次を確認する。

- Full Scopeを候補抽出より前に確定している。
- 価格履歴不足銘柄もscope status行として残る。
- 空ledger開始には`--initialize-cutover`と正しいmanifest hashの両方がある。
- MINT数、NO_SETUP数、AMBIGUOUS数、BOOTSTRAP_OBSERVED数を提示する。
- Core BREAKOUTから通常のBREAKOUT eventを遡及生成していない。
- 同じcutover commandの再実行はNO_OPとなり、mint/eventが増えない。

初回seedとrun-summaryの承認が済むまで、通常継続runを開始しない。

## 6. Production Shadow Gate

初回cutover後、最低3つの異なるmarket dateでproduction shadowを完走させる。
同日再試行は日数へ数えない。期間中はPhase 2A JSONをpublic/へ接続しない。

各runで次を比較・記録する。

- scope totalとstatus内訳
- input/config/scope hashes
- LINK/MINT/AMBIGUOUS/NO_SETUP内訳
- phase別件数
- event type別件数
- stale/missing/error/out-of-scopeによるphase不変件数
- duplicate mint/event 0
- same-day retryのNO_OP
- state hashのseed export/restore一致
- seed/archive sizeと実行時間
- Core/Validation/Experimental/Research/Morning Brief Phase 1の出力不変

3日分のうち1日でもseed authority、identity continuity、atomicity、scope completenessに
説明不能な差異があればpublic consumer接続へ進まない。

## 7. Public Consumer Policy

Production shadow gate承認後だけ、以下のread-only artifactを
`public/dashboard/briefing/state/`へ追加できる。

- `run-summary.json`
- `latest-state.json`
- `recent-events.json`
- 公開contractを示す`index.json`

`rejections.json`、operational DB、seed、failed-run manifest、内部path、stack traceは公開しない。
Public artifactはJSON Schema検証済みのCOMMITTED runだけを対象とする。

初回公開では既存UI、ランキングscore、Core consensus、Morning Brief Phase 1 schemaへ
必須fieldを追加しない。What Changed consumerは新pathをoptionalに読む。取得失敗、PARTIAL、
schema mismatchの場合は「更新なし」と断定せず、`利用不可 / 前回成功版`を区別する。

公開後のUI統合や自然言語説明は、このGoalの必須範囲ではない。必要なら別の
Morning Brief Phase 2A Consumer Goalで扱う。

## 8. Required Code and Configuration Changes

実装候補は次に限定する。

- Phase 2A production runnerまたは既存shadow runnerの安全なwrapper。
- Release seed download/upload/verify helper。
- Cutover manifest JSON Schemaとsample。
- Public `index.json` contract/schema。
- `.github/workflows/update-dashboard.yml`の段階的な接続。
- Cutover、通常継続、seed欠損、rollback、公開除外を検証するpytest。
- Operator runbook、deployment result、rollback runbook。

既存State MachineのT01–T26、`smt1`閾値、UID形式、schemaの意味を変更しない。
変更が必要になった場合はCutover実装を停止し、Design Amendmentへ戻る。

## 9. Required Automated Tests

最低限、次を自動化する。

1. schedule/pushからinitializeできない。
2. manual initializeでもmanifest hash不一致ならmint 0。
3. 最新適格seedの欠損・破損・期限切れ・schema/version/lineage不一致でmint 0。
4. 正常seed restore後の次日runで既存setupがLINKされる。
5. 同日同inputはNO_OP、同日異inputはconflict/quarantine。
6. out-of-order inputはlive state不変。
7. seed upload失敗時に不完全assetを次回選択しない。
8. immutable asset追加前後のrollback可能性。
9. 公開対象にseed、DB、rejections、秘密値、absolute pathが含まれない。
10. PARTIAL/degraded runの公開可否が`publish_eligibility`どおり。
11. Phase 2A失敗時も既存Core Pages artifactが前回仕様で生成できる。
12. `engine.state_machine`のdatabase isolation testが継続passする。
13. 全体pytestが248件未満へ減らず全件passする。
14. fixed-input regressionがpassする。

Workflow YAMLについても、event別権限、initialize gate、seed昇格順序、公開除外を
静的testまたはfixtureで固定する。

## 10. Security and Permissions

- 通常test/analyze jobは`contents: read`を維持する。
- Release assetを書き込むstep/jobだけ`contents: write`を付与する。
- Pages権限は既存deploy jobだけに限定する。
- Fork由来workflow、pull_request、未承認branchからseed authorityを書き換えない。
- `GITHUB_TOKEN`、API key、temporary URL、署名情報をartifact/logへ出さない。
- Downloadしたarchiveはpath traversal、symlink、想定外file、size上限を検査する。
- Release asset名、tag、lineage、dateは固定形式で検証し、shell展開へ未検証値を渡さない。

## 11. Rollback Plan

Rollbackは削除やDB上書きではなく、次の順序で行う。

1. Phase 2A workflow stepとpublic artifact昇格をfeature flagで停止する。
2. Coreの既存分析・Pages公開は継続する。
3. 最後の正常seedとその1世代前をdownloadして検証する。
4. 問題run以後をliveへmergeせず、明示的なrecovery rehearsalを別DBで行う。
5. Human review後に選択したseedを次回runへ明示指定する。
6. 同一market dateの異input競合がある場合は通常継続せず、replay lineageで診断する。
7. 原因、影響run、選択seed、state hash、再開日をrollback manifestへ記録する。

State Machine versionやidentity ruleに原因がある場合、旧seedへ戻して同じversionを再実行せず、
新version/lineageを設計するまで停止する。

## 12. Stopping Conditions

次のいずれかが起きたらcutoverまたは公開を停止する。

1. 公開先remote/repository/branchを一意に確認できない。
2. dirty worktreeから承認対象だけを安全に分離できない。
3. Release seed authorityの公開可否が未承認。
4. Full Scope、market session index、expected market dateを同一Core runから取得できない。
5. Seed検証失敗時に全件MINTする経路が残る。
6. Product DBまたは`public/`内へoperational stateを書き込む必要が生じる。
7. 3日分production shadowでidentity/eventの説明不能な変動がある。
8. Duplicate mint/event、orphan row、foreign key error、state hash不一致が1件でも出る。
9. Phase 2A導入で既存Coreまたはfixed-input出力が変わる。
10. Release/Pages更新の権限を必要最小限へ分離できない。
11. GitHub-hosted runnerの保存量、実行時間、asset制限が実測で運用不能。
12. `sm1`/`smt1`/`csu1`の再設計が必要になる。

## 13. Deliverables

- Cutover manifest schema、承認済みmanifest、SHA-256。
- Durable seed helperとtests。
- Production runner/wrapperとtests。
- 段階的workflow接続とevent/permission tests。
- 初回cutover report。
- 3 market dates分のproduction shadow report。
- Seed authority inventoryと5世代rollback evidence。
- Public artifact contract/schema（公開を承認した場合）。
- Deployment result report。
- Rollback/recovery runbook。
- Cutover固有changed-file manifest。
- 実行command、exit code、Actions run URL、Pages URL、公開JSON検証結果。

## 14. Acceptance Criteria

次をすべて満たした場合だけCutover / DeploymentをCOMPLETEとする。

1. 承認済みrepository commitだけからworkflowが実行される。
2. Phase 2A以外のdirty変更を誤ってcommit/pushしていない。
3. Explicit initialize以外から空ledgerが開始されない。
4. Remote seed authorityから正常restoreし、次日continuityが維持される。
5. Seed異常時のmint 0と直前の検証済みasset不変が証明される。
6. 初回cutover retryがNO_OPでduplicate mint/event 0。
7. 最低3 market datesのproduction shadowが成功する。
8. 全runでSQLite integrity、foreign key、artifact schema、state hashが正常。
9. 5世代以上のrollback seedが検証可能。
10. 全pytest 248件以上とfixed-input regressionがpass。
11. Existing Core/Validation/Experimental/Research/Morning Brief Phase 1契約が不変。
12. State Machine失敗がCore Pagesの可用性を奪わない。
13. Public consumerを有効にした場合、公開対象が許可4 JSONだけである。
14. Seed、DB、rejection、秘密値、内部pathがPagesへ出ていない。
15. 本番Actions runとPages上のJSONを外部URLから再確認できる。
16. Feature flag停止と過去seed選択によるrollback rehearsalが成功する。
17. Known limitationsとして自動`CLOSED`未接続を明示し続ける。

## 15. Human Review Gates

### Gate A — Implementation前

- Release assetをdurable seed authorityにすること。
- Seed metadataがpublic repositoryのRelease上で閲覧可能になること。
- Public consumerは3日shadow後に別途昇格すること。
- Cutover identity epochとmanifest内容。

### Gate B — First cutover前

- staged diffとcommit SHA。
- 248件以上のpytestとfixed-input結果。
- workflow permissionsとinitialize gate。
- manifest SHA-256。

### Gate C — Public consumer前

- 3 market dates分のshadow比較。
- seed continuity、event、quality、rollback evidence。
- 公開JSON sampleと情報露出監査。

## 16. Explicitly Out of Scope

- Coreランキング・6手法・売買シナリオ・閾値の変更。
- `smt1` sensitivity tuning。
- 旧964 setupのretroactive identity backfill。
- 自動`CLOSED`用の上場廃止master接続。
- Phase 2B How Fresh、Phase 3 outcome tracking。
- What Changedの自然言語生成、ChatGPT自動呼出し。
- UIの大幅変更。
- 証券注文、自動売買、通知。
- Full operational DBや株価履歴の公開。

## 17. Completion Report

完了時は、単なる成功要約ではなく次の実測証拠を残す。

- 実際のpytest/fixed-inputログと件数。
- Phase 2A changed/staged/committed path一覧。
- GitHub Actions run URL、commit SHA、event、入力mode。
- 初回runと3日shadowのscope/quality/identity/state/event内訳。
- Seed asset名、hash、schema、row count、restore後state hash。
- NO_OP、corrupt seed、missing seed、upload failure、rollback rehearsalの結果。
- Pages deploy URLと公開4 JSONのschema検証。
- Pagesにseed/DB/rejections/secret/internal pathがないことの監査結果。
- Existing product contract不変の証拠。
- 残る制約と次のMorning Brief Phase 2A Consumer Goal候補。

## 18. Execution Authorization

この文書はCutover / Deploymentの実装・実行条件を定義する。文書作成だけを根拠に、
workflow変更、remote設定、commit、push、Release作成、Actions実行、Pages公開は行わない。
Human Review Gate Aの回答と、このGoalを実行する明示指示を受けてから実装へ進む。
