# AOS Production Deployment on Hetzner

## 1. Prepare the server

Use Ubuntu 24.04, create the non-root `aos` user, enable SSH keys, disable password authentication, and configure the Hetzner firewall.

Required public ports:

- `22/tcp` from trusted administrator addresses
- `80/tcp`
- `443/tcp`
- `7881/tcp` for LiveKit TCP fallback
- `50000-50010/udp` for LiveKit media

Do not publicly expose Qdrant, MinIO console, Nominatim, Valhalla, Translation, TileServer raw port, or Frappe worker ports.

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

Configure Frappe:

```bash
bench --site <site> set-config nominatim_base_url http://127.0.0.1:8081
bench --site <site> set-config valhalla_base_url http://127.0.0.1:8002
```

## 5. Build or restore map data

For a fresh build:

```bash
./infra/maps/scripts/download-kenya.sh
./infra/maps/scripts/extract-mombasa.sh
./infra/maps/scripts/build-mombasa-tiles.sh
./infra/maps/scripts/build-valhalla.sh
./infra/maps/scripts/import-nominatim.sh --rebuild
./infra/maps/scripts/verify-map-data.sh
```

For disaster recovery, follow `restore.md` instead.

## 6. Validate and start Docker

```bash
docker compose config
docker compose up -d --build
docker compose ps
./infra/maps/scripts/verify-map-data.sh --services
```

## 7. Migrate Frappe

```bash
cd /home/aos/frappe-bench
bench --site <site> migrate
bench build --force
bench restart
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
