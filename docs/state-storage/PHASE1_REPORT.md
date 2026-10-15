# JSL State保存改善：Phase 1検証報告

2026-10-15。対象はPhase 1のみ。PR: [#3](https://github.com/takina6-source/japan-swing-lens/pull/3)。main・本番Pages・現行State writer・Storage Metrics判定式は変更していない。Phase 2の実装・切替は未実施。

## 対象世代と独立バックアップ

[既存の公開Run 38639189826](https://github.com/takina6-source/japan-swing-lens/actions/runs/38639189826)、commit `be9d1e565b90bc98b06019f78e5eb44a3a0a80ba`、Pages artifact `11931417857` を固定した。ZIPのSHA-256は `c4e6cc60b8ae0485f74f5b2cfccb15dcaf01b2a7cffd2de41793f74c32d826a3`。展開した同一artifact内の2,036ファイルを計測し、3系統もこのartifactから取得した。別時刻のStateを比較していない。

Core snapshot市場日は2026-10-14、Research index生成時刻は2026-10-15 06:51:32 JST。Researchのbenchmark基準進捗は20/90、index内のbenchmark最新市場日は10月13日であり、Core市場日とは一致しない。この差を改修で補正・上書きしていない。Stateの観測日とRun実行日も別に記録した。

[専用Release](https://github.com/takina6-source/japan-swing-lens/releases/tag/jsl-state-backup-run-38639189826) に3系統を保存した。既存のPhase 2A用Releaseとは別。元ファイル名、元バイト数、元SHA、全テーブル件数・IDダイジェスト、Version一覧、対象市場日、取得時刻、圧縮ファイルのSHAとサイズをmanifestへ収録した。

[バックアップ実行 38712441106](https://github.com/takina6-source/japan-swing-lens/actions/runs/38712441106) は、固定artifactのSHAとState SHA確認→アーカイブ作成→3系統復元→Release保存→Releaseから再ダウンロード→アーカイブSHA照合→再復元まで成功。Pagesの権限・environment・publish jobは持たない。利用者向けアーカイブとMacでの照合・復元手順は [PHASE2_RUNBOOK.md](PHASE2_RUNBOOK.md) に記載。

利用者によるローカル保管・照合の完了確認はまだ受けていない。Phase 2前には最新世代を再取得し、新しいローカル保管確認も必要。

## 調査と変更

| ファイル | 変更・確認内容 |
|---|---|
| `engine/state_io.py` | 共通gzip、SHA、厳格JSON、構造・必須フィールド・重複ID・参照・Version検証、新旧読込、キャッシュとのID包含確認、全テーブルの原子的取込 |
| `engine/validation.py` | Seedのみ共通readerへ接続。writer・計算は変更なし |
| `engine/experimental/validation.py` | 同上 |
| `engine/research/pipeline.py` | 同上。既存Metrics・研究処理は変更なし |
| `engine/state_backup.py`、`scripts/state_backup.py` | 3系統バックアップ作成・全検証・新規ディレクトリへの復元。既存アーカイブと復元先を上書きしない |
| `scripts/state_replay.py` | 固定された同じ入力を旧checkoutと提案checkoutで再取込・再出力し、研究結果とJSONハッシュを比較 |
| `scripts/state_capacity.py` | 不変artifact、元StateのSHAを固定した容量測定・仮定別試算。公開処理なし |
| `tests/test_state_io.py`、`tests/test_state_research.py` | 可逆性、通信/破損/混在/巻戻り、取込rollback、バックアップ、実研究処理の連続2回更新、workflow隔離 |
| `.github/workflows/state-storage-phase1.yml` | pull_request専用のオフラインpytest・固定入力回帰。読み取り権限のみ |
| `.github/workflows/state-storage-backup-phase1.yml` | 指定ブランチの固定backup request変更時のみ。独立Release保存・再取得検証。mainへのpushでは実行されない |
| `docs/state-storage/` | 固定世代要求、証跡、報告、Phase 2・復旧計画 |

Databaseのimport/export、Seed、`engine/research/storage.py`、`engine/performance_repair.py`、`scripts/export_web.py`、既存 `update-dashboard.yml`、Actions Cache、表示用JSON/CSV、UI、既存履歴圧縮、Corporate Action処理、既存テストを調査した。既存Database APIは `INSERT OR IGNORE`、Researchは一部履歴をreplaceする。新readerは既存APIを同一接続のトランザクション内で呼び、後続テーブルの失敗による部分取込をrollbackする。SQLiteスキーマ初期化はトランザクション前に行う。

既存のSeedは広い例外処理で失敗をFalseに変換し、呼出側もFalseを検査していなかった。提案ではURLがある取得に失敗すれば例外で停止する。URL未設定の初期構築のみFalseで継続する。3系統のうち先の系統が成功して後が失敗しても分析・公開へ進まない。次の復旧試行はIDを保持したまま冪等に取り込める。

gzipはlevel 9、mtime 0、filename空文字。既存JSONを再シリアライズせず元バイト列を圧縮し、同じ実行環境で同一結果になることをテストした。異なるzlibバージョンを跨いだ圧縮バイト列の同一性は保証対象外だが、展開後のSHAはどの環境でも元と一致する必要がある。

gzipの404だけがlegacy形式の探索を許す。タイムアウト、切断、HTTP 500、gzip破損、manifest欠落、不一致ではfallbackしない。gzipと旧JSONが共存する場合、同じ元SHAであることを確認し警告する。異なる世代なら停止する。legacyではindexを前後に取得し、世代変更と公開件数との不一致を検出する。キャッシュにあるIDが欠ける世代も拒否する。

## 同一データの容量実測

1 MiB = 1,048,576 bytes。Pagesは展開後の公開ファイルの合計であり、artifact ZIPの転送サイズではない。

| 対象 | 元bytes | gzip bytes | 元MiB | gzip MiB |
|---|---:|---:|---:|---:|
| Validation | 221,104,200 | 20,877,534 | 210.86 | 19.91 |
| Experimental | 245,438,519 | 24,442,773 | 234.07 | 23.31 |
| Research | 75,984,964 | 9,139,116 | 72.46 | 8.72 |
| State合計 | 542,527,683 | 54,459,423 | 517.39 | 51.94 |

- State合計削減率：**89.9619%**。80%目標を試験で満たす。
- Pages公開対象全体：**888,039,338 bytes = 846.90 MiB**。
- Stateだけをgzipへ置換し、reader必須の3つのmanifest計5,970 bytesを追加した試算：**399,977,048 bytes = 381.45 MiB**。
- 全体削減率：**54.9595%**。40%目標を試験で満たす。本番の公開容量が減ったという意味ではない。
- 非Stateデータは約329.51 MiB残る。大きい表示データはExperimental control_performance JSON約45.57 MiB、Validation control_performance JSON約38.86 MiB、Controls JSON/CSV、Signal History等。これらはUI/表示・集計用であり、削除していない。
- 単なる中間ファイルと判断できないJSON/CSVを重複扱いで削除していない。全ファイル内訳の大きい順は [capacity.json](evidence/capacity.json) に収録。

[GitHub公式のPages制限](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) は公開サイト1 GB。単位解釈で余裕を過大評価しないよう、この報告は運用上限を **1,000,000,000 bytes = 953.67 MiB** と保守的に置く。1 GiBを上限にした計算ではない。

現在の使用率は88.804%、残106.77 MiB。圧縮置換試算後は39.998%、残572.23 MiB。残70営業日を満たすために許容できる平均全体成長は、現状約1.53 MiB/営業日、置換後約8.17 MiB/営業日。ただし将来の実成長がこの値以内である保証はない。

## 成長履歴・予測・警告

完全なPages実測は1世代のみ。Pages全体、gzip State実容量について、前回差分・7/30日実測成長・到達営業日を確定できない。Researchの既存テレメトリをPages全体の履歴として扱っていない。

Researchの論理容量推計には9月9日〜10月15日の28観測がある。

| 期間（Run日） | 観測数 | Research論理容量差分 | 平日数を使った参考ペース |
|---|---:|---:|---:|
| 10月8日〜15日（7暦日） | 6 | 10.92 MiB | 2.185 MiB/平日 |
| 9月15日〜10月15日（30暦日） | 24 | 54.89 MiB | 2.495 MiB/平日 |
| 最終Runの前回差分 | 2点 | 4.874 MiB | 1 Run分。営業日平均ではない |

Run日は市場日ではなく、日本市場の休日調整もしていない。**平日は営業日の代理であり、実測30営業日平均ではない**。既存metricはJSONを別の構造でシリアライズした推計で、公開Research State実ファイルとも一致しない。さらに期間内の既存履歴圧縮等による段差が含まれ、信頼性は低い。

全体の無圧縮成長を2/5/10 MiB/営業日、その50%または100%がState由来、将来の圧縮率を今回の合計と同じと仮定した感度試算を [capacity.json](evidence/capacity.json) に収録した。観測から推定した成長率ではない。

| 仮定：無圧縮全体成長・State比率 | 現状の上限到達まで | 圧縮後の上限到達まで | 圧縮後70営業日 | 圧縮後180営業日 |
|---|---:|---:|---:|---:|
| 2 MiB/日・50% | 53日 | 520日 | 458.47 MiB | 579.52 MiB |
| 5 MiB/日・50% | 21日 | 208日 | 574.01 MiB | 876.62 MiB |
| 10 MiB/日・50% | 11日 | 104日 | 766.58 MiB | 1,371.79 MiB |
| 5 MiB/日・100% | 21日 | 1,140日 | 416.58 MiB | 471.79 MiB |

残70営業日はこの試算範囲で容量内だが、180営業日では再超過する場合がある。非State側が増えるほど効果は小さい。Phase 2の緊急度は高い。Phase 1レビュー後、実測履歴を取り始めて早期に準備する必要がある。現在の余裕106.77 MiBだけで70営業日を保証できない。

旧方式：Research論理推計の占有率>50%、180 Run相当予測>500 MiB、単日差分>5 MiBのいずれかでRED。YELLOWは占有率>=30%または予測>=250 MiB。それ以外GREEN。現在は占有率13.9566%、Research推計76.78 MiB、単日差分4.874 MiB、180倍予測954.05 MiBでRED。`rolling_30d_growth_mb` は単日差分×30であり、実測平均ではない。

新方式案は [runbook](PHASE2_RUNBOOK.md) に判定条件を記載した。主対象をPages全体の実バイトと予測へ変え、Research旧警告は別に維持する。同じ方式案を今回の元容量に適用すると **RED（使用率88.804% >= 85%）**、圧縮試算後は **YELLOW（使用率39.998%だが実測成長履歴不足）**。根拠のないGREENへ変更しない。旧Research警告は圧縮後も論理推計ではREDとして残る。改修前がREDでなくなる例外は発生していない。本番判定式の変更・閾値緩和は実施していない。

## バイト完全性・件数・ID

以下の元SHAと解凍後SHAは、それぞれ完全一致した。バイト比較では日時、並び、空白を含めて何も除外していない。

| 系統 | 元SHA-256 = 解凍後SHA-256 |
|---|---|
| Validation | `4c67b605b91d84047277f2708920ab17b4eae0682d83fb656d1df9213e055df9` |
| Experimental | `b98386273bd08bbd1fc961b0e07b973040b44c888904cf45fe3f52f8135ebbd2` |
| Research | `ac2be4d84b2a32fdb7a3b89b75b7626f22c088adacc67fc1618bb60773424200` |

| テーブル | 圧縮前 = 復元後 |
|---|---:|
| Validation signals / history | 1,432 / 21,215 |
| Validation controls / control_history | 33,012 / 463,304 |
| Experimental signals / history | 2,156 / 20,647 |
| Experimental controls / control_history | 60,368 / 577,306 |
| Research hypotheses / families | 4 / 1 |
| Research events / subjects | 4,195 / 3,325 |
| Research control_members / control_history | 17,836 / 124,065 |
| Research subject_history / checkpoint_evaluations | 14,469 / 1 |
| Research results / family_results / telemetry | 0 / 0 / 28 |

全テーブルで複合主キーの集合・件数を比較し、重複と参照欠落を検査した。ID一覧を巨大なログへ転記せず、ソートしたID集合のSHA-256を [byte-integrity.json](evidence/byte-integrity.json) に記録している。元バイト数・展開後バイト数も同ファイルで確認できる。

## 研究結果の再現性

上記同一RunのStateを、改修前checkoutの旧Seed readerと、提案checkoutのgzip readerで別々の新規DBへ取り込み、既存export処理で再計算した。**29/29 JSON artifactのハッシュが一致**。全ハッシュ・比較結果は [baseline](evidence/replay-baseline.json)、[gzip](evidence/replay-gzip.json)、[比較ログ](evidence/replay-comparison.txt) に収録。

研究再計算の比較ではDBが再付与する `created_at` とexportの `generated_at` のみ除外した。数値は丸めていない。State配列はDB行順序の差を除くため全行をソートして比較した。これと別に、生Stateの全バイト一致を上述のSHAで証明している。

| 確認項目 | 改修前 = gzip読込後 |
|---|---|
| H1–H4 | 4つすべてのdefinition hash、freeze情報、hypothesesの進捗・計算結果、checkpointsが一致 |
| LIVE_FORWARD events / subjects | 2,702 / 1,961 |
| LIVE_FORWARDかつHOLDOUT events / subjects | 2,634 / 1,903 |
| Formal results | 現在0件のまま。正式結論が存在すると解釈しない |
| Coreランキング | 既存固定入力回帰のrankingを含む11 artifactが一致 |
| Corporate Action・異常値 | 処理コード変更なし。既存補正・異常値テスト成功。輸送後の出力も旧コードと一致 |
| Morning Brief / What Changed / Investment Committee | 関連コード・UI変更なし。既存回帰テストを含む全テスト成功 |

探索セグメントは、既存のValidation performance出力に対して、initial_aligned_count=5、Momentum>=95、T+5有効値を持つ行、**現行strategy/threshold/schema Version**に限定した。Versionを混ぜず、WATCH/BREAKOUT別とID集合も比較した。参照の44件を固定期待値にしていない。

| 条件 | n（前/後） | T+5平均%（前=後） | Matched超過%（前=後） |
|---|---:|---:|---:|
| 5/5 × Momentum95+ | 48 / 48 | 3.239888288675148 | 1.759319932092566 |
| BREAKOUT WATCH | 37 / 37 | 2.5008925637235366 | 1.073729336514306 |
| BREAKOUT | 11 / 11 | 5.725601181694202 | 4.065397389946713 |

この世代の現行VersionではMatched有効数もそれぞれ48/37/11。計算式・抽出元・研究ロジックを変更していない。上記表示は検証用集計で、投資判断や研究仮説の新しい定義ではない。

## テスト・再現コマンド

macOS / Python 3.14：改修前275 passed、提案300 passed。Ubuntu / Python 3.12の初回 [PR CI 38712482579](https://github.com/takina6-source/japan-swing-lens/actions/runs/38712482579) では299 passed、固定入力回帰11 artifact成功。その後cold cacheの空gzip Seed拒否テストを追加し、最終CIでも再確認する。ログは [evidence](evidence/) に収録した。

```sh
python -m pytest -q
python scripts/fixed_input_regression.py
python scripts/state_backup.py verify --archive /path/to/jsl-state-backup-run-38639189826.zip
python scripts/state_backup.py restore --archive /path/to/jsl-state-backup-run-38639189826.zip --target /path/to/new-restored-directory
python scripts/state_capacity.py --pages /path/to/frozen-pages --pages-tar /path/to/artifact.tar --backup-manifest /path/to/backup-manifest.json --output /path/to/capacity.json
```

同一入力の研究再現には、先に指定RunのPages artifactを展開し、改修前checkoutを別ディレクトリに用意する。`state_replay.py` は提案checkoutにある同じスクリプトを使い、`PYTHONPATH` だけ旧checkoutに向ける。出力には実際にimportしたvalidationモジュールのSHAも記録する。

```sh
PYTHONPATH=/path/to/base-checkout python scripts/state_replay.py --pages /path/to/frozen-pages --report /path/to/replay-baseline.json
python scripts/state_replay.py --pages /path/to/frozen-pages --compressed --report /path/to/replay-gzip.json
```

異常系は404、タイムアウト、接続/途中切断、HTTP 500、gzip切断・破損、JSON破損・重複キー・非有限値、欠落section、schema/ID不一致、重複ID、孤立参照、manifest欠落/SHA不一致、混在世代、legacy index件数不一致、取得中の世代変更、古いID集合への巻戻り、取込途中のSQLite失敗、backup改変・上書き拒否を検証した。

連続更新は、Core Seedの2回追加取込と、固定価格fixtureからのResearch実処理を2回継続し、gzip経由と旧JSON経由でH1–H4/イベント/チェックポイントが一致することをテストした。ライブ価格を使う本番相当の連続日次dry-runはPhase 2の未実施項目。

## 残存リスクと承認事項

- legacy形式には完全なchecksum・全テーブル件数・親世代台帳がない。indexとキャッシュIDで欠落を検出するが、cold cacheで古くかつ内部整合したlegacy世代の提示や、公開indexにないControl Historyだけの削減を完全には証明できない。今回の評価・バックアップは不変artifactと固定SHAで補っている。Phase 2の世代台帳と最新バックアップの下限確認が必要。
- `describe` は既知section、必須フィールド、ID、参照、Research Versionを検査するが、将来の全schema evolutionに対する万能なバリデータではない。未対応section/versionでは停止し、互換性を検討する。
- 圧縮/JSON parse/ID集合はメモリを使う。今回macOSとGitHub hosted runnerの実Stateで成功した。今後も増えるため長期的な分離・ストリーミングの検討が必要。
- 数値・研究定義の変更はないが、取得失敗時に無視せず停止するため、障害時は公開が中止される。これは既存データを空Stateで置き換えないための意図した変更。
- 公開全体・圧縮後容量の履歴不足で、信頼できる到達営業日は未確定。試算を実測と誤認しない。旧REDを隠さない。
- Phase 1のコードはPR上であり、本番Seedの従来リスクを解消するには人間のレビューが必要。本番への自動マージ・デプロイはしていない。
- 利用者のローカル保管確認、Phase 1承認、別途Phase 2のPRレビュー、最新世代再バックアップ、非公開dry-run、明示的なmainマージ/本番切替承認を待つ。

ロールバックと長期保存候補は [PHASE2_RUNBOOK.md](PHASE2_RUNBOOK.md) に記載した。研究履歴・LIVE_FORWARD・HOLDOUTの削除や初期化、数値精度低下、CoreやH1–H4の変更、閾値緩和による回避は行っていない。
