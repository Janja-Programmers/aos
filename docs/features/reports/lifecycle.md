# Reports lifecycle

## State machine

Every new human report is server-created as `Reviewing`.

Allowed reviewer transitions are:

- `Reviewing -> Resolved`
- `Reviewing -> Rejected`

`Resolved` and `Rejected` are terminal. A terminal report cannot return to `Reviewing` or move to the other terminal state through a later/stale save.

Only users with effective Write permission on the concrete Report DocType may change status or `admin_action`. Frappe Desk permissions remain the first boundary and DocType lifecycle checks remain the server-side boundary even for `ignore_permissions` saves.

## Concurrency

Existing report rows are re-read under `SELECT ... FOR UPDATE` before lifecycle validation. This makes the transition decision use the current persisted state rather than a stale Desk form. Concurrent reviewers therefore serialize; a second stale decision sees the first terminal state and is rejected.

New submissions serialize on their target where appropriate. User, Ad, Short, and Review creation all have a database uniqueness boundary as the final duplicate-race guard.

## Immutable submission

After insertion, the target, reporter, reason, details, and target-derived ownership fields are immutable. A reviewer cannot rewrite submitted evidence while deciding the report. Reason deactivation after submission does not prevent an existing report from being reviewed; the linked reason must merely continue to exist.

Reviewer identity/time are server-stamped when status or moderation action changes and cannot be directly forged on an otherwise unchanged report.

## Moderation effects

Moderation effects run once for a newly resolved action and reuse the owning hardened domain:

- User suspension updates Accounts state, disables the User, and revokes account access.
- Ad suspension uses Ads lifecycle transition/locking and discovery refresh.
- Seller suspension uses Seller policy and refreshes affected Ads.
- Short hiding updates existing Shorts moderation/discovery state.
- Short creator suspension reuses Accounts suspension behavior.

No service-layer commit is performed; the report decision and its synchronous domain state changes participate in the request transaction.
