# AOS disaster restore

The restore is destructive. Use an isolated rehearsal or approved replacement host and follow `docs/production/runbooks/backup-and-restore.md`.

The current restore flow:

- Accepts a verified encrypted `.age` artifact or a development plaintext set.
- Decrypts into a mode-0700 temporary directory and cleans it on all exits.
- Verifies decrypted checksums before restore.
- Discovers `.tgz`, `.tar.gz`, and `.tar` native public/private archives independently.
- Rejects ambiguous matches and never selects private files as public files.
- Passes the correct Bench public/private flags.
- Verifies representative restored files and expected checksums.
- Records restore state for strict rehearsal validation.

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/rehearsal-backup.env \
  /home/aos/aos/infra/backup/restore.sh \
  --backup /var/backups/aos/encrypted/<backup>.tar.gz.age \
  --confirm DESTROY_AND_RESTORE
```

Do not switch traffic until migrations, production/rehearsal configuration, application health, job diagnostics, and file evidence pass.
