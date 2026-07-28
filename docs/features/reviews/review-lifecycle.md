# Review lifecycle

States:

- `Pending`: hidden while moderation is queued, processing or awaiting manual review.
- `Approved`: public and included in aggregates.
- `Rejected`: private to the author and excluded from aggregates.
- `Hidden`: administratively/moderation hidden and excluded from aggregates.
- `Withdrawn`: author soft-deleted, terminal, excluded from aggregates.

Creation always starts at `Pending`. Editing rating, title, text or Media increments `moderation_generation`, returns the review to `Pending`, and dispatches a new moderation job. A callback may mutate the review only when its persisted generation equals the review's current generation. This prevents an older callback from publishing or rejecting newer content.

Withdrawal is idempotent, preserves trust/audit history, clears public Media references, releases Media through the Media service and recalculates aggregates. Physical deletion remains an administrator-only maintenance operation.
