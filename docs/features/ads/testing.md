# Ads testing

## Focused tests

```bash
bench --site <disposable-test-site> run-tests \
  --module aos.api.ads.tests.test_domain_validation

bench --site <disposable-test-site> run-tests \
  --module aos.tests.test_ads_api_contracts

bench --site <disposable-test-site> run-tests \
  --module aos.tests.test_ads_database_contracts
```

Also run existing Ads conversion, Media, Catalog, moderation, outbox, callback atomicity, dynamic SQL, migration, operational-health, and rate-limit suites, followed by the full app suite.

## Coverage

The focused suite verifies strict text and structured-input rejection, Decimal money, image uniqueness and primary-image rules, typed Catalog attributes and historical snapshots, category pricing, bounded filters, cursor integrity, bounded drafts, explicit lifecycle transitions, no endpoint commits, explicit public-field selection, public eligibility, deterministic pagination, schema fields, indexes, uniqueness, and patch idempotency.

Database-backed tests must run after `bench migrate` against a disposable site. Do not claim them passed from compilation alone. Tests must use unique records, restore the prior Frappe user, clean only their own rows in dependency order, avoid live external services, and not create scheduler-visible overdue records unless the test owns and removes them.

## Required repository gates

```bash
python -m compileall aos
python ci/assert_frappe_tests.py
python ci/validate_rate_limit_coverage.py
python ci/validate_repository.py
python ci/validate_actions.py
python ci/validate_foundation.py
python ci/validate_doc_paths.py
python ci/validate_monitoring.py
python ci/validate_deployment.py
python ci/validate_companion_safety.py
python ci/validate_nginx_policy.py
python ci/validate_systemd.py
```
