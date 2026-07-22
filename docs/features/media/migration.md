# Migration and rollback

## Upgrade

Back up the database and object-storage configuration before deployment. Deploy code and environment changes, then run:

```bash
bench --site <site> migrate
bench clear-cache
bench restart
```

The post-model-sync patch `aos.patches.v1_0.harden_media_subsystem` is idempotent. It:

- backfills expected size from existing verified size;
- backfills completion time from upload time for active records;
- clears persisted public URLs from private records;
- preserves legacy initialized storage identities for compatibility;
- adds owner/purpose/status, attachment, cleanup, expiry, delete-retry, idempotency, and derived-media indexes through `frappe.db.add_index`, which safely handles migration DDL and duplicate indexes.

It does not delete or rename existing storage objects.

## Interrupted migration recovery

If an older revision failed with `frappe.exceptions.ImplicitCommitError` while executing `harden_media_subsystem`, deploy the corrected revision and rerun:

```bash
bench --site <site> migrate
```

Do not insert a Patch Log row manually and do not use `--skip-failing`. The failed patch was not marked complete, and its data updates are idempotent. The corrected patch creates indexes through Frappe's DDL-safe adapter before running the backfill updates. Confirm the expected indexes afterward with the SQL below, then clear cache and restart normally:

```sql
SHOW INDEX FROM `tabAOS Media Object`;
```

Repository validation rejects raw `ALTER`, `CREATE`, `DROP`, `TRUNCATE`, or `RENAME` statements passed to `frappe.db.sql` from patch modules. Use the database adapter's schema APIs (`add_index`, `add_unique`) or `sql_ddl` for narrowly reviewed DDL.

## Deployment validation

After migrate:

```bash
bench --site <site> execute aos.utils.production_config.validate_production_config
# Review lifecycle counts first, then run one bounded cleanup batch.
bench --site <site> execute aos.tasks.media.cleanup_media_objects
```

Use the repository's actual deployment/readiness commands where they wrap these functions. Then perform public and private upload smoke tests documented in `operations.md` and verify worker/scheduler health.

Review existing records before enabling cleanup aggressively:

```sql
SELECT status, purpose, visibility, COUNT(*)
FROM `tabAOS Media Object`
GROUP BY status, purpose, visibility;
```

Check that private rows have no `public_url`, active feature references point to valid Media IDs, and `Delete Pending` volume is understood.

## Compatibility notes

Versioned v1 method names and main response fields are preserved. New clients should submit only Media IDs to features. Legacy public URL fields remain output caches, but new arbitrary URL writes are rejected. Existing SVG category icons remain stored but cannot be newly uploaded through Media because executable/script-capable SVG is outside the allowlist.

## Rollback

Application rollback is safe only before clients depend on the new staged-upload fields and lifecycle semantics. To roll back code:

1. Stop web/workers/scheduler.
2. Restore the previous application revision.
3. Keep the added columns and indexes; they are backward-compatible and should not be dropped during an incident.
4. Restore prior environment values only if required, without exposing private buckets.
5. Run `bench --site <site> migrate`, clear cache, restart, and smoke test.

Do not restore cleared private public URLs and do not move staged/private objects into a public bucket. If a database restore predates the deployment, reconcile object storage from a matching backup rather than deleting unmatched objects.

## Data repair

Any production reconciliation must be reviewed, bounded, logged, and idempotent. Prefer marking ambiguous records for investigation over deleting bytes. External storage operations are not transactional with MariaDB; repair scripts need explicit compensation and rerun safety.
