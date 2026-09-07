# Migration

Patch: `aos.patches.v1_0.harden_social_subsystem`.

It reloads Social DocTypes, removes invalid self-follows plus orphaned edges in batches, reconciles duplicate follow pairs, converts missing/self/duplicate active blocks to inactive history, backfills active block keys, recalculates profile counters in batches, and adds indexes/unique constraints. Deleted-account edges/blocks are intentionally retained because they are recoverable state during the Accounts restore window. It does not commit.

Run normal `bench --site <site> migrate`; do not invoke the patch while application writers are active unless your deployment process already serializes migrations. Take a database backup first.

## Rollback

Application rollback is a code deployment rollback. Data cleanup is intentionally non-destructive except invalid self/duplicate follow rows; unblocked block history is retained. Index rollback, if required, should be performed by an operator with explicit `DROP INDEX` statements after restoring the previous application version. Do not restore removed duplicate/self rows unless you have proven they represented legitimate data.
