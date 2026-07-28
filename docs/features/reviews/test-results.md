# Reviews test-results report

Validation date: 2026-07-28. Artifact environment: Python 3.13.5, no Bench/Frappe package, no MariaDB/Redis/workers, no Git metadata, no network-installed Ruff or Semgrep. The project manifest requires Python 3.14.6.

## Executed and passed

| Command | Outcome |
|---|---|
| `python -m compileall -q aos` | Passed; all Python sources compiled. |
| `python -m unittest aos.api.reviews.tests.test_validation aos.api.reviews.tests.test_reviews_contracts -v` | Passed: 26 tests, 0 failures/errors. |
| `python ci/validate_rate_limit_coverage.py .` | Passed: 207 whitelisted endpoint policies validated. |
| `python ci/validate_doc_paths.py .` | Passed: 72 unique documented repository paths. |
| `python ci/validate_repository.py .` | Passed JSON/TOML/YAML parsing, duplicate-key, conflict marker, debug statement, patch DDL and whitespace checks. |
| `python ci/validate_actions.py .` | Passed workflow pinning, permissions, runner and timeout policy. |
| `python ci/validate_foundation.py .` | Passed version manifest, source/lock pairs, hashes and service matrices. |
| `python ci/validate_monitoring.py .` | Passed: 43 Prometheus alerts, 7 scrape jobs, 1 Alertmanager target and private monitoring units. |
| `python ci/validate_deployment.py .` | Passed deployment order, guarded migration behavior, immutable manifest, protected environments and dry-run checks. |
| `python ci/validate_companion_safety.py .` | Passed synchronized field sanitization for 8 services, recovery dispatch for 5 services and behavioral test contracts. |
| `python ci/validate_nginx_policy.py .` | Passed signed-callback and baseline Nginx rate-limit policy. |
| `python ci/validate_systemd.py .` | Passed syntax checks for 6 unit files. |

The 26 fast Reviews tests verify strict ratings/text/pagination/Media inputs, accepted fields, lifecycle schema, transport sanitization, public privacy, moderation generation, migration registration/batching/non-destructive Review duplicate handling, Accounts deletion policy, central reports, canonical Notifications, Ad/Seller reconciliation and endpoint rate-limit coverage.

## Executed and failed because of environment/tooling

| Command | Outcome |
|---|---|
| `python -m unittest aos.api.reviews.tests.test_eligibility -v` | Not executable: `ModuleNotFoundError: frappe`. One test module failed to import; no eligibility tests ran. |
| `bash ci/validate-compose.sh` | Not executable: requires Python 3.14.6; environment provides 3.13.5. |
| `bash ci/check-python314-compatibility.sh` | Not executable for the same Python version requirement. |
| `python ci/validate_lock_credentials.py .` | Not executable because the delivered source ZIP intentionally has no `.git`; the validator calls `git ls-files`. |
| `python ci/repository_hygiene.py .` | Not executable because the delivered source ZIP intentionally has no `.git`; the validator calls `git ls-files`. |
| `ruff --version` | Ruff is not installed. |
| `semgrep --version` | Semgrep is not installed. |
| `bench --version` | Bench is not installed. |
| `python -c 'import frappe'` | Frappe is not installed. |

## Executed and failed with unchanged baseline findings

`python ci/compose_policy.py docker-compose.yml` returned 16 findings: public-port binding policy findings for Qdrant, MinIO (two ports), Translation, Image Search, Background Removal, Video, Moderation, Search Ranking, Notification, Analytics, Tileserver, Nominatim and Valhalla; plus unpinned image findings for Tileserver and Valhalla. Running the identical command against the pristine cumulative baseline produced the same 16 findings. Reviews did not modify `docker-compose.yml`; these are pre-existing foundation findings, not Reviews regressions.

## Not executable in this environment

The following required staging checks were not claimed as passed:

- full `bench --site <site> run-tests --app aos`;
- Reviews Frappe integration/database/concurrency tests;
- Media, Accounts, Auth, Localization, Seller, Ads, moderation, Notification and transactional-outbox regression tests;
- MariaDB migration/index/unique-constraint execution;
- Redis/cache/worker and signed callback integration;
- Ruff lint/format, Semgrep and dependency audit requiring unavailable tools/runtime/network;
- Docker Compose runtime validation requiring the project Python/toolchain and services.

Run these on the production-equivalent staging Bench before release. Exact deployment and test commands are in `migration-deployment-guide.md` and `testing.md`.
