#!/usr/bin/env bash
# One-time migration of the Certbot-managed Next.js vhost to the AOS template.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT}/.env"
TARGET="/etc/nginx/sites-available/aos-web-staging"
ENABLED="/etc/nginx/sites-enabled/aos-web-staging"
TEMPLATE="${ROOT}/infra/nginx/web.conf.template"
TLS_SOURCE="${ROOT}/infra/nginx/snippets/ssl.conf"
TLS_TARGET="/etc/nginx/snippets/aos-ssl.conf"

[[ -f "$ENV_FILE" && -f "$TEMPLATE" && -f "$TLS_SOURCE" ]] || { echo "Required input file missing" >&2; exit 1; }
# shellcheck disable=SC1091
source "${ROOT}/infra/scripts/load-dotenv.sh"
load_dotenv_file "$ENV_FILE"
AOS_WEB_DOMAIN="${AOS_WEB_DOMAIN:-aos-web-staging.duckdns.org}"
[[ "$AOS_WEB_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Invalid AOS_WEB_DOMAIN" >&2; exit 1; }
[[ "$AOS_WEB_DOMAIN" == "aos-web-staging.duckdns.org" ]] || { echo "This installer is restricted to the staging web host" >&2; exit 1; }
command -v envsubst >/dev/null
command -v nginx >/dev/null

for FILE in "/etc/letsencrypt/live/$AOS_WEB_DOMAIN/fullchain.pem" "/etc/letsencrypt/live/$AOS_WEB_DOMAIN/privkey.pem"; do
    sudo test -s "$FILE" || { echo "Missing certificate file: $FILE" >&2; exit 1; }
done
sudo test -f "$TARGET" || { echo "Missing existing web configuration" >&2; exit 1; }
[[ "$(sudo readlink -f "$ENABLED")" == "$TARGET" ]] || { echo "Unexpected enabled web configuration" >&2; exit 1; }
sudo test -f "$TLS_TARGET" || { echo "Missing installed AOS TLS snippet" >&2; exit 1; }

VERSION="$(nginx -v 2>&1 | sed -nE 's|.*nginx/([0-9]+)\.([0-9]+)\.([0-9]+).*|\1 \2 \3|p')"
[[ "$VERSION" =~ ^[0-9]+[[:space:]][0-9]+[[:space:]][0-9]+$ ]] || { echo "Unknown Nginx version" >&2; exit 1; }
read -r MAJOR MINOR PATCH <<< "$VERSION"
if (( MAJOR > 1 || (MAJOR == 1 && (MINOR > 25 || (MINOR == 25 && PATCH >= 1))) )); then
    NGINX_HTTP2_LISTEN_OPTION=""
    NGINX_HTTP2_DIRECTIVE="http2 on;"
else
    NGINX_HTTP2_LISTEN_OPTION=" http2"
    NGINX_HTTP2_DIRECTIVE=""
fi
export AOS_WEB_DOMAIN NGINX_HTTP2_LISTEN_OPTION NGINX_HTTP2_DIRECTIVE

TMP="$(mktemp)"
BACKUP_DIR="$(sudo mktemp -d /etc/nginx/aos-web-backup.XXXXXXXX)"
RESTORE_NEEDED=false
cleanup() {
    rc=$?
    if [[ "$RESTORE_NEEDED" == true ]]; then
        echo "Restoring original Nginx configuration" >&2
        sudo cp -p "$BACKUP_DIR/web" "$TARGET"
        sudo cp -p "$BACKUP_DIR/tls" "$TLS_TARGET"
        sudo nginx -t && sudo systemctl reload nginx || true
    fi
    rm -f "$TMP"
    exit "$rc"
}
trap cleanup EXIT
sudo cp -p "$TARGET" "$BACKUP_DIR/web"
sudo cp -p "$TLS_TARGET" "$BACKUP_DIR/tls"

envsubst '${AOS_WEB_DOMAIN} ${NGINX_HTTP2_LISTEN_OPTION} ${NGINX_HTTP2_DIRECTIVE}' < "$TEMPLATE" > "$TMP"
if grep -qE '\$\{(AOS|NGINX)_[A-Z0-9_]+\}' "$TMP"; then
    echo "Unresolved substitution in rendered Nginx template" >&2
    exit 1
fi
RESTORE_NEEDED=true
sudo install -m 0644 "$TMP" "$TARGET"
sudo install -m 0644 "$TLS_SOURCE" "$TLS_TARGET"
sudo nginx -t
sudo systemctl reload nginx
RESTORE_NEEDED=false
echo "PASS: Nginx web host and TLS snippet installed."
echo "Backup retained at $BACKUP_DIR (root-owned)."
echo "Next: test all endpoints and challenge paths before Certbot reconfiguration."
