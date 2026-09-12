# Phase 2A Local Seed Recovery Runbook

## Boundary

This procedure is for an explicit local shadow database only. It does not
select a remote durable authority, modify GitHub Actions, open
`data/momentum.db`, or publish artifacts.

## Export a continuity seed

Pass a new, non-existing output directory to the shadow runner:

```bash
.venv/bin/python scripts/run_state_machine_shadow.py \
  --snapshot /absolute/private/input/snapshot.json \
  --details-dir /absolute/private/input/details \
  --scope-manifest /absolute/private/input/scope.json \
  --expected-date YYYY-MM-DD \
  --market-session-index N \
  --identity-epoch phase2a-cutover-YYYY-MM-DD \
  --db /absolute/private/state/phase2a-shadow.db \
  --output-dir /absolute/private/artifacts/YYYY-MM-DD \
  --seed-output /absolute/private/seeds/YYYY-MM-DD
```

The output must contain `seed.sqlite`, `seed-manifest.json` and `seed.sha256`.
Keep the three files together.

## Verify before restore

```bash
.venv/bin/python - <<'PY'
from engine.state_machine.storage import Phase2AStore
print(Phase2AStore.verify_seed("/absolute/private/seeds/YYYY-MM-DD"))
PY
```

Do not continue if verification reports a missing file, hash mismatch, schema
mismatch, integrity failure or row-count mismatch. Do not initialize an empty
ledger as a fallback.

## Restore and continue

Use a new/non-existing database path. The runner verifies the seed before it
restores and refuses a non-empty target.

```bash
.venv/bin/python scripts/run_state_machine_shadow.py \
  --snapshot /absolute/private/input/next/snapshot.json \
  --details-dir /absolute/private/input/next/details \
  --scope-manifest /absolute/private/input/next/scope.json \
  --expected-date YYYY-MM-DD \
  --market-session-index N \
  --identity-epoch phase2a-cutover-YYYY-MM-DD \
  --seed /absolute/private/seeds/PREVIOUS-DATE \
  --db /absolute/private/state/restored-phase2a-shadow.db \
  --output-dir /absolute/private/artifacts/YYYY-MM-DD \
  --seed-output /absolute/private/seeds/YYYY-MM-DD
```

Do not pass `--initialize-cutover` during a normal seed restore.

## First cutover only

An empty ledger can start only with explicit operator intent:

```bash
.venv/bin/python scripts/run_state_machine_shadow.py \
  ... \
  --migrate \
  --initialize-cutover \
  --cutover-manifest-sha256 <64-character-approved-manifest-hash>
```

Cutover is forward-only. It does not retroactively link the legacy 964 setup
identifiers. A Core BREAKOUT without a proven cross is recorded as
`BOOTSTRAP_OBSERVED`, not an invented historical breakout.

## Recovery checks

After restore/run:

1. Confirm command status is `COMMITTED` or, for an exact repeat, `NO_OP`.
2. Inspect `run-summary.json` for scope and quality counts.
3. Run `Phase2AStore(...).integrity()` and require `ok` with no foreign-key errors.
4. Compare `state_hash()` before export and after a test restore when performing
   an operational rehearsal.
5. Preserve a failed bundle unchanged for diagnosis; never edit its hash files.

If commit fails, operational rows are rolled back. A digest-only row may remain
in `phase2a_failed_run_manifests`; it is audit evidence, not a partially applied
run.
