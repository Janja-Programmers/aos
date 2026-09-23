# Calls testing

Feature-owned tests live in `aos/api/calls/tests/`.

## Pure/source-contract tests

`test_calls_validation_unit.py` covers strict request fields, opaque public IDs, removed aliases, cursor pairs, bulk bounds, and the public endpoint specification.

`test_calls_source_guards.py` covers opaque-ID/no-room leakage, provisioning-before-incoming, token readiness, LiveKit call-source grants, preservation of Live grants, no sleeping timeout worker, bounded recovery/timeout work, migration/index ordering, after-commit realtime, policy reuse, history bounds, and account-deletion integration.

`test_calls_livekit_contracts.py` runs in the Frappe test runtime and verifies call-scoped media/data grants, invalid call-type rejection, the two-participant room request, and unchanged Live grants.

The validation/source-guard tests do not require a Frappe site.

## Database-backed Calls tests

`test_calls_database_contracts.py` is intended for a migrated Frappe test/staging site. It covers opaque initiation/idempotent provisioning enqueue, one-time incoming dispatch, provisioning failure, busy-user rules, receiver pre-accept token denial, block-after-ring behavior, accept/cancel races, stale ring-expiry rejection before the scheduler runs, accepted-never-missed behavior, cross-participant idempotent end/cleanup, public history privacy, after-commit realtime, state-version monotonicity, required indexes, and account-deletion cleanup while provider effects are mocked.

Run the shared LiveKit/Live tests after Calls because Calls extends shared room provisioning and token generation without changing Live's contract. Then run the complete AOS app suite.

The exact migration/test commands are included in the backend delivery summary.
