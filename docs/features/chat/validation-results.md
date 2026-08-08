# AOS Chat production-hardening validation results

## Passed locally

- `python -m compileall -q aos` — PASS.
- `python -m unittest aos.api.chat.tests.test_chat_validation_unit aos.api.chat.tests.test_chat_source_guards -v` — PASS, **29 tests**.
- `python -m unittest aos.api.live.tests.test_live_validation_unit aos.api.live.tests.test_live_source_guards -v` — PASS, **32 tests**.
- `python -m unittest aos.tests.test_api_versioning -v` — PASS, **5 tests**.
- `python ci/validate_rate_limit_coverage.py .` — PASS, **209 whitelisted endpoint policies validated**.
- `python ci/validate_repository.py` — PASS: JSON/TOML/YAML, duplicate keys, conflict markers, debug statements, patch DDL and whitespace checks OK.
- `python ci/validate_doc_paths.py` — PASS before the generated validation manifest was embedded: **81 unique documented repository paths**.
- `python ci/audit_exceptions.py --root . --exceptions ci/vulnerability-exceptions.json --lock ci/requirements/root-production.lock` — PASS.
- Parsed every `aos/**/*.json` file with Python `json` — PASS, **68 JSON files**.
- Exact AST equivalent of `TestPublicErrorSafety.test_public_api_fail_calls_do_not_use_raw_exception_strings` — PASS, **0 offenders**.
- Chat API f-string SQL source scan — PASS, **0 offenders**.
- Chat transaction/privacy source scan for internal `commit`, whole-transaction `rollback()` and raw `get_traceback()` — PASS, **0 offenders**.

Total directly executable unit/source/versioning tests above: **66 passed**.

## Environment-blocked / unavailable

- `aos.api.chat.tests.test_chat_database_contracts` could not import because this container does not have Frappe installed (`ModuleNotFoundError: frappe`). The database-backed tests are included in the archive for Bench/staging.
- `aos.tests.test_dynamic_sql_safety` could not import for the same missing-Frappe reason.
- `aos.tests.test_public_error_safety` could not import for the same missing-Frappe reason; its exact raw-`fail(...)` AST rule was executed separately and passed with 0 offenders.
- `bench` is not installed in this container, so no site/database test command was claimed as passed.
- `ci/repository-hygiene-and-secrets.sh`, `ci/check-python314-compatibility.sh`, and `ci/repository-quality.sh` correctly stop because the repository requires Python **3.14.6** while this container has Python **3.13.5**.
- The optional companion-service directories for notification-delivery, moderation and analytics-pipeline are not present in this cumulative backend archive, so their pytest suites were unavailable here.

## Required staging validation

```bash
bench --site <site> run-tests --app aos --module aos.api.chat
bench --site <site> run-tests --app aos --module aos.api.chat.tests.test_chat_database_contracts
bench --site <site> run-tests --app aos --module aos.tests.test_core_feature_flows
bench --site <site> run-tests --app aos --module aos.api.live
bench --site <site> run-tests --app aos
ci/repository-hygiene-and-secrets.sh
ci/repository-quality.sh
```
