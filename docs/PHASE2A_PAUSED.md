# Phase 2A pause record

Status: **PAUSED** (2026-09-13)

Phase 2A の state machine / setup identity / release seed による厳密な継続追跡は、削除せず再開可能な状態で凍結する。日々の What Changed は当面、銘柄コード単位の軽量スナップショット比較を利用する。

## 保存場所と到達点

- Archive branch: `codex/phase2a-cutover`（pause archive commit: `4fcea92`）
- First production cutover commit: `699d47de09eb5abfac9528305de7ee0b1ff98548`
- Release authority: `phase2a-state-v1`
- 初回 cutover、Release asset 再取得、SQLite 整合性検証まで完了
- FORMING への MINT が過多（初回811状態銘柄中745件）と判明し、入口条件の再設計と migration / activation 設計まで進行

公開済みReleaseとseedは履歴・再開材料として残す。公開済みassetは削除せず、Phase 2Aの日次production shadowだけを停止する。手動実行口は誤作動防止の明示フラグ付きで残す。

## 再開時の推奨読書順

1. `docs/PHASE2A_STATE_MACHINE_DESIGN.md`
2. `docs/PHASE2A_STATE_MACHINE_IMPLEMENTATION_GOAL.md`
3. `docs/PHASE2A_CUTOVER_DEPLOYMENT_GOAL.md`
4. archive branch の `docs/PHASE2A_FORMING_MINT_GATE_DESIGN.md`
5. archive branch の `docs/PHASE2A_FORMING_MINT_GATE_DESIGN_RESULT.md`
6. archive branch の `docs/PHASE2A_FORMING_MINT_GATE_MIGRATION_ACTIVATION_GOAL.md`

再開時は、公開済みseedを無条件に継続せず、FORMING MINT gateのactivation方針とseed互換性を先に再承認すること。

## 当面の代替方式

Coreの `snapshot.json` から銘柄コード・名称・state・rankだけを日付別に保存し、直前の市場日と単純比較する。これはsetupの同一性やPivot継続性を証明するものではなく、「同じ銘柄コードの表示状態・順位が前回からどう変わったか」を示す参考情報に限定する。
