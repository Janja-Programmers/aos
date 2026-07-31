# Migration and recovery

Patch: `aos.patches.v1_0.harden_shorts_subsystem` (post-model-sync).

The patch reloads affected DocTypes, reconciles duplicate likes/saves/comment-likes/sounds/views/daily metrics/reports/reposts/jobs, normalizes privacy and stale processing states, backfills fixed SHA-256 active/event keys, reconciles counters, then installs unique/query indexes.

It is bounded, idempotent and contains no commit. Run it with normal `bench migrate`; do not execute while bypassing Frappe transaction handling.

Before deployment, take a database and object-storage backup and pause write traffic/workers. On failure, let migrate roll back, inspect the error, restore the database if DDL was partially applied by the database engine, and rerun after correction. The patch preserves historical rows where possible; duplicate active records become inactive/cancelled rather than being silently reinterpreted as valid activity.
