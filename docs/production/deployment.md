# AOS production deployment

The controlled GitHub Environment workflow, immutable release manifest, migration preflight, guarded migration marker, staging-first sequence, and rollback procedure are authoritative in:

- `docs/production/ci-cd.md`
- `docs/production/runbooks/deployment-and-rollback.md`

The host preparation notes below remain applicable, but releases must use the exact reviewed commit and image digests; do not deploy a floating branch or tag.

## 1. Prepare the server

Use Ubuntu 24.04, create the non-root `aos` user, enable SSH keys, disable password authentication, and configure the Hetzner firewall.

Required public ports:

- `22/tcp` from trusted administrator addresses
- `80/tcp`
- `443/tcp`
- `7881/tcp` for LiveKit TCP fallback
- `7882/udp` for LiveKit media

Do not publicly expose Qdrant, Image Search, Background Removal, MinIO console/API, Nominatim, Valhalla, Translation, Photon, OpenSearch, or Frappe worker ports. The public Maps origin/CDN should expose only the intended `/basemap/` objects.

## 2. Install prerequisites

```bash
sudo apt update
sudo apt install -y git nginx certbot gettext-base curl jq rsync
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker aos
```

Install Frappe/Bench separately using the approved AOS staging/production procedure.

## 3. Clone the repository

```bash
sudo -u aos git clone <AOS_REPOSITORY_URL> /home/aos/aos
cd /home/aos/aos
```

Checkout a reviewed release commit, not an unpinned branch head.

## 4. Configure secrets

```bash
cp .env.example .env
sudo chmod 600 .env
cp infra/maps/manifest.env.example infra/maps/manifest.env
sudo chmod 600 infra/maps/manifest.env
```

Replace every placeholder. All Docker images used by the map build/runtime pipeline must be pinned to immutable digests.

Configure the canonical private Photon endpoint. In production this should resolve to a health-checked private load balancer/service spanning multiple Photon replicas, not a public host or a loopback-only development endpoint:

```bash
bench --site <site> set-config maps_photon_enabled 1
bench --site <site> set-config photon_base_url http://photon:2322
bench --site <site> set-config maps_nominatim_fallback_enabled 0
bench --site <site> set-config maps_routing_enabled 1
bench --site <site> set-config valhalla_base_url http://valhalla:8002
```

Build Valhalla from the same checksum-pinned single planet PBF on a dedicated high-memory/high-disk worker. The build creates an immutable release, verification records graph/admin/timezone checksums, and activation changes only the atomic `maps/valhalla/current` symlink:

```bash
infra/maps/scripts/download-planet.sh
infra/maps/scripts/build-valhalla.sh
infra/maps/scripts/activate-valhalla.sh "${MAP_DATA_VERSION}"
docker compose --profile maps-routing up -d --force-recreate valhalla
python3 infra/maps/scripts/smoke-valhalla-global.py --base-url http://127.0.0.1:8002
```

Do not build a planet graph on an API host. Keep the previous release under `maps/valhalla/releases/` through the rollback window; rollback is the same activation command with the previous version followed by a Valhalla container recreate. Production should run multiple private Valhalla serving replicas behind a health-checked internal load balancer.

Configure internal service URLs in `.env` and keep only product limits/timeouts in AOS Settings:

```text
# .env
IMAGE_SEARCH_SERVICE_URL=http://127.0.0.1:8110
BACKGROUND_REMOVAL_SERVICE_URL=http://127.0.0.1:8120
TRANSLATION_SERVICE_URL=http://127.0.0.1:8100

# AOS Settings
image_search_service_timeout_seconds: 20
image_search_default_limit: 20
image_search_max_limit: 100
background_removal_service_timeout_seconds: 30
background_removal_max_image_bytes: 10485760
translation_service_timeout_seconds: 30
translation_max_characters: 1000
```

## 5. Build or restore map data

For a fresh global build, use dedicated build/import workers rather than request-serving hosts:

```bash
./infra/maps/scripts/download-planet.sh
./infra/maps/scripts/build-world-pmtiles.sh
# Export MAPS_OBJECT_STORAGE_* credentials, then publish the immutable PMTiles generation.
./infra/maps/scripts/publish-basemap.py
MAPS_PUBLIC_BASE_URL=https://<maps-domain>/basemap MAPS_WEB_ORIGIN=https://<web-domain> ./infra/maps/scripts/verify-basemap-origin.py
./infra/maps/scripts/build-photon-image.sh
# Import into an inactive external OpenSearch target; this command deliberately
# requires PHOTON_IMPORT_CONFIRM_TARGET=YES. Choose one maintained source path.
# ./infra/maps/scripts/import-photon.sh --from-dump
# ./infra/maps/scripts/import-photon.sh --from-nominatim
# Optional routing graph build:
# ./infra/maps/scripts/build-valhalla.sh
./infra/maps/scripts/verify-map-data.sh
```


If a previously published staging basemap exists but the dedicated bucket was left private, repair only the scoped read policy without uploading the PMTiles object again:

```bash
./infra/maps/scripts/publish-basemap.py --policy-only
./infra/maps/scripts/provision-basemap-publisher.sh
MAPS_PUBLIC_BASE_URL=https://<maps-domain>/basemap MAPS_WEB_ORIGIN=https://<web-domain> ./infra/maps/scripts/verify-basemap-origin.py
```

For disaster recovery, follow `restore.md` instead.

## 6. Validate and start Docker

```bash
docker compose config
docker compose up -d --build
docker compose ps
./infra/maps/scripts/verify-map-data.sh --services
curl http://127.0.0.1:8110/health
curl http://127.0.0.1:8110/ready
curl http://127.0.0.1:8120/health
curl http://127.0.0.1:8120/ready
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
```

## 7. Migrate Frappe

```bash
cd /home/aos/frappe-bench
bench --site <site> migrate
bench build --force
bench restart

# Rebuild image-search vectors after the image-search service is deployed.
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index --kwargs '{"dry_run": true}'
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index
```

Smoke-test the Frappe translation integration client when translation is enabled:

```bash
bench --site <site> console
```

```python
from aos.integrations.ai.translation_client import health_check, ready_check
print(health_check())
print(ready_check())
```

## 8. Configure TLS and Nginx

Ensure DNS A/AAAA records resolve to the server, then:

```bash
cd /home/aos/aos
./infra/nginx/issue-certificates.sh
./infra/nginx/install.sh
sudo nginx -t
```

## 9. Configure backups

```bash
sudo install -d -m 750 -o aos -g aos /var/backups/aos /etc/aos
sudo install -m 600 -o aos -g aos infra/backup/backup.env.example /etc/aos/backup.env
sudo cp infra/systemd/aos-backup.service /etc/systemd/system/
sudo cp infra/systemd/aos-backup.timer /etc/systemd/system/
sudo cp infra/systemd/aos-backup-verify.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aos-backup.timer
```

Edit `/etc/aos/backup.env` before enabling the timer.

## 10. Verify release

Complete every item in `release-checklist.md`. Record the deployed Git commit, image digests, map manifest checksum, and backup ID.
