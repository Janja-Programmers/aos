# AOS Reports backend

AOS Reports is the existing human-reporting and Desk moderation domain. The repository currently supports exactly four report targets: **Users, Ads, Shorts, and Reviews**. All four reuse the active `AOS Report Reason` master.

The authoritative human-review states are:

`Reviewing -> Resolved | Rejected`

`Resolved` and `Rejected` are terminal. New reports always start as `Reviewing`; public callers cannot choose status, reviewer metadata, or moderation actions.

## Supported workflows

- Authenticated User reporting, with the existing optional **report and block** behavior.
- Authenticated Ad reporting.
- Authenticated Short reporting.
- Authenticated Review reporting through the existing Reviews v1 API.
- Authenticated listing of active Report Reasons.
- Desk review by users with effective Write permission on the relevant Report DocType, using the exact actions already modeled by that DocType.
- Server-controlled review metadata, immutable submitted evidence, terminal lifecycle enforcement, and one-shot moderation effects.
- Duplicate/race protection through target locks plus the existing Ad/Short/Review database uniqueness constraints and the Report hardening constraint for User reports.
- Recoverable deletion preserves reporter-private rows for 30 days; permanent cleanup after restore expiry removes reports submitted by that account while preserving reports *about* the account/content as moderation history.

## Exact existing moderation actions

- User: `Warn User`, `Suspend User`, `Dismiss Report`.
- Ad: `Warn Seller`, `Suspended Ad`, `Suspended Seller`.
- Short: `Hide Short`, `Warn Creator`, `Suspend Creator`, `Dismiss Report`.
- Review: no `admin_action` field; reviewers resolve or reject the report only.

Warning/dismiss actions that have no existing notification or enforcement contract remain audit-only. No new warning-notification category was invented.

## Intentionally unsupported

The repository does not currently model dedicated report records/endpoints for Live streams, Live comments, Chat messages, Calls, sellers directly, profile comments, or arbitrary generic entities. It also does not model report attachments/evidence uploads, appeals, reporter-facing report history/status endpoints, report expiry, automatic report escalation thresholds, or a separate Report notification domain. These were not invented.

The production content-moderation job service remains a separate automated moderation subsystem. Human Reports reuse owning-domain enforcement services where already available rather than duplicating Ads, Accounts, Sellers, Shorts, Reviews, Social, or Auth rules.

See `api.md`, `lifecycle.md`, `security.md`, `operations.md`, `migration.md`, and `testing.md`.
