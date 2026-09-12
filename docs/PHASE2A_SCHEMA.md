# Phase 2A State Machine Schema

## Purpose and isolation

Phase 2A owns a standalone SQLite database. All operational objects use the
`phase2a_` prefix. Migration is performed only by an explicit call to
`Phase2AStore.migrate()` or `scripts/run_state_machine_shadow.py --migrate`.
Importing the package, running the existing app, and running the existing
exporter do not open or migrate the Phase 2A database.

Schema identity is the pair `phase2a-schema-v1` and the SHA-256 of the exact
`SCHEMA_SQL` text. The implemented hash is
`9e210ab3760dbe1e6db977db82a7f41623e067eb5d24024f73fd7d83354fb81a`.
A previously recorded version with a different hash is a hard error.

## Operational records

| Table | Role | Mutability |
|---|---|---|
| `phase2a_schema_migrations` | Schema version and exact schema hash | Append by explicit migration |
| `phase2a_runs` | Full-scope run envelope, quality and commit result | Staged then finalized in one transaction |
| `phase2a_scope_members` | One terminal observation status per scope code and run | Append |
| `phase2a_identity_ledger` | Stable Core setup UID and canonical mint request | Append; terminal flag only closes |
| `phase2a_strategy_identity_ledger` | Stable strategy setup UID | Append |
| `phase2a_observations` | Minimal accepted CURRENT facts | Append |
| `phase2a_identity_decisions` | LINK/MINT/AMBIGUOUS/NO_SETUP evidence | Append |
| `phase2a_pivot_revisions` | Versioned tracking Pivot | Append; prior validity may close |
| `phase2a_strategy_memberships` | Versioned strategy membership | Append; prior validity may close |
| `phase2a_events` | Deterministic event ledger | Append-only |
| `phase2a_current_states` | Materialized latest state per lineage and setup | Optimistic versioned update |
| `phase2a_rejections` | Quarantine/replay/reject audit rows | Append |
| `phase2a_replay_lineages` | Explicit replay namespaces | Append; never merged to live |
| `phase2a_event_anchors` | Minimal anchors needed for continuity/recovery | Upsert to latest anchor |
| `phase2a_seed_manifests` | Verified seed import audit | Append |
| `phase2a_failed_run_manifests` | Failure digest outside a rolled-back run | Append/idempotent |

## Transaction invariants

- Run, scope, observation, identity, Pivot, membership, event, rejection and
  current state changes commit atomically.
- A failure before finalization rolls back every operational row from that
  attempt. A separate failure manifest may be recorded afterwards.
- `(state_lineage, expected_market_date)` prevents a different same-day run.
- Event UID, full request hash and idempotency key are independently unique.
- A current-state write must advance the expected version by exactly one.
- A terminal identity cannot be returned by the active-candidate query.
- Events are not recomputed under a newer evaluator; replay requires another
  lineage.

## Seed schema

`phase2a-seed-v1` is deliberately smaller than the operational database. It
contains identity ledgers, current states, Pivot revisions required by those
states, active strategy memberships and event anchors. It excludes observation
history, run history, rejected rows and the full event ledger.

Every local seed bundle contains:

- `seed.sqlite`
- `seed-manifest.json`
- `seed.sha256`

Verification checks the file hash, seed schema version, operational schema hash,
SQLite integrity and exact row counts before restoration.

## Retention readiness

Run/date and setup/date indexes required for a later hot/archive policy are
present. Phase 2A does not delete or archive any records in this implementation.
