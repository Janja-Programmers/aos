# AOS Backup Policy

## Scope

The automated backup covers:

- Frappe/MariaDB database using `bench backup`
- Frappe public and private files
- Frappe site configuration
- MinIO Docker volume
- Qdrant Docker volume for image-search vectors
- Optional Nominatim Docker volume
- Generated MBTiles and Valhalla artifacts
- Map manifest and production infrastructure configuration
- Checksums and deployment metadata

Nominatim data is reproducible from the PBF and is disabled by default because its volume can be large. Enable it only when recovery-time requirements justify the storage cost.

Qdrant stores image-search vectors. Backing it up improves recovery time, but vectors can also be rebuilt from Active AOS ads and saved ad images using `aos.integrations.ai.image_search_tasks.rebuild_image_search_index`. Background removal has no generated index or persistent service data; processed outputs are normal Frappe files and are covered by the Frappe public/private files backup.

## Schedule and retention

- Daily backup at approximately 02:30 server time
- Default local retention: 14 days
- Keep at least one encrypted off-server copy
- Keep weekly/monthly snapshots according to business requirements

A backup stored only on the production server is not a disaster-recovery backup.

## Configuration

Install `/etc/aos/backup.env` from `infra/backup/backup.env.example`. Set permissions to `0600` and ownership to `aos:aos`.

## Manual backup

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  /home/aos/aos/infra/backup/backup.sh
```

## Verification

```bash
/home/aos/aos/infra/backup/verify-backup.sh /var/backups/aos/<timestamp>
```

Test an actual restore on a separate server regularly. Checksum verification alone does not prove that the application can be recovered.

## Encryption and remote storage

Use an encrypted transport and encrypted destination. The backup can contain API secrets, private user files, database credentials, and personal information. Restrict access and audit downloads.
