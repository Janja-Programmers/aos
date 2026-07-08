# AOS Offsite Backups

A local backup proves that the backup process can create an artifact. It does not protect against disk loss, server compromise, accidental deletion, or datacenter failure. Production must copy each verified backup to an off-server destination.

The built-in offsite helper is:

```bash
infra/backup/offsite-copy.sh /var/backups/aos/<timestamp>
```

`infra/backup/backup.sh` calls it automatically after local verification succeeds when `OFFSITE_BACKUP_MODE`, `OFFSITE_BACKUP_ENABLED`, or `REMOTE_COPY_COMMAND` is configured.

## Common settings

Set these in `/etc/aos/backup.env`:

```bash
OFFSITE_BACKUP_MODE=rsync   # disabled, rsync, s3, or custom
OFFSITE_SYNC_MARKER=/var/backups/aos/offsite-sync-passed.env
OFFSITE_MAX_SYNC_AGE_HOURS=26
```

After a successful copy, the helper writes:

```text
OFFSITE_SYNC_PASSED_AT_UTC=<UTC timestamp>
OFFSITE_BACKUP_ID=<backup directory name>
OFFSITE_BACKUP_MODE=<rsync|s3|custom>
```

`aos.utils.backup_readiness.backup_readiness_summary` checks that the marker is fresh and, when `OFFSITE_BACKUP_ID` is present, that it matches the latest local backup.

## Option A: rsync over SSH

Use this for Hetzner Storage Box, another VPS, or a private backup server.

```bash
OFFSITE_BACKUP_MODE=rsync
OFFSITE_RSYNC_TARGET=backup-user@backup-host:/srv/aos-backups
OFFSITE_RSYNC_SSH_KEY=/etc/aos/backup_rsync_ed25519
OFFSITE_RSYNC_EXTRA_ARGS=
```

Recommended SSH setup:

```bash
sudo -u aos ssh-keygen -t ed25519 -f /etc/aos/backup_rsync_ed25519 -N ''
sudo chmod 600 /etc/aos/backup_rsync_ed25519
sudo chown aos:aos /etc/aos/backup_rsync_ed25519
```

Add the public key to the remote backup account. Restrict that account to the backup destination where possible.

Manual test:

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/offsite-copy.sh /var/backups/aos/<timestamp>
```

## Option B: S3-compatible storage

Use this for AWS S3, Cloudflare R2, Backblaze B2 S3, MinIO on another server, or another S3-compatible provider.

```bash
OFFSITE_BACKUP_MODE=s3
OFFSITE_S3_BUCKET=aos-production-backups
OFFSITE_S3_PREFIX=aos-backups
OFFSITE_S3_ENDPOINT_URL=https://s3.example.com
OFFSITE_S3_STORAGE_CLASS=
OFFSITE_S3_EXTRA_ARGS=
```

Install AWS CLI and configure credentials outside the repo and outside committed files. For S3-compatible providers, use the provider endpoint in `OFFSITE_S3_ENDPOINT_URL`.

Manual test:

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/offsite-copy.sh /var/backups/aos/<timestamp>
```

## Option C: custom wrapper

Use this when the copy mechanism requires provider-specific encryption, `rclone`, `restic`, or another audited script.

```bash
OFFSITE_BACKUP_MODE=custom
REMOTE_COPY_COMMAND=/usr/local/sbin/upload-aos-backup
```

The wrapper receives the verified backup directory as its first argument and must exit non-zero if the remote copy fails.

Example wrapper shape:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
backup_dir="${1:?backup directory required}"
# Run audited provider-specific copy here.
# Exit non-zero on failure.
```

## Readiness verification

Run after a backup completes:

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.utils.backup_readiness.backup_readiness_summary
```

Expected production result:

```text
ready=true
unhealthy=0
degraded=0
offsite_backup_scope: healthy
```

A fresh local backup with no offsite marker is not production-ready.

## Restore from offsite storage

On a clean replacement server, first copy the selected backup directory back to the local backup root, preserving the directory structure:

```bash
/var/backups/aos/<timestamp>/
```

For rsync:

```bash
rsync -a backup-user@backup-host:/srv/aos-backups/<timestamp>/ /var/backups/aos/<timestamp>/
```

For S3-compatible storage:

```bash
aws s3 sync s3://aos-production-backups/aos-backups/<timestamp>/ /var/backups/aos/<timestamp>/ \
  --endpoint-url https://s3.example.com
```

Then verify and restore:

```bash
/home/aos/aos/infra/backup/verify-backup.sh /var/backups/aos/<timestamp>

sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/restore.sh \
  --backup /var/backups/aos/<timestamp> \
  --confirm DESTROY_AND_RESTORE
```
