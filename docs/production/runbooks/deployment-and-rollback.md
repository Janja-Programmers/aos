# Deployment, failed migration, and rollback

## Before approval

Review the exact release manifest, image digests, pending patches, schema-changing code, encrypted backup identity, full restore-rehearsal marker, outbox backlog, dead letters, and prior migration-failure marker. Automated preflight detects common blockers but cannot prove every migration is non-destructive.

## Required environment declarations

```bash
export REMOTE_BENCH_ROOT=/home/aos/frappe-bench
export REMOTE_PROJECT_ROOT=/srv/aos/runtime
export FRAPPE_SITE=<site>
export REMOTE_RELEASE_ROOT=/srv/aos/releases
export REMOTE_PROJECT_ROOT=/srv/aos/runtime
export REMOTE_APPLY_RELEASE_PATH=/usr/local/sbin/aos-apply-release
export REMOTE_APPLY_RELEASE_SHA256=<reviewed-64-character-sha256>
```

The repository provides `scripts/deploy/apply-release.py` as the concrete reviewed candidate. On each host an authorized operator must inspect it and install the exact checked-out file independently (never from the just-downloaded release archive):

```bash
sudo install -o root -g root -m 0755 scripts/deploy/apply-release.py /usr/local/sbin/aos-apply-release
sha256sum /usr/local/sbin/aos-apply-release
# Copy the actual hash into the corresponding protected GitHub Environment variable.
```

Create `REMOTE_PROJECT_ROOT` as a persistent host-controlled directory with a private mode-`0600` `.env` and reviewed model/graph/Firebase mount paths. Neither secrets nor host mount data belong in the release archive. `REMOTE_BENCH_ROOT/apps/aos` must first be deliberately provisioned as a symlink to a fully backed-up, reviewed app source; never allow a first deployment to overwrite an existing Bench directory. Stage this change independently and verify it with Bench before any release attempt. The host applier refuses to replace a normal app directory; it verifies archive policy and exact resolved image inventory, activates the digest-locked Compose without building, then atomically points Bench to the immutable extracted source. Deployment runs guarded migration and `bench restart` only after the applier returns successfully; application-only rollback restarts Bench without migration or database restore. Do not overlap rollback with migration/restart. Recheck this cross-command coordination before permitting unattended rollback.

`REMOTE_APPLY_RELEASE_PATH` must be one reviewed absolute executable path without arguments and must accept exactly five positional inputs: the verified archive, release manifest, release commit, promoted image lock and immutable no-build Compose. It must activate the provided Compose and reject any omitted or altered lock. The deployment wrapper first checks both registries and archive integrity, then uploads the policy verifier and rechecks everything independently on the host. The remote Bench Python must have PyYAML available; the deployment account must have pinned `crane` 0.21.7 and separately provisioned **read-only** GHCR authentication (never put tokens in the release artifacts). Pin its reviewed executable checksum using `REMOTE_APPLY_RELEASE_SHA256` for each protected deployment Environment (and the authorized rollback operator). Both wrappers verify the remote binary immediately before invocation, and check retained policy verifier files against the original archive. An older three-argument applier is not authorized. The repository invokes `scripts/deploy/run-migrate.sh` unconditionally after the reviewed applier succeeds.

## Dry-run validation

```bash
export DEPLOY_ENVIRONMENT=staging
export RELEASE_COMMIT="$(git rev-parse HEAD)"
export RELEASE_ARTIFACT="$(mktemp -d)/aos-release.tar.gz"
export RELEASE_MANIFEST="$(dirname "$RELEASE_ARTIFACT")/release-manifest.json"
export CI_GATE_VERIFIED=true
git archive --format=tar.gz --output="$RELEASE_ARTIFACT" "$RELEASE_COMMIT"
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
export ROLLBACK_ARTIFACT=/secure/releases/<prior-sha>/aos-release.tar.gz
export ROLLBACK_IMAGE_LOCK=/secure/releases/<prior-sha>/release-image-lock.json
export ROLLBACK_LOCKED_COMPOSE=/secure/releases/<prior-sha>/release-compose.locked.yml
export VERIFIED_BACKUP_ID=<exact-verified-backup-id>
export REMOTE_RELEASE_ROOT=/srv/aos/releases
export REMOTE_APPLY_RELEASE_PATH=/usr/local/sbin/aos-apply-release
export REMOTE_BENCH_ROOT=/home/aos/frappe-bench
export FRAPPE_SITE=<site>
scripts/deploy/rollback.sh --dry-run
```

For a real application-only rollback, independently review the migration history and confirm that the **previous application is compatible with the current database schema**. Inspect the exact prior archive/manifest, their recorded hashes and image digests, verified backup evidence, incident owner, and the actual reviewed release-applier executable. Obtain separate incident/change approval and configure the trusted SSH known-hosts file/credentials. Only then set `ROLLBACK_APPROVED=true` and `ROLLBACK_DB_DECISION=application-only` for the operator-executed rollback.

The rollback wrapper validates the prior archive, manifest, promoted image lock and no-build Compose locally, and checks all four identical retained artifacts by checksum and registry digest on the host immediately before invoking the reviewed five-argument `REMOTE_APPLY_RELEASE_PATH` used for deployments; it runs operational health/job checks after the application release switch. A release without its exact retained lock/Compose cannot be rolled back using this automated application-only wrapper. The wrapper does not perform `bench migrate` or restore the database. It does not accept arbitrary remote rollback command text. The exact prior archive/manifest **must already be retained** under `REMOTE_RELEASE_ROOT/<prior-sha>/`; a missing or mismatched release fails closed.

`VERIFIED_BACKUP_ID` identifies operator-reviewed backup evidence; merely providing an ID does not independently verify backup usability. Do not perform a database restore under the application-only authorization. Database restore is a separate destructive incident operation, permitted only when application/schema compatibility requires it, after explicit approval and a fresh complete restore-rehearsal marker.
