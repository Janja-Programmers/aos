# Reports migration

`aos.patches.v1_0.harden_reports_subsystem` reloads the existing Report schemas and adds the missing User-report duplicate integrity boundary plus indexes justified by the existing review/query paths.

Indexes added when supported and absent:

- User-report review queue: `(status, creation, name)`
- User-report target review lookup: `(reported_user, status, creation, name)`
- User-report reporter cleanup/history lookup: `(reported_by, creation, name)`
- active Report Reason listing: `(is_active, sort_order, title)`

The patch adds hidden `active_key` to User Report and unique `uq_aos_user_report_active`. It deterministically reconciles legacy duplicate non-Rejected User reports by retaining the oldest row and marking later duplicates `Rejected`; no record is deleted. Active keys are backfilled in batches of 250.

Existing Ad, Short, and Review uniqueness constraints remain owned by their already-hardened feature migrations and are not duplicated here.

The patch normalizes only blank report statuses to the existing default `Reviewing`, contains no explicit commit, and is registered in `aos/patches.txt`.
