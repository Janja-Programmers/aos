# AOS Production Operations

## Service status

```bash
cd /home/aos/aos
docker compose ps
docker compose ps qdrant image-search background-removal translation minio livekit
docker stats --no-stream
curl http://127.0.0.1:8110/health
curl http://127.0.0.1:8110/ready
curl http://127.0.0.1:8120/health
curl http://127.0.0.1:8120/ready
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
sudo supervisorctl status
sudo systemctl status nginx
```

## Logs

```bash
docker compose logs --tail=200 <service>
docker compose logs --tail=200 image-search
docker compose logs --tail=200 background-removal
docker compose logs --tail=200 translation
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

## Background-removal service check

Directly test the private service with a known image:

```bash
curl -X POST http://127.0.0.1:8120/remove-background \
  -F "image=@/path/to/test-image.jpg" \
  --output /tmp/aos-removed-bg.png
file /tmp/aos-removed-bg.png
```

Use this when the Flutter image editor reports background-removal failures. If direct service testing works but the app fails, inspect media object ownership, rate limits, and `aos.api.v1.media.remove_background` logs.

## Translation service check

Directly test the private translation service:

```bash
curl -X POST http://127.0.0.1:8100/translate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello, how are you?",
    "source_language": "eng_Latn",
    "target_language": "swh_Latn"
  }'
```

Check Frappe connectivity:

```bash
cd /home/aos/frappe-bench
bench --site <site> console
```

```python
from aos.integrations.ai.translation_client import health_check, ready_check
print(health_check())
print(ready_check())
```

If direct service testing works but the app fails, inspect chat membership, deleted-message rules, rate limits, `AOS Message Translation` cache records, and `aos/api/chat/translate_message.py` logs.

## Incident priorities

1. Protect data and preserve logs.
2. Remove unhealthy public traffic if needed.
3. Restore the last verified release or backup.
4. Document the incident, cause, and preventive action.
