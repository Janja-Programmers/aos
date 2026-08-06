# Live Hardening Validation Results

Validation date: 2026-08-07 (Africa/Nairobi)

Execution environment:

- Python: 3.13.5
- Repository-required Python: 3.14.6
- Bench/Frappe site: unavailable in this container
- `frappe`, `livekit`, `ruff`, and `fakeredis` Python packages: unavailable
- LiveKit server, credentials, and network integration environment: unavailable

## Successful commands

```text
python -m compileall aos
Result: PASS (exit 0)

python -m unittest aos.api.live.tests.test_live_validation_unit aos.api.live.tests.test_live_source_guards -v
Result: PASS (exit 0), 29 tests passed

python -m unittest aos.tests.test_api_versioning -v
Result: PASS (exit 0), 5 tests passed

Exact AST rule from aos.tests.test_public_error_safety.test_public_api_fail_calls_do_not_use_raw_exception_strings
Result: PASS; no public fail() call serializes str(exc), .message, or get_traceback()

Static compatibility assertion for aos.api.live.live.NotificationService
Result: PASS; the legacy patch seam is restored

python ci/validate_rate_limit_coverage.py .
Result: PASS (exit 0), 208 whitelisted endpoint policies validated

python -c '<parse every aos/**/*.json>'
Result: PASS (exit 0), 68 JSON files parsed

python ci/validate_repository.py
Result: PASS (exit 0)

python ci/validate_doc_paths.py
Result: PASS (exit 0), 81 documented repository paths validated

python ci/audit_exceptions.py --root . --exceptions ci/vulnerability-exceptions.json --lock ci/requirements/root-production.lock
Result: PASS (exit 0)
```

There are 41 Live-specific test methods in `aos/api/live/tests`. The 29 import-free contract, validation, source-security, transaction, migration, LiveKit-grant, webhook, rate-limit, and documentation tests ran successfully. The remaining 12 tests require Frappe, MariaDB, Redis, and a Bench site.

## Correctly attempted but environment-blocked commands

```text
ci/repository-hygiene-and-secrets.sh
ci/check-python314-compatibility.sh
ci/repository-quality.sh
Result: NOT RUN TO COMPLETION (exit 1)
Reason: each gate requires Python 3.14.6; this container has Python 3.13.5.

bench --site test_site run-tests --app aos --module aos.api.live
bench --site test_site run-tests --app aos --module aos.tests.test_dynamic_sql_safety
bench --site test_site run-tests --app aos
Result: UNAVAILABLE (exit 127)
Reason: `bench` is not installed and no Frappe site/database is mounted.

python -m ruff check .
Result: UNAVAILABLE (exit 1)
Reason: `ruff` is not installed.

python -m pytest infra/notification-delivery -q
python -m pytest infra/moderation -q
python -m pytest infra/analytics-pipeline -q
Result: COLLECTION BLOCKED (exit 4)
Reason: test dependency `fakeredis` is not installed.

python -m pytest infra/livekit -q
Result: NO TESTS DISCOVERED (exit 5)
Reason: the repository has no `infra/livekit` test suite.
```

## Existing repository issue outside Live scope

```text
python ci/validate_companion_safety.py
Result: FAIL (exit 1)
```

The same command fails against the unmodified input ZIP with these two pre-existing findings:

- companion validation-field helper copies have diverged;
- five durable companion lifecycle module copies have diverged.

No companion-service module was changed as part of Live hardening.

## Repository hygiene correction discovered during validation

The input ZIP's Shorts schema patch used raw `ALTER TABLE` through `frappe.db.sql`, causing `ci/validate_repository.py` to fail before Live changes. The patch was changed to equivalent Frappe `add_index`/`add_unique` helpers. This is a narrowly scoped, behavior-preserving repository-hygiene correction. The final repository validator passes.

## Required staging evidence

Deployment is not complete until the staging commands in `testing.md` and `operations.md` pass under Python 3.14.6 with a real Bench site, MariaDB, Redis, workers, LiveKit 1.9.x deployment, configured credentials, and webhook delivery.

## Full-suite regression correction

A subsequent Bench full-suite run exposed three compatibility regressions, all corrected in this archive:

- Live test modules no longer import the public `aos.api.v1` namespace. The transport helper now lives in `aos.api.shared.transport`, while `aos.api.v1._transport` remains a backward-compatible export.
- `aos.api.live.live.NotificationService` is restored as an explicit legacy test/patch seam without moving notification business logic back into the endpoint module.
- Live endpoints no longer pass `str(exc)` to `fail()`. `LiveError.public_message` makes the intentional server-owned domain message explicit.

The exact Frappe-dependent core-flow and public-error test methods could not be rerun in this container because Frappe/Bench is unavailable. Their structural failure conditions were reproduced directly and now pass.
