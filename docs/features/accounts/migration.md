# Accounts migration and rollback

The post-model-sync patch `aos.patches.v1_0.harden_accounts_subsystem` backfills missing display names and random public IDs, then adds lifecycle/public-identity/preference indexes when their columns exist. It is idempotent and does not rewrite existing valid public IDs or internal User names.

Deploy with:

```bash
bench --site <site> migrate
bench --site <site> clear-cache
bench restart
```

Before migration, take a verified backup. Rollback application code only after confirming whether schema fields are still consumed by newer clients. Do not drop `public_id` or lifecycle columns during an emergency rollback; leaving additive fields in place is safer than destructive reversal.
