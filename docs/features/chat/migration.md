# Chat migration

The production migration order in `aos/patches.txt` is:

1. `aos.patches.v1_0.harden_chat_subsystem`
2. `aos.patches.v1_0.install_chat_indexes`

## Data reconciliation

`harden_chat_subsystem` is bounded and rerunnable. It does not commit and does not call external services. It:

- normalizes unordered conversation participants and pair keys;
- repairs negative unread counters;
- merges duplicate participant-pair conversations while retaining message history;
- re-points known conversation references when those DocTypes/columns exist;
- clears duplicate message idempotency digests without deleting legitimate messages;
- removes duplicate `(message, media)` attachment rows;
- removes orphaned reaction/star/translation/attachment private rows;
- recomputes unread state and best-effort conversation previews;
- deactivates malformed/self conversations and clears their unread state without deleting history;
- deactivates conversation sides belonging to missing, disabled, suspended/deactivated or deleted legacy accounts while retaining the other participant's legitimate shared history.

Invalid self/missing participant legacy conversations are retained but made inactive and excluded from the uniqueness pair key rather than destructively deleted.

## Index installation

`install_chat_indexes` runs only after reconciliation and verifies duplicate-free values before installing uniqueness boundaries. It adds/ensures indexes for participant pair, inbox ordering, message history/status/reply/shared-object lookups, attachment uniqueness/order, stars, reactions and translation pagination.

## Deployment

Back up the site before deployment. Deploy code, run `bench --site <site> migrate`, then run Chat tests. Do not manually run the index patch before the data reconciliation patch.

Rollback should restore the pre-deployment database backup plus previous code. Avoid dropping additive Chat fields/indexes while newer code or messages may depend on them.
