# Production foundation smoke-test checklist

Run destructive or provider-facing cases only in staging or an isolated rehearsal environment. Replace placeholders and keep credentials in restricted environment files.

## 1. Migrations and patch idempotency

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.utils.production_config.assert_production_config_ready
bench --site <site> execute aos.utils.migration_preflight.assert_migration_preflight_ready
/home/aos/aos/scripts/deploy/run-migrate.sh --site <site>
bench --site <site> execute aos.patches.v1_0.finalize_outbox_failure_reconciliation.execute
bench --site <site> execute aos.patches.v1_0.finalize_outbox_failure_reconciliation.execute
```

Expected: migration succeeds, no failure marker remains, and the second patch execution is idempotent.

## 2. Retryable work failure

Run the real Redis/RQ integration suites:

```bash
pytest -q \
  infra/video-processing/tests/test_real_redis_rq_lifecycle.py \
  infra/moderation/tests/test_real_redis_rq_lifecycle.py \
  infra/search-ranking/tests/test_real_redis_rq_lifecycle.py \
  infra/notification-delivery/tests/test_real_redis_rq_lifecycle.py \
  infra/analytics-pipeline/tests/test_real_redis_rq_lifecycle.py
```

Expected: first transient work attempt enters RQ scheduled retry, the scheduled registry requeues it, the later attempt succeeds, no premature failure callback is created, and the external effect count is one.

## 3. Exhausted work failure

Force a retryable test error through all configured work retries.

Expected:

- terminal `work_failed` result persists only after exhaustion;
- failure callback is independently queued;
- Frappe domain/job/outbox update atomically;
- outbox becomes `Completed With Failure`;
- the next publisher run does not rerun work.

## 4. Callback response loss

Use a staging proxy that forwards the signed callback to Frappe but drops the response.

Expected: Frappe commits once; callback retry receives idempotent success; domain counters/timestamps/notifications do not repeat; external work remains one execution.

## 5. Callback during publisher lease

Run:

```bash
bench --site <test-site> run-tests --app aos \
  --module aos.tests.test_transactional_outbox_recovery
```

Expected: a matching callback succeeds while the row has a publisher lease and clears the lease on completion.

## 6. Dispatch uncertainty and manual review

Use a proxy that forwards a dispatch request but withholds the response.

Expected:

- domain object remains nonterminal;
- outbox enters `Dispatch Uncertain`;
- matching callback is accepted;
- unresolved bounded attempts lead to `Manual Review`, not false work failure;
- a later authoritative callback can still complete manual review.

## 7. Newer-generation convergence

Create a staging mismatch where Frappe is generation 1 and the companion reports generation 5.

Expected: Frappe stores the generation floor, never proposes below 5, queries the active/completed state, and either converges or reaches bounded manual review. It must not repeatedly propose generation 2.

## 8. Callback-complete repair

Simulate Frappe committing a terminal callback while the client loses the response or the outbox remains nonterminal.

Expected: signed status evidence plus consistent domain/service-job state repairs the outbox to `Completed` or `Completed With Failure`; conflicting evidence goes to bounded manual review.

## 9. Callback dead letter and replay

Make callback routing fail until callback attempts exhaust, then repair it and call:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -H '<service-signature-header>: <computed-signature>' \
  --data '{"job_id":"<service-job-id>","idempotency_key":"<stable-work-id>"}' \
  http://127.0.0.1:<port>/internal/jobs/callback/replay
```

Expected: callback completes without another work execution.

## 10. Explicit work replay

After reviewing a `Completed With Failure` row:

```bash
bench --site <site> execute aos.tasks.outbox.authorize_terminal_work_replay \
  --kwargs '{
    "outbox_name":"<outbox>",
    "expected_idempotency_key":"<stable-key>",
    "additional_attempts":2
  }'
```

Expected: System Manager and exact-key checks pass, no old work/callback job is active, history is archived, and the same durable job/outbox reopens as `Queued`.

## 11. Notification uncertainty

Make the test provider accept a request while its response is lost.

Expected: no blind automatic resend. Resolve through signed `/internal/jobs/uncertainty/resolve` using `confirmed_accepted`, `confirmed_failed`, `approved_resend`, or `permanently_unresolved`. Approved resend is bounded and audited by digest.

## 12. Analytics cross-job dedupe

Submit the same explicit event ID through two different analytics work jobs and positions.

Expected: counters apply once; the global dedupe metric increases. Retry the same no-ID work after changing generated time; its fallback identity remains stable and counters still apply once.

## 13. Durable TTL and Redis restart

Record a completed work result, trigger reconciliation/callback replay, and verify Redis TTL does not decrease below the configured durable TTL. Restart a staging Redis configured with AOF.

Expected: result survives, stale indexes are cleaned if backing data is absent, callback resumes, and work does not rerun.

## 14. Monitoring and alerts

```bash
promtool check config /etc/prometheus/prometheus.yml
promtool check rules /etc/prometheus/rules/aos-alerts.yml
amtool check-config /etc/alertmanager/alertmanager.yml
curl --fail http://127.0.0.1:9090/-/ready
curl --fail http://127.0.0.1:9093/-/ready
```

Confirm alerts for work retry exhaustion, terminal work/outbox mismatch, callback retry stall, callback-complete repair, generation/reconciliation exhaustion, manual review, notification uncertainty, analytics dedupe anomalies, stale heartbeat/index, target down, and absent lifecycle metrics.

## 15. Encrypted backup and restore rehearsal

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/backup.sh
bench --site <site> execute aos.utils.backup_readiness.assert_backup_readiness_ready
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/rehearsal-backup.env \
  /home/aos/aos/infra/backup/restore-rehearsal-checklist.sh \
  --backup /var/backups/aos/encrypted/<backup>.tar.gz.age \
  --run-tests --mark-passed
```

Expected: only encrypted artifacts remain; database, public/private files, representative checksums, migrations, configuration, health, and job diagnostics pass before the marker is written.

## 16. Deployment and rollback

```bash
DEPLOY_ENVIRONMENT=staging scripts/deploy/preflight.sh --dry-run
DEPLOY_ENVIRONMENT=staging scripts/deploy/deploy.sh --dry-run
DEPLOY_ENVIRONMENT=production scripts/deploy/preflight.sh --dry-run
DEPLOY_ENVIRONMENT=production scripts/deploy/deploy.sh --dry-run
DEPLOY_ENVIRONMENT=production \
ROLLBACK_COMMIT=<40-char-prior-sha> \
ROLLBACK_MANIFEST=/path/to/prior-release-manifest.json \
VERIFIED_BACKUP_ID=<verified-backup-id> \
  scripts/deploy/rollback.sh --dry-run
```

Expected: immutable release, mandatory guarded migration, no smoke after migration failure, and exact prior release/backup rollback evidence.
