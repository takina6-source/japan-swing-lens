# Japan Swing Lens — Stable Setup Identity Design Goal

## 0. Goal

Japan Swing Lensにおける現行`setup_id`を監査し、銘柄コードを含み、同一の投資セットアップを複数日にわたって安定して識別できる、新しいsetup identity契約を設計する。

このGoalは**設計専用**である。設計結果を人間がレビューし、明示的に承認するまでPhase 2A State Machine Designへ進まない。

このGoalでは、状態遷移、`current_phase`、FAILED後の再待機・再突破条件、日数・価格・回復率などの投資閾値を設計しない。それらはsetup identity確定後の別Goal「Phase 2A State Machine Design Goal」で扱う。

## 1. 必須の実測前提

2026-09-11のRuntime Verificationで、2026-09-03のローカルdetailと2026-09-10の最新公開detailに共通する964銘柄を比較した結果、次を確認した。

| 項目 | 件数 |
|---|---:|
| 比較対象 | 964 |
| 総合setup_id維持 | 0 |
| 総合setup_id変更 | 964 |
| consensus state維持 | 711 |
| consensus state変更 | 253 |

現行の総合IDは、`code + ソート済み手法setup_id集合`を材料に生成される。構成手法IDやPivot由来の値が変化すると総合IDも変わるため、状態が維持された711銘柄を含めて全件でIDが変化した。

現行の手法setup IDは銘柄コードを含まず、`setup_registry`の4,850行に対して4,626種類しかなく、複数銘柄間の衝突も実測された。現行`signal_id = setup_id + strategy_version`にも、この不安定性が伝播する。

したがって、前回監査の`DESIGN_REQUIRED_PARTIAL`より強く、次を設計開始時の確定事項とする。

- 現行の総合`setup_id`を同一setupの継続主キーとして再利用しない。
- 現行の手法setup IDを銘柄横断で一意なIDとして扱わない。
- 既存IDをハッシュ長だけ変更して問題解決としない。
- 新しいidentityは銘柄コードを必須構成要素または必須namespaceとして持つ。
- 日々変動する手法集合、state、現在値、毎日再計算されるPivot値だけをidentityの直接材料にしない。

根拠レポート: `docs/PHASE2A_RUNTIME_VERIFICATION.md`

## 2. 設計目的

新しいidentity契約は、最低限次を実現する。

1. 同一銘柄・同一setupの観測を複数run・複数日にわたり同じidentityへ結合できる。
2. 同じ価格・日付・Pivotを持つ別銘柄が衝突しない。
3. 同一setupを説明する手法が増減しても、総合identityを無条件に作り直さない。
4. Pivot等の属性更新を「identity変更」ではなく、必要に応じて「同一identity内のrevision」として表現できる。
5. 本当に別のsetupである場合は、新しいidentityを発行できる。
6. identity発行理由と根拠を後から監査できる。
7. Core、Validation、Control、Research、Morning Briefが同じ意味で参照できる。
8. Experimental固有setupをCoreと誤って同一視しない。
9. 欠測、部分run、scope離脱、logic version変更があっても、identityを暗黙に継続・分断しない。

## 3. Absolute Principles

1. identityは投資判断や状態ではなく、観測を同じsetupへ帰属させるための参照キーとする。
2. `BREAKOUT`、`WATCH`、`FAILED`等のstateをidentityそのものに埋め込まない。
3. identityの同一性と、setupが現在activeかどうかを分離する。
4. identityの同一性と、Pivot・構成手法・根拠値のrevisionを分離する。
5. ID文字列の一致だけでなく、発行・継続・改訂・終了の判断根拠を保存できる契約にする。
6. 欠損を推測で補い、同一setupまたは新setupと断定しない。判定不能を表現できるようにする。
7. 過去の現行setup_idを、根拠なく新identityへ一括統合しない。
8. 新しい外部API、市場データ、LLM判断をidentity生成の必須依存にしない。
9. Coreランキング、既存手法判定、投資閾値を変更しない。
10. 新identityの採番方式は、短縮ハッシュ衝突時の挙動まで定義する。

## 4. Scope

### In Scope

- 現行Core手法setup ID、総合setup ID、signal IDの生成・保存・参照経路の完全な依存関係図。
- setup identityの用語、責務、階層、namespaceの定義。
- 銘柄コードを含むグローバル一意性契約。
- identityの新規発行、継続、revision、supersede、判定不能の意味論。
- 手法別setupと総合setupの関係。
- 構成手法の追加・離脱時のidentity維持方針。
- Pivot属性変更とidentity変更の境界。
- deterministic ID、永続採番ID、両者のhybrid案の比較と採用判断。
- ID文字列表現、canonical input、正規化、hash/UUID、collision処理、schema version。
- 既存DB・JSON・Validation・Control・Research・Committee・Morning Briefとの参照契約。
- 旧IDとの互換、alias、migration、rollback方針。
- 固定入力と合成ケースによる設計検証。
- Phase 2Aへ渡す、確定済みidentity契約と未決事項。

### Out of Scope

- Phase 2A State Machineの設計・実装。
- `current_phase`、`previous_state`、`active_signal_id`の選択規則。
- WATCH→BREAKOUT、BREAKOUT→FAILED、FAILED→WATCH等の状態遷移規則。
- `FAILED -> RETRY_WATCH`、再BREAKOUT、回復、失効に使う価格率・日数・出来高等の閾値。
- 売買シグナル、ランキング、entry/stop/targetの変更。
- 日次観測Snapshot schema全体の設計。identity参照に不可欠な最小項目だけを申し送る。
- 本番DB migration、既存データ書換え、production code変更。
- GitHub Actions変更、git push、公開、本番run。
- UI・Morning Brief表示・通知・LLM文章生成。
- Experimental setup identityの全面再設計。Coreとのnamespace境界と参照方法だけを定義する。

## 5. 用語を分離して定義する

設計書では、少なくとも次の概念を一つの`setup_id`へ押し込めず、別々に定義する。最終名称は設計で確定してよい。

| 概念 | 必須の意味 |
|---|---|
| Setup Identity | 複数日の観測が同一setupへ属することを示す安定キー |
| Setup Revision | 同一identity内でPivotや根拠属性が変化した版 |
| Strategy Setup Reference | 各手法が認識した構造と総合setupの関連 |
| Observation ID | 特定run・銘柄・分析日の観測を一意に指すキー |
| Signal/Event ID | BREAKOUT等の出来事を一意に指すキー。setup identityとは別 |
| Legacy ID | 現行の手法/総合setup_id。互換参照用であり新主キーではない |

`active`、`phase`、`retry`はこのGoalでidentityの属性として定義しない。

## 6. Identity Equivalence Contract

設計成果物は「何が同じなら同一setupか」を、実装可能な入力契約として明文化する。ただし状態遷移閾値を決めてはならない。

最低限、次のケースに対する帰属結果と理由を定義する。

1. 同じ銘柄・同じ構造で日次再計算した場合。
2. stateだけが変わり、構造の参照関係は同じ場合。
3. 同じ構造のPivot価格が再計算で更新された場合。
4. 構成手法が1つ追加または離脱した場合。
5. 手法ごとのPivotが異なるが、総合的には同一の価格構造を参照する場合。
6. 既存setup観測が欠けた後に再び観測された場合。
7. scope外へ離脱後、再びscopeへ入った場合。
8. logic/strategy/threshold versionが変わった場合。
9. 同じ銘柄に独立した複数setup候補が同時に存在する場合。
10. 同じ銘柄で過去setupとは別の新しい価格構造が形成された場合。
11. 判断材料不足により同一か新規か確定できない場合。

この契約では、identity判断に使用するOBSERVED_FACTと、後続State Machineが決めるDERIVED_STATEを分離する。新setup境界に数値閾値が必要だと判明した場合、このGoalで勝手に数値を決めず、必要な入力・選択肢・影響を未決事項として人間レビューへ上げる。

## 7. Strategy-levelとConsensus-levelの契約

現行不具合の主因である「手法ID集合の変化で総合IDが変わる」を解消する。

設計書は最低限、次を決定する。

- Core総合setup identityを1つの永続entityとして持つか。
- 手法別setupを独立entityとして持ち、総合setupへ多対多で関連付けるか。
- 手法の加入・離脱をidentity変更ではなくmembership履歴として扱う条件。
- 同時に複数の総合setup候補がある場合の表現。
- primary Pivotの交代とsetup identityの関係。
- Strategy Setup Reference自体の一意性にcode、strategy namespace、versionをどう含めるか。
- CoreとExperimentalのID namespaceをどう分離するか。

総合IDを「その日に有効な手法IDの集合hash」だけで再構成する案は採用不可とする。

## 8. ID Format / Canonicalization

次を具体的に規定する。

- 人間可読prefixとnamespace。
- codeの正規化。4桁文字列を保持し、数値化による先頭ゼロ消失を防ぐ。
- ID schema version。
- canonical serializationと文字コード。
- immutableな採番入力と、revisionへ送る可変属性。
- hashアルゴリズムまたはUUID方式と選定理由。
- 短縮する場合のbit数、想定件数、衝突確率、衝突検出、再採番方法。
- 同じ入力から同じIDを再現すべき範囲。
- DB復元、再run、別環境での再現性。
- 秘密情報やローカルパスをID材料に含めないこと。

少なくとも、純粋なcontent hash方式、DBでの永続採番方式、hybrid方式を比較し、安定性・再現性・migration・並行run・collisionの観点から1案を採用する。

## 9. Persistence / Concurrency Contract

実装は行わないが、後続実装が曖昧にならない粒度で永続化契約を設計する。

- identity entity、revision、strategy membership、legacy aliasに必要なテーブルまたは論理schema。
- 主キー、unique制約、外部キー、index。
- 同じsetupを並行runが同時に発見した場合のidempotency。
- transaction境界と、途中失敗時に孤立IDを作らない方法。
- observation保存失敗時にidentityだけが確定するか否か。
- immutable項目と更新可能項目。
- audit metadata: created_at、created_by_run、identity_version、reason/evidence参照。
- hard deleteを避け、supersede/aliasで追跡可能にする方針。

新DB製品は導入しない。既存SQLiteと静的JSON公開の構成を前提にする。

## 10. Downstream Compatibility Matrix

次の利用箇所ごとに、現行ID、新identity、revision、event IDのどれを主参照にするかを表で確定する。

- `setup_registry`
- `signal_snapshots` / `signal_history`
- Validation signals
- Control group / control history
- Experimental snapshots / history
- Research event / validation subject
- Committee export
- Morning Brief Phase 1
- 将来のPhase 2A observation store

必須ルール:

- 既存`signal_id`を新setup identityと同義にしない。
- signal/eventは新setup identityを外部キーとして参照できる構造にする。
- ControlとResearchの既存結果を、ID変更だけで別setupへ付け替えない。
- Morning Brief Phase 1は現状setup ID非依存であり、このGoalで出力契約を変更しない。
- Experimental IDは独立namespaceを維持し、根拠なしにCore identityへ統合しない。

## 11. Legacy Migration / Rollback

設計成果物に、実行前提ではないmigration planを含める。

1. 現行IDを`legacy_setup_id`として保持する方法。
2. 新旧ID alias tableまたはmapping artifactの形式。
3. 1旧ID→複数新ID、複数旧ID→1新ID、対応不能の表現。
4. 既存signal/control/research行を自動移行できる条件。
5. 根拠不足の履歴を`UNRESOLVED_LEGACY_ID`として残す方法。
6. dual-write / shadow-read期間の要否。
7. 新設計が不適切だった場合に旧読取経路へ戻すrollback手順。
8. migration前後の件数・参照整合性・孤立行を検証する照合表。

過去データを都合よく完全復元できると仮定しない。移行不能な履歴は削除・推定統合せず明示する。

## 12. Design Verification

本番コード・DBを変更せず、保存済み入力の読み取りと合成fixtureで設計を検証する。必要なら一時ディレクトリ内の非production prototypeを使用してよいが、製品コードへ組み込まない。

### 12.1 実測再検証

- 9月3日と9月10日の共通964銘柄を再現可能な比較手順として固定する。
- 現行総合setup_idが964/964で変化する原因を、手法membership、手法setup ID、Pivot属性等へ分解する。
- consensus stateが同じ711銘柄について、新identity設計ならどの件数を同一setup候補として扱えるかを示す。
- 964件すべてを同一とすることを成功条件にしない。別setupと判断する場合は、銘柄別に機械可読な根拠分類を出す。
- 新設計でも同一性を決められない行は、推測せず`AMBIGUOUS`として件数と理由を出す。

### 12.2 必須fixture

- 同銘柄・同構造・同Pivot・翌日再計算。
- 同銘柄・同構造・Pivot revisionあり。
- 同銘柄・同構造・手法加入/離脱あり。
- 同銘柄・stateのみ変更。
- 同銘柄・明確に別構造。
- 同銘柄・複数setup同時存在。
- 別銘柄・同日・同価格・同Pivot。
- codeの文字列正規化境界。
- logic version変更。
- 欠測後の再観測。
- identity判定不能。
- 並行runによる同時発行。
- 意図的なhash collisionまたはcollision検出経路。
- legacy IDの1:1、1:N、N:1、unresolved mapping。

各fixtureに期待するidentity関係（same / different / ambiguous）、revision関係、理由コードを定義する。State MachineのphaseやFAILED後の閾値は期待値に含めない。

## 13. Deliverables

1. `docs/SETUP_ID_DESIGN.md`
   - 採用案、却下案、判断理由。
   - 用語、identity階層、同一性契約。
   - ID format、canonicalization、collision処理。
   - persistence/concurrency契約。
2. 現行ID依存関係図とDownstream Compatibility Matrix。
3. 論理DB schema / JSON参照例。migrationは実行しない。
4. Legacy Migration / Rollback Plan。
5. 固定fixture一覧と期待結果。
6. 964銘柄実測に対する新設計の分類結果。
7. Phase 2AへのIdentity Contract Handoff。
8. 未決事項と人間が選ぶ必要のある選択肢。

## 14. Acceptance Criteria

- 銘柄コードを含むグローバル一意なCore setup identityを新規定義している。
- identity、revision、strategy membership、observation、signal/event、legacy IDを区別している。
- 日々変動する手法ID集合だけで総合identityを生成しない。
- state変更だけではidentityを変更しない契約になっている。
- Pivot変更とidentity変更の境界、および曖昧時の扱いが定義されている。
- 同じ銘柄で複数setupが共存できる。
- 別銘柄の同一価格/Pivotで衝突しない。
- deterministic / persistent / hybridの比較があり、採用理由が説明されている。
- collision検出・並行run・idempotencyが定義されている。
- 既存Validation / Control / Researchの参照を壊さないmigration方針がある。
- 実測964銘柄を新契約で分類し、same / different / ambiguousの件数と理由を報告している。
- 現行964/964変化を解消できる根拠を示す一方、全件同一化を目的化していない。
- migration不能なlegacy履歴を捏造せず、unresolvedとして保持する。
- 状態遷移ルール、FAILED→RETRY_WATCH、再BREAKOUT、失効等の閾値を設計していない。
- product code、DB、workflow、公開成果物を変更していない。
- 人間レビュー用の明確なDecision Summaryがある。

## 15. Human Review Gate

このGoalの完了時点では、Phase 2A State Machine Design Goalを自動的に開始しない。

以下を人間へ提示し、明示的な承認を待つ。

1. 採用するidentity方式とID例。
2. same / different / ambiguousの境界。
3. Pivot revisionと新setupの境界。
4. 手法membership変更時の扱い。
5. 964銘柄実測の分類結果。
6. legacy migrationで失われるもの・確定できないもの。
7. Phase 2Aへ渡す確定契約と未決事項。

承認後にのみ、確定したsetup identityを前提として別文書のPhase 2A State Machine Design Goalを作成する。

## 16. 次の順序

1. 本Goalに基づくsetup identity設計。
2. 設計成果物と964銘柄分類結果の人間レビュー。
3. 必要ならsetup identity設計だけを修正・再検証。
4. setup identityの明示的承認。
5. 別GoalとしてPhase 2A State Machine Design Goalを作成。

この順序を統合・省略しない。
