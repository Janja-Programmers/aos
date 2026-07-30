# AOS Nginx and TLS deployment

## Public DNS

Create A/AAAA records pointing to the Hetzner server:

- `AOS_API_DOMAIN`
- `AOS_MAPS_DOMAIN`
- `AOS_LIVEKIT_DOMAIN`
- `AOS_MINIO_DOMAIN`

Do not expose Nominatim, Valhalla, Qdrant, translation, or the MinIO console publicly.

## Firewall

Allow:

- TCP 22 for SSH, restricted where possible
- TCP 80 and 443 for Nginx and certificate issuance
- TCP 7881 for LiveKit RTC fallback
- UDP 50000-50010 for LiveKit media

Keep Docker service ports 6333, 8002, 8080, 8081, 8100, 9100, and 9101 bound to `127.0.0.1`.

## Install packages

```bash
sudo apt update
sudo apt install -y nginx certbot gettext-base
```

## Configure

```bash
cp .env.example .env
nano .env
chmod +x infra/nginx/*.sh
```

Set real domains, secrets, server IP, image digests, and `/home/aos/frappe-bench` path.

## Start internal services

```bash
docker compose config
docker compose up -d
```

Confirm local upstreams:

```bash
curl -f http://127.0.0.1:8080/styles/aos/style.json
curl -f http://127.0.0.1:7880
curl -f http://127.0.0.1:9100/minio/health/live
curl -f http://127.0.0.1:8002/status
curl -f 'http://127.0.0.1:8081/status?format=json'
```

## Issue certificates

Ensure DNS has propagated and ports 80/443 are reachable:

```bash
./infra/nginx/issue-certificates.sh
```

## Install Nginx configs

```bash
./infra/nginx/install.sh
```

## Verify

```bash
curl -f https://${AOS_MAPS_DOMAIN}/healthz
curl -I https://${AOS_API_DOMAIN}/api/method/ping
curl -f https://${AOS_MINIO_DOMAIN}/minio/health/live
openssl s_client -connect ${AOS_LIVEKIT_DOMAIN}:443 -servername ${AOS_LIVEKIT_DOMAIN} </dev/null
```

## Automatic renewal

Certbot normally installs a systemd timer. Confirm it:

```bash
systemctl list-timers | grep certbot
sudo certbot renew --dry-run
```

Add a deploy hook if Nginx is not automatically reloaded:

```bash
sudo install -d /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

## LiveKit client URL

Use:

```text
wss://<AOS_LIVEKIT_DOMAIN>
```

The TCP fallback port `7881` and configured UDP range bypass Nginx and must remain open in the firewall.

### Browser CORS ownership

TileServer GL may emit `Access-Control-Allow-Origin` itself. The AOS Maps Nginx proxy strips that upstream header and emits one canonical wildcard header. Do not remove `proxy_hide_header Access-Control-Allow-Origin;` from the Maps proxy locations; duplicate wildcard headers are accepted by `curl` but rejected by browsers.
