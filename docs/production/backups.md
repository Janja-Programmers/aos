# AOS production backup policy

Production backups contain database data, public/private files, service volumes, configuration, and secrets. They must not remain plaintext on ordinary local storage.

Canonical procedures are in `docs/production/runbooks/backup-and-restore.md`.

## Enforced policy

- `BACKUP_LOCAL_RETENTION_MODE=encrypted-artifact`
- `BACKUP_ENCRYPTION_REQUIRED=true`
- `BACKUP_ENCRYPTION_METHOD=age`
- Production requires both native public and private file archives.
- Plaintext exists only in a restrictive temporary workspace and is removed on success and failure.
- Retention contains only the verified `.tar.gz.age` artifact and non-secret checksum/metadata sidecars.
- Offsite copy accepts only the encrypted artifact.
- Readiness fails for placeholder recipients, unavailable tooling, retained plaintext, invalid encrypted checksums, stale offsite evidence, or insufficient restore-rehearsal evidence.

Development-only plaintext retention must be selected explicitly and is rejected in production.

## Operations

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/backup.sh
cd /home/aos/frappe-bench
bench --site <site> execute aos.utils.backup_readiness.assert_backup_readiness_ready
```

The systemd timer and failure notification unit are documented in the canonical runbook. Never commit recipients, identities, credentials, or generated backup artifacts.
