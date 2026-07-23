# Catalog testing

## Focused modules

```bash
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_validation
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_service
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_api
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_security
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_migration_patch
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_permission_patch
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_database_integration
bench --site <disposable-test-site> run-tests --module aos.api.media.tests.test_category_integration
bench --site <disposable-test-site> run-tests --module aos.tests.test_catalog_database_contracts
bench --site <disposable-test-site> run-tests --module aos.tests.test_response_status_mapping
bench --site <disposable-test-site> run-tests --module aos.tests.test_operational_metrics
```

The database suite creates unique records, restores the prior user, reloads mutated documents when needed, and cleans only its own records in dependency order. It does not delete broad tables or create overdue outbox fixtures.

## Full gates

```bash
bench --site <disposable-test-site> migrate
bench --site <disposable-test-site> run-tests --app aos
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

Do not promote based on compile/static checks alone. Focused database tests, the full AOS suite, migration on a disposable site, and deployment/staging verification are separate acceptance gates.
