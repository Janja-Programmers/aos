# Encrypted backup, restore, and rehearsal

## Production policy

Production uses `BACKUP_LOCAL_RETENTION_MODE=encrypted-artifact` and `BACKUP_ENCRYPTION_METHOD=age`.

The backup process:

1. Validates encryption configuration before sensitive data is created.
2. Creates database, public files, private files, configured volume, map, and configuration snapshots inside a mode-0700 temporary workspace.
3. Verifies the plaintext set and representative archive structure.
4. Encrypts the complete verified set with `age`.
5. Verifies the encrypted artifact SHA-256 and metadata.
6. Removes the temporary plaintext workspace on success and failure.
7. Retains only `.tar.gz.age`, checksum, and non-secret metadata.
8. Copies only the encrypted artifact offsite.

The repository does not claim cryptographic secure deletion on copy-on-write, journaled, snapshotting, or virtualized filesystems. It minimizes plaintext lifetime and requires restrictive permissions. Operators needing stronger at-rest guarantees should place the temporary workspace on an encrypted block device or encrypted filesystem and document the operational verification.

## Configuration

Start from `infra/backup/backup.env.example`. Store the real recipient and identity outside the repository. Required production values include:

```bash
AOS_ENVIRONMENT=production
BACKUP_ENCRYPTION_REQUIRED=true
BACKUP_ENCRYPTION_METHOD=age
BACKUP_AGE_RECIPIENT=<non-placeholder-age-recipient>
BACKUP_AGE_IDENTITY_FILE=/etc/aos/secrets/backup-age-identity.txt
BACKUP_LOCAL_RETENTION_MODE=encrypted-artifact
RESTORE_REHEARSAL_REQUIRE_FILES=true
```

The identity file should be root/backup-operator owned, mode 0600. Never log or copy its contents into markers.

## Create and verify

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/backup.sh

latest="$(find /var/backups/aos/encrypted -maxdepth 1 -type f -name '*.tar.gz.age' -printf '%T@ %p\n' \
  | sort -n | tail -1 | cut -d' ' -f2-)"
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/backup_crypto.py verify --input "$latest"
find /var/backups/aos -maxdepth 1 -type d -regextype posix-extended \
  -regex '.*/[0-9]{8}T[0-9]{6}Z' -print
find /var/backups/aos/.plaintext-work -mindepth 1 -print 2>/dev/null
```

Expected: encryption verification passes; both plaintext searches return no retained backup set.

## Offsite copy

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/offsite-copy.sh \
  "$latest"
```

Use the exact arguments expected by the configured mode. Expected: the marker records `age` and the encrypted artifact identity/checksum; no plaintext path is transferred.

## Full restore

Run only in an isolated rehearsal or approved recovery environment:

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/rehearsal-backup.env \
  /home/aos/aos/infra/backup/restore.sh \
  --backup "$latest" \
  --confirm DESTROY_AND_RESTORE
```

The restore decrypts into a mode-0700 temporary directory, verifies the decrypted backup, selects `.tgz`, `.tar.gz`, or `.tar` public/private archives independently, passes the correct Bench restore flags, verifies representative restored files/checksums, writes restore state, and removes decrypted data on every exit path. A missing or wrong identity fails with a sanitized error.

## Full rehearsal

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/rehearsal-backup.env \
  /home/aos/aos/infra/backup/restore-rehearsal-checklist.sh \
  --backup "$latest" --run-tests --mark-passed
```

With `RESTORE_REHEARSAL_REQUIRE_FILES=true`, both public and private archives and representative checksum evidence are mandatory. Missing archive, missing restored file, checksum mismatch, migration/config/health/job failure, or test failure exits before the atomic marker is written.

An intentionally database-only rehearsal requires both:

```bash
RESTORE_REHEARSAL_REQUIRE_FILES=false
RESTORE_REHEARSAL_ALLOW_DATABASE_ONLY=true
```

Its marker states `RESTORE_REHEARSAL_MODE=database-only`. Production readiness rejects it whenever production policy requires full restoration.

## Readiness

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.utils.backup_readiness.assert_backup_readiness_ready
```

Expected production checks include encryption required/method/tooling, encrypted local retention, no retained plaintext, encrypted checksum verification, encrypted offsite evidence, and a fresh full marker referring to the latest backup.
