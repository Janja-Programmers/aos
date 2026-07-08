# AOS Backup and Restore Verification

This checklist proves that AOS can be recovered, not merely backed up.

## What must be recoverable

A production recovery must cover all of these layers:

1. Frappe/MariaDB database backup from `bench backup`.
2. Frappe public files.
3. Frappe private files.
4. MinIO object data for AOS media and Shorts processing output.
5. Site configuration: `site_config.json`, `common_site_config.json`, and deployment `.env`.
6. Backup configuration from `/etc/aos/backup.env`.
7. Optional but recommended generated data: Qdrant image-search vectors, map artifacts, and Nominatim data when recovery time requires it.

A backup stored only on the same production server is not a disaster-recovery backup. Keep an encrypted off-server copy.

## 1. Configure backup env

Install the backup configuration:

```bash
sudo install -m 600 infra/backup/backup.env.example /etc/aos/backup.env
sudo chown aos:aos /etc/aos/backup.env
sudo nano /etc/aos/backup.env
```

For AOS production, keep these enabled unless you have an independently verified replacement:

```bash
INCLUDE_MINIO_DATA=true
INCLUDE_CONFIGURATION=true
INCLUDE_QDRANT_DATA=true
INCLUDE_MAP_ARTIFACTS=true
```

Set one of these for off-server backup coverage:

```bash
REMOTE_COPY_COMMAND=/usr/local/sbin/upload-aos-backup
# or
OFFSITE_BACKUP_CONFIGURED=true
```

## 2. Create and verify a backup

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/backup.sh
```

Then verify the generated artifact:

```bash
/home/aos/aos/infra/backup/verify-backup.sh /var/backups/aos/<timestamp>
```

The backup script also writes `VERIFIED_AT_UTC` after checksum/archive validation succeeds.

## 3. Run backup-readiness diagnostic

From the bench host:

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.utils.backup_readiness.backup_readiness_summary
```

Admin API endpoint:

```text
/api/method/aos.api.diagnostics.get_backup_readiness_status
```

The diagnostic is redacted. It reports whether backup env, scripts, latest backup artifact, MinIO coverage, configuration coverage, offsite copy configuration, and restore rehearsal evidence are present without exposing secrets or file contents.

## 4. Restore rehearsal on a clean test site/server

Use a separate staging/test server whenever possible. Do not rehearse on the live production site.

High-level flow:

```bash
# 1. Provision a clean server with the same AOS repo and Frappe app version.
# 2. Copy the selected backup directory to the test server.
# 3. Install /etc/aos/backup.env for the test server.
# 4. Restore destructively into the test site.

sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/restore.sh \
  --backup /var/backups/aos/<timestamp> \
  --confirm DESTROY_AND_RESTORE
```

After restore, run the rehearsal verification script:

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/restore-rehearsal-checklist.sh \
  --backup /var/backups/aos/<timestamp> \
  --run-tests \
  --mark-passed
```

`--mark-passed` writes the restore rehearsal marker consumed by `backup_readiness_summary`.

## 5. Restored-site verification gates

The restored site must pass:

```bash
bench --site <site> migrate
bench --site <site> execute aos.utils.production_config.production_config_summary
bench --site <site> execute aos.utils.operational_health.operational_health_summary
bench --site <site> execute aos.utils.job_monitoring.job_monitoring_summary
bench --site <site> execute aos.utils.backup_readiness.backup_readiness_summary
bench --site <site> run-tests --app aos
```

Manual smoke checks after restore:

1. Login succeeds.
2. Existing ads load with images.
3. Public and private files load.
4. MinIO-backed media opens.
5. Shorts processed media loads.
6. Maps style and tiles load.
7. Photon search, Nominatim reverse geocoding, and Valhalla routing work.
8. LiveKit signaling works.
9. Notification delivery can be triggered safely.
10. Image search and background removal service checks pass.

Do not switch production DNS or traffic until the restored site passes all gates.
