# Testing

## Fast source validation

```bash
python -m compileall -q aos
python -m unittest \
  aos.api.reviews.tests.test_validation \
  aos.api.reviews.tests.test_reviews_contracts -v
python ci/validate_rate_limit_coverage.py .
```

## Repository, security and deployment validation

```bash
python ci/validate_doc_paths.py .
python ci/validate_repository.py .
python ci/validate_actions.py .
python ci/validate_foundation.py .
python ci/validate_monitoring.py .
python ci/validate_deployment.py .
python ci/validate_companion_safety.py .
python ci/validate_nginx_policy.py .
python ci/validate_systemd.py .
python ci/compose_policy.py docker-compose.yml
bash ci/validate-compose.sh
bash ci/check-python314-compatibility.sh
bash ci/repository-quality.sh
bash ci/repository-hygiene-and-secrets.sh
bash ci/semgrep.sh
bash ci/dependency-audit.sh
```

Some shell gates require Python 3.14, Git metadata, installed security tools, internet/package indexes or container services. The artifact report distinguishes executed results from environment-blocked checks.

## Frappe/Bench validation

```bash
bench --site <site> run-tests --app aos --module aos.api.reviews.tests.test_eligibility
bench --site <site> run-tests --app aos
```

Run the full app suite because Reviews changes touch Media, Accounts public identity, Sellers/Ads aggregates, Auth, blocking/chat eligibility, Moderation, Notifications, transactional outbox and account deletion.

Tests cover strict ratings/text/pagination/Media policy, accepted fields, eligibility, self/blocked/duplicate protection, transport sanitisation, lifecycle schema, privacy, generation-safe moderation, migration registration/batching/non-destructive duplicate handling, report-reason integration, canonical notification integration, aggregate reconciliation and rate-limit coverage. Database integration, callbacks and concurrency require the Frappe/MariaDB staging runtime.
