# Catalog testing

## Focused modules

```bash
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_validation
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_service
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_api
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_security
bench --site <disposable-test-site> run-tests --module aos.api.catalog.tests.test_desk_uploader
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

## Desk uploader device check

After `bench --site <site> migrate` and asset rebuild/cache clear:

1. Sign in to Desk as a user with `AOS Category` Write permission and save a category.
2. Upload a valid JPG, PNG, or WebP and confirm progress, preview, Media ID, and persisted image after reload.
3. Replace the image and confirm the prior Media row is released/replaced rather than remaining attached.
4. Remove the image and confirm the category reloads without an icon.
5. Reject a file over 5 MB, an unsupported extension, and dimensions outside 16×16–4096×4096.
6. Confirm a non-System-Manager cannot use the controls and cannot bypass the server API.
7. Simulate a failed storage PUT and confirm the category remains unchanged and a safe error is shown.

The client checks are usability hints. Server-side Media tests remain the security and integrity acceptance gate.
