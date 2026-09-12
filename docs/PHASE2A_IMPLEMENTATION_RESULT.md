# Phase 2A State Machine Implementation Result

## 1. Outcome

**Local implementation: COMPLETE.**

**Production cutover / scheduled operation / public consumer: NOT READY and not
authorized by this Goal.**

The approved seven-phase event-centered state machine, stable identity
resolution, additive SQLite persistence, explicit shadow CLI, compact seed and
private artifacts are implemented without connecting them to the product
database, workflow, ranking, UI or Morning Brief.

## 2. Repository boundary

- Start HEAD: `9aa5fe332b3fd3b89457ab80fed00cd9daa7434c`
- End HEAD: `9aa5fe332b3fd3b89457ab80fed00cd9daa7434c`
- Dirty baseline before Phase 2A implementation: 1,067 porcelain paths.
- No commit, push, deployment or remote mutation was performed.
- Phase 2A changed paths are enumerated in `docs/PHASE2A_CHANGED_FILES.txt`.

The pre-existing worktree already contained changes to `app.py`,
`engine/database.py`, `.github/workflows/update-dashboard.yml` and many public
artifacts. They were preserved. Phase 2A changes are new isolated paths and do
not edit those files.

## 3. Implemented modules

| Module | Responsibility |
|---|---|
| `models.py` | Closed enums and immutable validated value objects |
| `config.py` | `sm1`, `smt1` and exact fixed thresholds |
| `guards.py` | Candidate, cross, failure, retry, expiry and Pivot guards |
| `ids.py` | Canonical JSON and deterministic full/short identity hashes |
| `identity_resolver.py` | Conservative LINK/MINT/AMBIGUOUS/NO_SETUP order |
| `transitions.py` | Pure T01–T26 evaluator with explicit precedence |
| `schema.py` | Versioned additive operational and compact seed schemas |
| `storage.py` | Migration, atomic commit, idempotency, seed and replay storage |
| `adapter.py` | Full-scope-first Core artifact adaptation and quality status |
| `service.py` | Quality → identity → transition → atomic bundle orchestration |
| `artifacts.py` | Private compact artifacts and JSON Schema validation |
| `run_state_machine_shadow.py` | Explicit offline CLI and path safety |

## 4. Design and fixture traceability

| Requirement | Implementation evidence |
|---|---|
| Seven phases | `SetupPhase` and schema CHECK constraint |
| T01–T26 | One deterministic evaluator; all IDs executed in domain tests |
| 35 fixed scenarios | Exhaustive fixture registry; 20 step scenarios execute directly and 15 boundary/storage/seed/replay cases execute in the same Phase 2A suite |
| Cross before expiry | First over-limit cross returns T06/T12/T13 and records `QUALIFIED_CROSS_PRECEDES_EXPIRY` |
| CLOSED evidence only | T26 requires `PermanentExitEvidence`; dynamic OUT_OF_SCOPE stays paused |
| No legacy 964 backfill | Cutover only mints from current forward observations |
| Cutover BREAKOUT | T04 records `SETUP_MINTED` + `BOOTSTRAP_OBSERVED`, not an invented breakout |
| Pivot freeze/revision | T23 revises pre-breakout; T24 freezes after breakout |
| Gap limit | Five sessions can link; six becomes AMBIGUOUS/SUSPENDED_GAP |
| Connors exclusion | Adapter counts only five trend strategies; Connors-only cannot mint |

Threshold version `smt1` remains: 3% failure, 3% retry distance, 2 aligned
strategies, 2 breakout strategies, 90 pre-breakout sessions, 20 post-breakout
sessions and 5 auto-link gap sessions.

## 5. Schema and versions

- State machine: `sm1`
- Thresholds: `smt1`
- Core identity: `csu1`
- Identity decision rule: `idr1`
- Observation: `smo1`
- Event: `sev1`
- Operational schema: `phase2a-schema-v1`, SHA-256
  `9e210ab3760dbe1e6db977db82a7f41623e067eb5d24024f73fd7d83354fb81a`
- Seed schema: `phase2a-seed-v1`

The operational schema has 16 `phase2a_` tables and 11 explicit secondary
indexes. Detailed ownership and invariants are in `docs/PHASE2A_SCHEMA.md`.
Migration is explicit and idempotent; a same-version hash mismatch stops.

## 6. Full-scope and run-quality evidence

The final benchmark stored 1,000 scope rows before candidate evaluation:

- CURRENT: 995
- STALE_MARKET_DATE: 4
- INSUFFICIENT_PRICE_HISTORY: 1
- Other error statuses: 0 in the benchmark distribution

Unit fixtures also cover FETCH_FAILED, ANALYSIS_FAILED, INVALID_INPUT,
OUT_OF_SCOPE, NO_NEW_MARKET_OBSERVATION and unmapped structured errors. Quality
status, coverage and publication eligibility remain separate fields. Pivot
source dates that are missing, same-day or future fail closed; full chart,
ranking, trade plan and raw financial objects are not persisted.

## 7. Test and verification results

| Command / evidence | Result |
|---|---|
| Phase 2A domain/fixture/storage/CLI suite | 79 passed |
| Full repository `pytest -q` | 247 passed in 2.36 s |
| T01–T26 transition ID coverage | 26/26, unique |
| Fixed scenario registry | 35/35 accounted for |
| Exact boundary tests | Passed |
| JSON artifact schema validation | Passed during shadow artifact/CLI tests |
| SQLite integrity / foreign key checks | `ok` / no errors in all 18 benchmark scenarios |

The full repository suite covers the existing Core, export, Validation,
Experimental, Research, Morning Brief and committee contracts in addition to
Phase 2A. No product contract regression was observed.

## 8. Atomicity, idempotency and concurrency

- Failure injection at all eight commit gates—from `RUN_INSERT` through
  `INVARIANT_CHECK`—rolls back the complete run bundle.
- Retry commits once; another exact attempt returns `NO_OP`.
- Duplicate mint and event counts remain zero.
- A different same-day input is quarantined by lineage/date uniqueness.
- Optimistic state-version conflict rolls back the entire transaction.
- Out-of-order input is rejected and routed to a separate replay namespace.
- A commit exception records only a digest in the separate failed-run manifest;
  no partial operational row remains.

## 9. Seed and recovery

The compact seed includes only continuity facts: identity ledgers, current
states, required Pivot revisions, active strategy memberships and event anchors.

- Final seed: 6,840,320 bytes.
- Export / verify / restore: 125.96 / 8.54 / 79.43 ms.
- Restored current-state hash: exact match.
- Missing, incomplete, corrupt, schema-mismatched or row-count-mismatched seeds
  fail closed and never trigger all-MINT fallback.

Operator steps are in `docs/PHASE2A_SEED_RECOVERY.md`.

## 10. Final physical benchmark

- Scenario count: 18.
- Annualized range: 391,607,808–497,439,744 bytes.
- Representative `e10_r15_wal`: 429,256,704 bytes/year.
- Representative p50 / p95: 49.11 / 56.84 ms.
- Maximum WAL + SHM peak: 9,039,120 bytes.
- Compact artifact: 455,568 raw / 159,392 gzip bytes.
- Capacity gate: PASS.

Compared with the prototype's roughly 473 MB representative estimate, the
final normalized layout is about 44 MB (9.3%) lower. Required audit tables and
fields were checked and retained; no hypothetical archive/compression savings
were counted. See `docs/PHASE2A_FINAL_PHYSICAL_BENCHMARK.md` and the machine
readable JSON result.

## 11. Isolation evidence

- `engine.state_machine` does not import `engine.database`.
- The CLI explicitly rejects repository product DB paths and every path under
  `public/`; both rules are tested.
- Phase 2A migration never runs on package import or normal app/export startup.
- No Phase 2A reference was added to `public/`, `.github/`, `app.py`,
  `engine/database.py` or the existing exporter.
- The final benchmark used temporary SQLite databases only.

## 12. Known limitations and NOT READY items

1. No approved real permanent-exit master field is connected. Runtime CLOSED is
   fail-closed; synthetic structured-evidence coverage passes.
2. No remote durable seed authority has been selected. This blocks scheduled
   production operation.
3. Workflow restore/export, rollback and operator approval are not implemented.
4. No public or private product consumer is connected to Phase 2A artifacts.
5. No immutable multi-day, full-scope Core input set was available for a real
   965-code local shadow sequence. Candidate-only data was deliberately not
   substituted.
6. Capacity timings are local synthetic measurements, not CI latency guarantees.

## 13. Human review checklist

- [ ] Confirm T01–T26 and the 35 fixed scenarios match the approved design.
- [ ] Confirm cross-before-expiry and structured-evidence-only CLOSED behavior.
- [ ] Confirm the full-scope/status model and minimal observation fields.
- [ ] Confirm seed contents are sufficient but do not duplicate full history.
- [ ] Accept the 392–497 MB/year projection and measured artifact size.
- [ ] Confirm product outputs and workflow remain unconnected.
- [ ] Supply/approve multiple immutable full-scope Core input days.
- [ ] Decide the permanent-exit master source.
- [ ] Select the GitHub Actions durable seed authority and rollback point.

## 14. Next Goal

First perform several saved-input local shadow runs and review identity/event/
expiry deltas without changing `smt1`. After that review, create a separate
**Phase 2A Cutover / Deployment Goal** covering durable seed authority, workflow
restore/export, operator approval, rollback, first cutover and consumer policy.
