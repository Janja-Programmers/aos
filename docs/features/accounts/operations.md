# Accounts operations

## Signals

Structured logs use events such as `account.profile.updated`, `account.preference.updated`, `account.deletion.requested`, `account.deleted`, `account.restored`, and permanent-deletion progress/completion. Payloads contain a site-keyed opaque account correlation value, bounded changed-field names, outcome, and safe failure category.

Prometheus exposes `aos_account_events_total{event,outcome}` with bounded labels. Never add email, phone, internal User.name, request body, token, Media key, or arbitrary exception text as a metric label.

## Runbook

After deployment, migrate, clear cache, restart, then smoke-test login, `/me`, private/public profile separation, a partial preference update, avatar replacement, 2FA continuation when policy applies, account deletion/restoration, and lifecycle session invalidation. Monitor Auth failure rate, Accounts failure events, Redis availability, and Media orphan cleanup.
