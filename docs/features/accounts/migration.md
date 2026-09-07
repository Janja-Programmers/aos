# Accounts migration and rollback

`aos.patches.v1_0.harden_accounts_subsystem` keeps fresh installs aligned with the final schema and performs one bounded upgrade action for a v10 staging site: when the retired `public_id` database column exists, each `AOS Profile` is renamed once so its primary key becomes that existing `ACC-*` value. Fresh v11 sites already create `ACC-*` profile names and skip this step. The patch also installs lifecycle/purge composite indexes.

Deploy with:

```bash
bench --site <site> migrate
bench --site <site> clear-cache
bench restart
```

Take a verified backup before upgrading an existing staging site. After v11 is deployed, application code must not depend on the retired `public_id`, `is_deleted`, profile `location`, verification audit projections, or `Deactivated` fields even if MariaDB retains old physical columns until a later database compaction.
