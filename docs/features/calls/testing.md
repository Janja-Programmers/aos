# Calls testing

Feature-owned tests live in `aos/api/calls/tests/`.

## Pure tests

`test_calls_validation_unit.py` validates strict fields, canonical IDs, aliases, cursors, limits, bulk bounds, and the complete public endpoint specification.

`test_calls_source_guards.py` validates the v1 surface, transaction boundary, after-commit realtime, public RTC identities, pre-accept token denial, canonical policy reuse, transient incoming/persistent missed semantics, rate-limit coverage, bounded history, cleanup/reconciliation, migrations/indexes, account-deletion integration, existing states/types, and documentation.

These tests do not require a Frappe site.

## Database-backed tests

`test_calls_database_contracts.py` is intended for a migrated Frappe staging/test site. It exercises lifecycle idempotency, participant busy rules, token authorization, block-after-ring behavior, timeout race safety, history privacy, realtime after-commit arguments, and account-deletion call cleanup while external LiveKit/notification effects are mocked.

Recommended staging commands are documented in the delivery summary. Run the complete AOS suite after the Calls module tests.

## Notification-delivery companion

The Android native incoming-call transport is implemented by the existing notification-delivery companion. Its feature test is:

```bash
PYTHONPATH=infra/notification-delivery pytest -q infra/notification-delivery/tests/test_worker_calls.py
```

The companion test environment needs the repository's notification-delivery test dependencies. After deploying a Calls release that changes `infra/notification-delivery/app/worker.py`, rebuild and restart only the existing notification services:

```bash
docker compose up -d --build notification-api notification-worker
docker compose ps notification-api notification-worker
```

Run `scripts/aos_services_smoke_test.sh` after the services report healthy/ready.
