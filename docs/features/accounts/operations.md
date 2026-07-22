# Accounts operations

## Signals

Structured logs use events such as `account.profile.updated`, `account.preference.updated`, `account.deactivated`, `account.deleted`, and `account.restored`. Payloads contain an opaque hash, bounded changed-field names, outcome, and safe failure category.

Prometheus exposes `aos_account_events_total{event,outcome}` with bounded labels. Never add email, phone, user ID, request body, token, Media key, or arbitrary exception text as a metric label.

## Runbook

After deployment, migrate, clear cache, restart, then smoke-test login, `/me`, private/public profile separation, a partial preference update, avatar replacement, and lifecycle session invalidation. Monitor Auth failure rate, Accounts failure events, Redis availability, and Media orphan cleanup.
