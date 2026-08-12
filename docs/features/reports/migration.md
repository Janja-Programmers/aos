# Reports migration

`aos.patches.v1_0.harden_reports_subsystem` runs after normal Frappe model sync and adds the missing User-report duplicate integrity boundary plus indexes justified by the existing review/query paths. It deliberately does not force-reload Report DocTypes after model sync, because doing so can remove manual indexes owned by the Ads, Shorts, and Reviews domains.

Indexes added when supported and absent:

- User-report review queue: `(status, creation, name)`
- User-report target review lookup: `(reported_user, status, creation, name)`
- User-report reporter cleanup/history lookup: `(reported_by, creation, name)`
- active Report Reason listing: `(is_active, sort_order, title)`

The data patch backfills the hidden `active_key` on User Report and deterministically reconciles legacy duplicate non-Rejected User reports by retaining the oldest row and marking later duplicates `Rejected`; no record is deleted. Active keys are backfilled in batches of 250.

`aos.patches.v1_0.install_report_indexes` is a schema-only, idempotent companion patch registered immediately after shared Report hardening. It installs `uq_aos_user_report_active` and the shared User/Reason query indexes, and it restores domain-owned Ad, Short, and Review report indexes that may have been removed by an older Report schema reload, including `uq_short_report_active`, without changing report records or weakening uniqueness checks.

The shared hardening patch normalizes only blank report statuses to the existing default `Reviewing`, contains no explicit commit, and is registered in `aos/patches.txt` before the schema-only repair patch.
