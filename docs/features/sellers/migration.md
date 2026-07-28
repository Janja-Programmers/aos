# Migration

`aos.patches.v1_0.harden_sellers_subsystem` reloads the Seller DocType, backfills opaque public IDs and lifecycle metadata in bounded batches, fails invalid legacy states closed, clamps invalid metrics, recomputes Active-ad counts, and creates discovery/uniqueness indexes.

The patch validates legacy public IDs, deterministically reconciles duplicate or malformed values without renaming internal Seller documents, and then installs the unique public-ID constraint. It is additive, idempotent, safe on missing tables/columns, and does not commit.

Deploy with:

```bash
bench --site <site> migrate
bench --site <site> clear-cache
bench restart
```

Before applying aggregate repairs, run the documented Seller active-ad reconciliation in dry-run mode and retain its drift report.
