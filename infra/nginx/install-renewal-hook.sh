#!/usr/bin/env bash

set -Eeuo pipefail


# PATHS

RENEWAL_HOOK_DIR="/etc/letsencrypt/renewal-hooks/deploy"
RENEWAL_HOOK="${RENEWAL_HOOK_DIR}/reload-nginx.sh"


# REQUIREMENTS

if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required." >&2
    exit 1
fi

if ! command -v nginx >/dev/null 2>&1; then
    echo "Nginx is not installed." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required." >&2
    exit 1
fi


# INSTALL DEPLOY HOOK

sudo install \
    -d \
    -m 0755 \
    "${RENEWAL_HOOK_DIR}"

temporary_file="$(
    mktemp
)"

cat > "${temporary_file}" <<'HOOK'
#!/usr/bin/env bash

set -Eeuo pipefail

nginx -t
systemctl reload nginx
HOOK

sudo install \
    -m 0755 \
    "${temporary_file}" \
    "${RENEWAL_HOOK}"

rm -f "${temporary_file}"


# ENABLE CERTBOT TIMER

if systemctl list-unit-files \
    | grep -q '^certbot\.timer'
then
    sudo systemctl enable --now certbot.timer
else
    echo "Warning: certbot.timer was not found." >&2
    echo "Check the Certbot package installation." >&2
fi


# RESULT

echo
echo "Certificate renewal hook installed:"
echo "  ${RENEWAL_HOOK}"
echo
echo "The hook validates and reloads Nginx after successful renewal."
