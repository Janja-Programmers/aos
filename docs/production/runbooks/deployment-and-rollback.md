# Deployment, failed migration, and rollback

## Before approval

Review the exact release manifest, image digests, pending patches, schema-changing code, encrypted backup identity, full restore-rehearsal marker, outbox backlog, dead letters, and prior migration-failure marker. Automated preflight detects common blockers but cannot prove every migration is non-destructive.

## Required environment declarations

```bash
export REMOTE_BENCH_ROOT=/home/aos/frappe-bench
export FRAPPE_SITE=<site>
export REMOTE_RELEASE_ROOT=/srv/aos/releases
export REMOTE_APPLY_RELEASE_PATH=/usr/local/sbin/aos-apply-release
```

`REMOTE_APPLY_RELEASE_PATH` must be one reviewed absolute executable path without arguments. It applies the exact archive/manifest/commit only. It does not control whether migrations run: the repository invokes `scripts/deploy/run-migrate.sh` unconditionally after the release-applier succeeds.

## Dry-run validation

```bash
export DEPLOY_ENVIRONMENT=staging
export RELEASE_COMMIT=0123456789abcdef0123456789abcdef01234567
export RELEASE_ARTIFACT=/tmp/aos-release.tar.gz
export RELEASE_MANIFEST=/tmp/release-manifest.json
export CI_GATE_VERIFIED=true
python scripts/deploy/release_manifest.py create "$RELEASE_MANIFEST" "$RELEASE_ARTIFACT" "$RELEASE_COMMIT"
python ci/validate_deployment.py .
scripts/deploy/preflight.sh --dry-run
scripts/deploy/deploy.sh --dry-run
REMOTE_BENCH_ROOT="$REMOTE_BENCH_ROOT" FRAPPE_SITE="$FRAPPE_SITE" \
  scripts/deploy/run-migrate.sh --dry-run
scripts/deploy/smoke.sh --dry-run
```

Repeat with `DEPLOY_ENVIRONMENT=production` and `PRODUCTION_APPROVAL_CONTEXT=true` only for validation. Dry-run mode never contacts a host.

Expected: output explicitly states that the immutable release is applied, guarded migration is unconditional, and smoke checks occur only after migration success.

## Guarded migration and failure evidence

The deployment script remotely invokes the versioned repository path:

```bash
REMOTE_BENCH_ROOT=/home/aos/frappe-bench \
FRAPPE_SITE=<site> \
RELEASE_COMMIT=<exact-sha> \
AOS_MIGRATION_FAILURE_MARKER=/var/lib/aos/deployment/last-migration-failed.env \
/home/aos/frappe-bench/apps/aos/scripts/deploy/run-migrate.sh
```

A failed `bench migrate`:

- Returns nonzero and stops deployment.
- Atomically writes a mode-`0600` marker containing only site, release commit, timestamp, and `MIGRATION_COMMAND_FAILED`.
- Prevents post-deployment smoke checks.
- Leaves preflight blocked until a later guarded migration succeeds.

Do not delete the marker to bypass preflight. Review database state and patch logs first. A successful guarded migration clears the marker.

## Post-migration checks

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.utils.operational_health.assert_operational_health_ready
bench --site <site> execute aos.utils.job_monitoring.assert_job_monitoring_ready
bench --site <site> execute aos.services.transactional_outbox.outbox_monitoring_summary
```

Expected: health and job checks pass; outbox backlog and dead-letter counts stay within production policy.

## Rollback decision

Before rollback, determine whether guarded migration completed:

```bash
test -f /var/lib/aos/deployment/last-migration-failed.env \
  && sudo cat /var/lib/aos/deployment/last-migration-failed.env \
  || echo 'No unresolved migration-failure marker.'
```

A missing marker means only that no unresolved failure is recorded; confirm Bench migration logs and release metadata. Record the conclusion in the incident timeline.

## Rollback dry run

```bash
export DEPLOY_ENVIRONMENT=production
export ROLLBACK_COMMIT=<exact-prior-sha>
export ROLLBACK_MANIFEST=/secure/releases/<prior-sha>/release-manifest.json
export VERIFIED_BACKUP_ID=<exact-verified-backup-id>
scripts/deploy/rollback.sh --dry-run
```

For a real rollback, use the exact prior archive and digest manifest. Application rollback and destructive database restore are separate decisions. Restore the database only when the prior application cannot operate against the migrated schema, after explicit incident approval, and only from the exact verified backup with a current full restore-rehearsal marker.
