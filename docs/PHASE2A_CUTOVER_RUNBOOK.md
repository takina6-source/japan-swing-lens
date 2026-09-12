# Phase 2A Cutover Runbook

## Gate B boundary

Gate B approval authorizes pushing the reviewed commits and manually dispatching the first
cutover. It does not authorize public What Changed JSON. That remains blocked until Gate C.

## First cutover

1. Confirm `main` equals the reviewed manifest commit and all required checks pass.
2. Confirm `docs/phase2a-cutover-manifest.json` still matches the same-run Core market date,
   scope count/hash, config hash, versions, identity epoch, and disabled automatic `CLOSED`.
3. Dispatch `Update Swing Lens` with `phase2a_initialize=true`, the exact manifest SHA-256,
   and the manifest identity epoch.
4. The workflow generates the normal Core site first. Phase 2A failure cannot roll it back.
5. The shadow job starts with an empty private DB only after the event/hash gates pass.
6. It uploads one archive and its manifest to the single `phase2a-state-v1` Release, then
   downloads both again and verifies archive bytes, SQLite integrity, versions, and row counts.
7. Record the Actions URL, asset names/hashes, run summary, and restored state hash.

If the market date or scope has changed since manifest approval, initialization must fail
before creating the DB. Update the manifest in a new reviewed commit and repeat Gate B; never
weaken the equality checks.

## Normal production shadow

Scheduled and manual non-initialize runs enumerate immutable manifests, validate every one,
and select the newest eligible market date. Missing, malformed, corrupt, wrong-lineage, or
wrong-version data stops Phase 2A without falling back to cache or creating new identities.
Same-date/same-input retries return `NO_OP` before a DB is created. Same-date/different-input
retries fail closed.

Keep at least five distinct verified market dates. Assets are not overwritten and this phase
does not use a mutable latest pointer.

## Rollback

1. Disable the Phase 2A shadow job while leaving Core Pages enabled.
2. Download the selected historical archive and its matching manifest.
3. Verify both with `scripts/phase2a_release_seed.py extract` into a new empty directory.
4. Rehearse continuation in a separate DB and compare state hash/run summary.
5. Resume only after a human records the chosen seed, reason, affected runs, and restart date.

Never delete or overwrite the current product DB, merge a later live state into an older seed,
or reinitialize the live lineage to recover from a continuity error.
