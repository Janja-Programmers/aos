# AOS production release checklist

## Exact release evidence

- [ ] `CI / Required Gate` passed for the exact commit.
- [ ] Schema-2 release manifest records the Git SHA, archive SHA-256, all static external image digests, all source-built service/context fingerprints, and required runtime image variables.
- [ ] Every locally built application image is separately identified and verified by its actual deployed OCI digest; a source-context fingerprint alone is insufficient.
- [ ] Runtime-supplied image references (including Valhalla) resolve to reviewed immutable digests in the deployed Compose configuration.
- [ ] Staging deployment and smoke checks passed first.
- [ ] Production GitHub Environment approval was granted.
- [ ] `main` protection and protected production Environment reviewers were verified in repository settings.
- [ ] `AOS_PRODUCTION_DEPLOYMENT_ENABLED=true` was explicitly authorized for this production release; leave it unset for staging-only rehearsals.
- [ ] No real secrets, generated files, runtime logs, plaintext backups, or decrypted workspaces are tracked.

## Migration review

- [ ] `aos.utils.migration_preflight.assert_migration_preflight_ready` passed.
- [ ] Pending patches and schema-changing code were manually reviewed.
- [ ] Previous migration-failure marker is absent/resolved.
- [ ] Required outbox fields and backfill patch are present.
- [ ] Disk headroom, long transactions, metadata locks, workers, outbox backlog, and dead letters are acceptable.
- [ ] Remote deployment uses `scripts/deploy/run-migrate.sh` or an equivalent guarded wrapper.

## Backup and restore

- [ ] Latest local retained backup is verified and encrypted.
- [ ] No plaintext backup set/workspace remains.
- [ ] Encrypted offsite marker refers to the latest backup.
- [ ] Full restore rehearsal includes database, public files, private files, representative checksums, migrations, config, health, and job diagnostics.
- [ ] Rehearsal marker is fresh, atomic, and refers to the latest backup.
- [ ] Exact backup ID and rollback decision owner are recorded.

## Outbox and workers

- [ ] Fresh-site schema installers and invariant indexes complete without duplicates.
- [ ] Queue depth and oldest queued age are within policy.
- [ ] No stale claims, overdue published callbacks, or unexplained dead letters remain.
- [ ] Signed callback routes and bounded callback edge limits are healthy.

## Metrics and alerts

- [ ] Redis-backed Frappe metrics aggregate across workers.
- [ ] Prometheus config and rules pass `promtool`.
- [ ] Alertmanager config passes `amtool`.
- [ ] Prometheus reports its Alertmanager target.
- [ ] Required `up == 0` and critical absent-metric alerts are active for production targets.
- [ ] Synthetic staging alert routing was verified.

## Post-deployment

- [ ] Operational health passes.
- [ ] Background-job diagnostics pass.
- [ ] Outbox publisher runs successfully.
- [ ] Metrics scrape succeeds without sensitive labels.
- [ ] Prior exact release manifest and verified backup remain available for rollback.

## Localization

- [ ] `AOS Settings` default country, currency, and language resolve to valid enabled master records.
- [ ] Locale bundle returns schema version `2.0` and the expected defaults.
- [ ] Guest header/default resolution and authenticated stored-preference resolution pass smoke tests.
- [ ] Location pagination returns stable, non-overlapping pages and excludes inactive rows.
- [ ] `idx_aos_location_country_active_order` exists after migration.
- [ ] Country market lock is enforced after seller ad activity while currency and language remain independently editable.
