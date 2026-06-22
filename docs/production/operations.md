# AOS Production Operations

## Service status

```bash
cd /home/aos/aos
docker compose ps
docker compose ps qdrant image-search translation minio livekit
docker stats --no-stream
curl http://127.0.0.1:8110/health
curl http://127.0.0.1:8110/ready
sudo supervisorctl status
sudo systemctl status nginx
```

## Logs

```bash
docker compose logs --tail=200 <service>
docker compose logs --tail=200 image-search
docker compose logs --tail=200 qdrant
sudo journalctl -u aos-backup.service -n 200 --no-pager
sudo tail -n 200 /var/log/nginx/error.log
```

## Safe restart

```bash
docker compose restart <service>
cd /home/aos/frappe-bench && bench restart
sudo nginx -t && sudo systemctl reload nginx
```

## Disk and memory

```bash
df -h
du -sh /var/lib/docker /var/backups/aos /home/aos/aos/maps
free -h
sudo dmesg -T | grep -Ei 'oom|out of memory|killed process' | tail
```

## Certificates

```bash
sudo certbot renew --dry-run
systemctl list-timers | grep certbot
```

## Map-data update

Build new artifacts outside the live path, verify them, take a backup, then switch atomically using the deterministic scripts. Rebuild/import Nominatim only during a maintenance window.


## Image-search index maintenance

Dry run a rebuild:

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index --kwargs '{"dry_run": true}'
```

Queue a full rebuild:

```bash
cd /home/aos/frappe-bench
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index
```

Use this after image-search deployment, Qdrant restore issues, vector corruption, or suspected stale search results.

## Incident priorities

1. Protect data and preserve logs.
2. Remove unhealthy public traffic if needed.
3. Restore the last verified release or backup.
4. Document the incident, cause, and preventive action.
