# Migration and recovery

Patches (post-model-sync, in order):

1. `aos.patches.v1_0.harden_shorts_subsystem` — bounded data reconciliation only.
2. `aos.patches.v1_0.install_shorts_indexes` — idempotent schema/index installation only.
3. `aos.patches.v1_0.install_shorts_recommendation_indexes` — idempotent recommendation query indexes only.
4. `aos.patches.v1_0.initialize_short_classification_metadata` — idempotently preserves valid historical modes as `legacy` and assigns `vibes` only to invalid or missing publishable historical modes.

After normal post-model-sync DocType updates, the data patch reconciles duplicate likes/saves/comment-likes/sounds/views/daily metrics/reports/reposts/jobs, normalizes privacy and stale processing states, backfills fixed SHA-256 active/event keys, and reconciles counters. The following schema-only patches install the core and recommendation query indexes. The final data-only classification patch initializes metadata for existing published Shorts without reclassifying legitimate historical choices. The DML/DDL split is required because MariaDB DDL implicitly commits and Frappe rejects `ALTER TABLE` after transactional writes in the same patch.

It is bounded, idempotent and contains no commit. Run it with normal `bench migrate`; do not execute while bypassing Frappe transaction handling.

Before deployment, take a database and object-storage backup and pause write traffic/workers. On failure, let migrate roll back, inspect the error, restore the database if DDL was partially applied by the database engine, and rerun after correction. The patch preserves historical rows where possible; duplicate active records become inactive/cancelled rather than being silently reinterpreted as valid activity.
