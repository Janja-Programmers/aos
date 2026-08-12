# Activity migration

Activity hardening is split into two idempotent patches registered in this order:

1. `aos.patches.v1_0.harden_activity_subsystem`
2. `aos.patches.v1_0.install_activity_indexes`

## Data reconciliation

The data patch contains no schema reload and no explicit commit. It:

- normalizes invalid/blank legacy status values to the existing `Active` default;
- repairs non-positive occurrence counts;
- clears integrity keys on non-active rows;
- deterministically reconciles duplicate active `(user, unique_key)` rows without deleting history;
- merges repeatable counts/timestamps into the newest canonical row and hides duplicate rows;
- backfills server-generated `active_key` values;
- removes legacy private metadata keys such as `seller_user`, `session_id`, `view_id`, and `report_id`;
- converts legacy account/seller identity metadata to opaque public IDs when possible;
- canonicalizes legacy profile target names to their existing public `ACC-*` route IDs.

Rows are processed in bounded batches where the operation can span many records.

## Schema indexes

The schema-only patch installs, when absent:

- `uq_aos_activity_active (active_key)`
- `idx_aos_activity_user_timeline (user, status, last_occurrence_at, creation, name)`
- `idx_aos_activity_group_timeline (user, status, activity_group, last_occurrence_at, creation, name)`
- `idx_aos_activity_type_timeline (user, status, activity_type, last_occurrence_at, creation, name)`
- `idx_aos_activity_route_target (route_type, route_id, status, user)` for deletion-time target cleanup

The patch verifies required columns and duplicate readiness before creating the unique constraint.
