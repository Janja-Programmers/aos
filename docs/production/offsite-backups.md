# AOS encrypted offsite backups

Production offsite transfer is encrypted-only. The destination receives the verified `age` artifact and sidecars, never a plaintext directory.

Configure one reviewed mode in `/etc/aos/backup.env`:

- `rsync` over restricted SSH.
- `s3` with credentials stored outside the repository.
- `custom` only through an audited wrapper.

The success marker records non-secret metadata: backup ID, mode, encryption method, artifact identity/checksum, and timestamp. Backup readiness requires the marker to be fresh and to refer to the latest encrypted backup.

Canonical commands and restore handling are in `docs/production/runbooks/backup-and-restore.md`.

Do not commit SSH keys, S3 credentials, webhook URLs, encryption identities, or real destinations. Prefer immutable/retention-locked destination policy where supported.
