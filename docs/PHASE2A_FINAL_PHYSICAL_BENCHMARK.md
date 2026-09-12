# Phase 2A Final Physical Schema Benchmark

## Outcome

Capacity gate: **PASS**.

The final SQLite schema was exercised in 18 isolated scenarios: 1,000 scope
rows per run, 995 CURRENT + 4 STALE + 1 INSUFFICIENT, 10 transactions,
DELETE/WAL journal modes, event rates 0/10/25%, and Pivot revision rates
0/15/50%. No product database or public path was used.

| Measure | Result |
|---|---:|
| Annualized range, 965 codes × 250 runs | 391,607,808–497,439,744 bytes |
| Representative `e10_r15_wal` | 429,256,704 bytes/year |
| Representative p50 / p95 | 49.11 / 56.84 ms |
| Representative 10-run increment | 17,793,024 bytes |
| Representative WAL + SHM peak | 8,610,640 bytes |
| Maximum tested WAL + SHM peak | 9,039,120 bytes |
| Compact artifact raw / gzip | 455,568 / 159,392 bytes |

All 18 scenarios returned `integrity_check=ok` and no foreign-key errors.

## Seed and failure recovery measurements

| Measure | Result |
|---|---:|
| Seed size | 6,840,320 bytes |
| Export | 125.96 ms |
| Verify | 8.54 ms |
| Restore | 79.43 ms |
| Restored state hash | Exact match |

The injected `EVENT_INSERT` failure left counts equal to the pre-run state.
Retry produced one `COMMITTED` run, and a second identical attempt returned
`NO_OP`. A missing seed failed closed.

## Representative physical attribution

The following values were measured by dropping one table in a copied database
and running `VACUUM`. They are useful for relative diagnosis but are not
strictly additive because SQLite repacks pages.

| Table | Attributed bytes |
|---|---:|
| Observations | 11,071,488 |
| Identity decisions | 5,955,584 |
| Scope members | 2,854,912 |
| Events | 2,748,416 |
| Strategy identity ledger | 1,921,024 |
| Strategy memberships | 1,847,296 |
| Pivot revisions | 1,781,760 |
| Identity ledger | 1,368,064 |
| Current states | 1,351,680 |
| Runs | 860,160 |

## Comparison with the prototype benchmark

The limited prototype estimated 435–540 MB/year with a representative result
of about 473 MB/year. The final representative result is about 429 MB/year,
roughly 44 MB (9.3%) lower.

This lower result was reviewed under the sub-435 MB rule. Required audit data
was not removed: the final schema includes full-scope statuses, minimal current
observations, both identity ledgers, identity decisions, versioned Pivots,
strategy memberships, events, current states, rejections and recovery anchors.
The reduction comes from the normalized final layout and compact versioned
records rather than assuming unmeasured compression or archival. The highest
tested scenario remains below the 540 MB review threshold.

## Limits

- This is a deterministic synthetic workload on the local machine, not a
  GitHub-hosted runner timing guarantee.
- The projection does not subtract future retention, compression or archival.
- A real multi-day shadow run is still needed to measure the observed event and
  Pivot revision rates.

Machine-readable evidence is in
`docs/examples/phase2a-state-machine/final-physical-benchmark-results.json`.
