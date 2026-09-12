# Phase 2A Shadow Readiness

## Decision

**Local explicit shadow execution: READY.**

**Scheduled production cutover and public consumption: NOT READY.**

## Ready now

- Pure `sm1` / `smt1` transition evaluation with T01–T26.
- Cross-before-expiry and structured-evidence-only CLOSED behavior.
- Stable Core and strategy setup identities with conservative ambiguity handling.
- Full-scope-first adapter and one terminal observation status per scope member.
- Explicit standalone SQLite migration and atomic run commits.
- Same-run NO_OP, same-day conflict quarantine and optimistic state versioning.
- Compact local seed export, verification, restore and state-hash comparison.
- Explicit private artifacts validated against JSON Schema.
- CLI refusal of the repository product databases and anything under `public/`.

## Required input for a real shadow run

Each run needs an immutable, same-run set of:

1. Core snapshot JSON.
2. Detail JSON directory.
3. Full screening-scope manifest, including non-candidates and history counts.
4. Expected market date and monotonic market-session index.
5. Configuration file/hash and identity epoch.
6. A verified previous seed, or an approved first-cutover manifest hash.

The repository currently has no approved immutable full-scope manifest paired
with a multi-day sequence of saved Core snapshots. Therefore this implementation
did not pretend that a candidate list was the full scope and did not perform a
real 965-code historical shadow run.

## NOT READY items

- The real structured permanent-exit master source is not connected. Runtime
  CLOSED detection remains disabled/fail-closed; synthetic evidence tests pass.
- A remote durable seed authority for GitHub Actions has not been selected.
- Workflow restore/export, rollback and operator approval steps are not wired.
- No ranking, UI, Morning Brief or public artifact reads Phase 2A output.
- No legacy 964 setup IDs have been backfilled or linked.
- Real event/revision rates and multiple-day output deltas have not been observed.

## Human review gate

Review T01–T26, the 35 fixture scenarios, minimal stored facts, benchmark range,
seed contents, public artifact boundary and product regression evidence. Then
collect several immutable Core input days and run the local shadow CLI. Only
after those results are accepted should a separate Phase 2A Cutover / Deployment
Goal choose durable storage and modify workflows or consumers.
