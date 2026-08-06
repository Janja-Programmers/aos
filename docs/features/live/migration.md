# Live Migration and Rollback

## Patch order

`aos/patches.txt` runs:

1. `aos.patches.v1_0.harden_live_subsystem`
2. `aos.patches.v1_0.install_live_indexes`

Data is reconciled before unique indexes are installed.

## Data reconciliation

The data patch is rerunnable, owns no commit, and makes no LiveKit call. It processes deterministic batches of 250 and repairs:

- unknown lifecycle states to terminal ended;
- missing server room names;
- unavailable active hosts;
- duplicate active Lives per host;
- stale/negative counters;
- active viewers on ended/missing Lives or unavailable accounts;
- duplicate active viewer sessions;
- missing opaque viewer/co-host identities;
- invalid/self/stale/duplicate co-host workflows;
- empty or duplicate idempotent comments;
- aggregate metrics.

Ended/stale rooms are marked for post-migration reconciliation; the patch does not call LiveKit.

## Schema/index patch

The index patch verifies tables/columns and unique-readiness before adding:

- unique room name;
- discovery, host-state, cleanup indexes;
- unique active viewer session and presence/identity indexes;
- co-host state/user indexes;
- message/reply pagination indexes;
- reaction stream index;
- webhook retention index.

DocType unique fields create final uniqueness for active host, active workflow, active comment idempotency, and webhook event ID.

## Deployment

```bash
# 1. Backup first
bench --site <site> backup --with-files

# 2. Deploy code/dependencies, then migrate
bench --site <site> migrate

# 3. Restart web, scheduler, and workers
bench restart

# 4. Run external consistency after migration
bench --site <site> execute aos.tasks.live.reconcile_live_state
```

Review database process/lock metrics while indexes are created. Schedule migration during a controlled window on very large tables.

## Rollback

- Roll back application code only after stopping new Live traffic.
- Do not immediately drop added columns/indexes; the previous code can ignore additive schema, while dropping them risks data loss and long locks.
- Restore the pre-deployment database backup only when a full database rollback is required.
- If migration fails before completion, fix the cause and rerun `bench migrate`; patches are designed to be idempotent.
- If LiveKit cleanup is pending, keep workers running or run reconciliation after restoring compatible code.
