# AOS Disaster Restore

## Safety

The restore script is destructive. Restore into a replacement/staging server first whenever possible.

## 1. Prepare the replacement server

Install Docker, Frappe Bench, Nginx, Certbot, the AOS repository, and the same application dependencies. Checkout the Git commit recorded in `metadata.env` when possible.

## 2. Restore configuration

Create `/etc/aos/backup.env`, repository `.env`, Frappe site configuration, TLS certificates, DNS, and firewall rules. Do not blindly copy expired certificates or obsolete secrets.

## 3. Verify the selected backup

```bash
./infra/backup/verify-backup.sh /var/backups/aos/<timestamp>
```

## 4. Run restore

```bash
sudo -u aos AOS_BACKUP_ENV_FILE=/etc/aos/backup.env \
  ./infra/backup/restore.sh \
  --backup /var/backups/aos/<timestamp> \
  --confirm DESTROY_AND_RESTORE
```

The script stops services, restores selected Docker volumes and map artifacts, restores Frappe with public/private files, runs migrations, and restarts services.

## 5. Verify

- `docker compose ps` reports healthy services
- `curl http://127.0.0.1:8110/health` succeeds
- `curl http://127.0.0.1:8110/ready` succeeds
- Frappe login succeeds
- Private/public files load
- Seller location APIs work
- Nominatim search and reverse geocoding work
- Valhalla routing works
- TileServer style and tiles load
- MinIO objects are available
- LiveKit signaling and media work
- Image search returns expected Active ads

If the restored Qdrant volume is missing or stale, rebuild vectors before switching traffic:

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index --kwargs '{"dry_run": true}'
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index
```

Do not switch production DNS until verification is complete.
