# Phase 2A Limited Runtime Benchmark

実行日: 2026-09-12 JST
結果: **PASS WITH CAPACITY AMENDMENT**

## 1. Purpose and Boundary

Phase 2A State Machine Designの論理schemaを、固定約1,000 scope rowsの隔離SQLiteで実測した。

- 一時directoryだけを使用し、終了時に削除。
- productの`data/momentum.db`、`momentum.db`はopenしていない。
- network、workflow、公開サイトは使用・変更していない。
- 1 scenarioにつき1,000 scope rows（995 CURRENT、4 STALE、1 INSUFFICIENT）を10 transaction保存。
- journal modeは`DELETE`と`WAL`、`synchronous=FULL`。
- event率`0 / 10 / 25%`、Pivot revision率`0 / 15 / 50%`の全18組合せ。
- identity ledger 995件、初期Pivot/event/current stateを持つ固定seedから開始。

再現script: `scripts/benchmark_phase2a_storage.py`
固定結果: `docs/examples/phase2a-state-machine/runtime-benchmark-results.json`

## 2. Executive Result

| Check | Result |
|---|---|
| 18 scenario × 10 transaction | PASS |
| SQLite integrity | 全scenario `ok` |
| Foreign key error | 0 |
| Atomic failure rollback | 孤立行0 |
| Retry | 1回だけCOMMIT |
| Identical rerun | `NO_OP`、event/mint重複0 |
| Missing/invalid seed | fail closed、mint 0 |
| Seed export/import | PASS |
| Current state rebuild | hash一致 |
| Public 30-day artifact | 0.575MB、gzip 0.119MB |
| Original capacity plan | **FAIL: 258MB/年を83.46%超過** |

処理性能・整合性・復元性は問題ない。容量だけは設計時見積りを更新する必要がある。

## 3. Storage

### 3.1 Representative scenario

代表条件はWAL、event 10%、Pivot revision 15%。

| Metric | Result |
|---|---:|
| Seeded DB | 1.724MB |
| 10run incremental | 19.620MB |
| 1,000 scope rows / run | 1.962MB |
| 965銘柄 × 250run | **473.329MB/年** |
| Peak WAL + SHM | 7.593MB |
| Original plan | 258MB/年 |
| Difference | +83.46% |

全scenarioの年間換算rangeは435.482–539.931MB。journal modeはcheckpoint後の永続容量に影響せず、event/revision率が主な差を生んだ。

### 3.2 Why the estimate was low

代表DBのdrop-and-VACUUM隔離copyによる非加算のattribution estimateでは、次が大きかった。

| Object group | Approximate footprint after 10run |
|---|---:|
| Accepted observations + indexes | 11.227MB |
| Identity decisions + indexes | 5.620MB |
| Events + indexes | 2.392MB |
| Scope status + indexes | 2.384MB |
| Pivot revisions + indexes | 1.253MB |

事前見積りはSQLiteのTEXT UID、監査hash、JSON provenance、secondary index、page allocationを過小評価していた。full candidateを保存した結果ではない。

物理実装ではrun-level versionの正規化、enum/reasonのcompact化、不要な重複indexを検討できる。ただし削減量は実測するまでcapacity planへ織り込まない。現時点では473MBを代表値、435–540MBをplanning rangeとして採用する。

## 4. Transaction Timing

official passの全18scenario:

- p50: 22.055–30.446ms / 1,000 scope run。
- p95: 28.869–39.170ms / 1,000 scope run。
- 代表WAL scenario: p50 27.554ms、p95 34.252ms。

3回の完全実行で永続容量は一致した。OS cache等により代表timingはp50約28–48ms、p95約34–60msの変動があったため、これはhardware上の参考値でありproduction SLAではない。それでも日次約1,000銘柄に対して十分小さい。

## 5. Seed, Recovery, and Atomicity

代表10run DB（21.344MB）のroundtrip:

| Operation | Result |
|---|---:|
| SQLite backup export | 22.321ms |
| SHA-256 | 8.408ms |
| SQLite backup import | 23.165ms |
| integrity + foreign key check | 47.272ms / error 0 |
| current state rebuild | 13.563ms |
| state hash | before = after |

Failure injectionはevent insert後・current state更新前に例外を発生させた。

- identity、observation、decision、Pivot、event、stateは全rollback。
- orphan rowは全tableで0。
- 同じinputのretryは1回だけCOMMIT。
- さらに同一runを実行するとNO_OP。
- event idempotency key重複0、MINT rowは1件。

seed missing/hash mismatchは受理せず、MINT件数0。全件再採番fallbackが起きないことを確認した。

## 6. Public Artifact

995 latest states + event 10%/日 × 30日（3,000 events）のcompact JSON:

- Raw: 574,520 bytes（0.575MB）。
- gzip: 119,003 bytes（0.119MB）。

GitHub Pagesへ出すlatest/30-day artifactは十分小さい。473MB/年はprivate operational historyの見積りであり、スマホが毎回取得する量ではない。

## 7. Capacity and Retention Amendment

設計を次へ修正する。

1. Planning baseline: **473MB/年**。
2. Sensitivity envelope: **435–540MB/年**。
3. Hot operational DB: rolling 12か月を目標。
4. 古いrun/scope/observation/decisionは年単位のimmutable archiveへ移し、最低3年はarchiveを含めて参照可能にする。
5. identity ledger、current state、active Pivot/membership、lineageに必要なevent anchorはcompact durable seedとして無期限保持。
6. GitHub Actions cacheを正本にせず、full history DBを毎run seedとして配布しない。
7. Public artifactはlatest state + 30市場日eventを維持する。
8. final physical schemaで同じbenchmarkを再実行し、range外ならImplementation Reviewへ戻す。

圧縮率は今回測っていないため、archive容量から差し引かない。

## 8. Limitations

- 合成固定入力であり、実際のsetup率・event率・文字列分布とは異なる。
- current 995件すべてにaccepted observationとidentity decisionを持たせた保守的な上限寄りの測定。
- transactionはローカルApple Silicon上。GitHub Actions runnerのI/O性能は別途shadow runで確認する。
- schemaは設計契約を表現するbenchmark prototype。production migrationそのものではない。
- table attributionは`dbstat`が利用できない環境のため、tableを落としてVACUUMしたcopyとの差分。値は正確に加算できない。
- archive compression、実際のActions artifact upload/download時間は未測定。

## 9. Gate Result and Next Step

Limited Runtime Benchmarkは、runtime/integrity/recoveryについてPASS。元の258MB/年計画だけを棄却し、473MB/年と12か月hot retentionへ設計を改訂した。

Phase 2A product実装、DB migration、workflow、公開はまだ行っていない。次は本benchmarkを前提にした**Phase 2A Implementation Goalの作成**であり、別のユーザー指示を待つ。
