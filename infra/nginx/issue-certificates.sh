#!/usr/bin/env bash

set -Eeuo pipefail


# PATHS

ROOT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/../.."
    pwd
)"

ENV_FILE="${ROOT_DIR}/.env"
NGINX_DIR="${ROOT_DIR}/infra/nginx"

BOOTSTRAP_TEMPLATE="${NGINX_DIR}/acme-bootstrap.conf.template"

BOOTSTRAP_AVAILABLE="/etc/nginx/sites-available/aos-acme-bootstrap.conf"
BOOTSTRAP_ENABLED="/etc/nginx/sites-enabled/aos-acme-bootstrap.conf"

LETSENCRYPT_WEBROOT="/var/www/letsencrypt"


# ERROR HANDLING

cleanup() {
    sudo rm -f "${BOOTSTRAP_ENABLED}"
    sudo rm -f "${BOOTSTRAP_AVAILABLE}"
}

on_error() {
    local exit_code=$?
    local line_number="${1:-unknown}"

    echo >&2
    echo "Certificate issuance failed." >&2
    echo "Line: ${line_number}" >&2
    echo "Exit code: ${exit_code}" >&2

    cleanup || true

    if sudo nginx -t >/dev/null 2>&1; then
        sudo systemctl reload nginx || true
    fi

    exit "${exit_code}"
}

trap 'on_error ${LINENO}' ERR


# HELPERS

require_command() {
    local command_name="$1"
    local installation_hint="${2:-}"

    if command -v "${command_name}" >/dev/null 2>&1; then
        return
    fi

    echo "Required command is not installed: ${command_name}" >&2

    if [[ -n "${installation_hint}" ]]; then
        echo "${installation_hint}" >&2
    fi

    exit 1
}


require_file() {
    local file_path="$1"
    local description="$2"

    if [[ -f "${file_path}" ]]; then
        return
    fi

    echo "Missing ${description}:" >&2
    echo "  ${file_path}" >&2
    exit 1
}


require_variable() {
    local variable_name="$1"
    local variable_value="${!variable_name:-}"

    if [[ -n "${variable_value}" ]]; then
        return
    fi

    echo "Missing required environment variable:" >&2
    echo "  ${variable_name}" >&2
    exit 1
}


validate_domain() {
    local domain="$1"
    local label="$2"

    if [[ "${domain}" =~ ^[A-Za-z0-9.-]+$ ]]; then
        return
    fi

    echo "${label} contains invalid characters:" >&2
    echo "  ${domain}" >&2
    exit 1
}


certificate_exists() {
    local domain="$1"

    [[ -s "/etc/letsencrypt/live/${domain}/fullchain.pem" ]] &&
    [[ -s "/etc/letsencrypt/live/${domain}/privkey.pem" ]]
}


issue_certificate() {
    local domain="$1"

    if certificate_exists "${domain}"; then
        echo "Certificate already exists:"
        echo "  ${domain}"
        return
    fi

    echo "Issuing certificate:"
    echo "  ${domain}"

    sudo certbot certonly \
        --webroot \
        --webroot-path "${LETSENCRYPT_WEBROOT}" \
        --domain "${domain}" \
        --email "${TLS_ADMIN_EMAIL}" \
        --agree-tos \
        --non-interactive \
        --no-eff-email
}


# LOAD ENVIRONMENT

require_file "${ENV_FILE}" "environment file"

set -a

# shellcheck disable=SC1090
source "${ENV_FILE}"

set +a


# REQUIRED TOOLS

require_command \
    "sudo"

require_command \
    "nginx" \
    "Install Nginx: sudo apt install -y nginx"

require_command \
    "certbot" \
    "Install Certbot: sudo apt install -y certbot"

require_command \
    "envsubst" \
    "Install envsubst: sudo apt install -y gettext-base"

require_command \
    "systemctl"


# REQUIRED CONFIGURATION

required_variables=(
    AOS_API_DOMAIN
    AOS_MAPS_DOMAIN
    AOS_LIVEKIT_DOMAIN
    AOS_MINIO_DOMAIN
    TLS_ADMIN_EMAIL
)

for variable_name in "${required_variables[@]}"; do
    require_variable "${variable_name}"
done

validate_domain \
    "${AOS_API_DOMAIN}" \
    "AOS_API_DOMAIN"

validate_domain \
    "${AOS_MAPS_DOMAIN}" \
    "AOS_MAPS_DOMAIN"

validate_domain \
    "${AOS_LIVEKIT_DOMAIN}" \
    "AOS_LIVEKIT_DOMAIN"

validate_domain \
    "${AOS_MINIO_DOMAIN}" \
    "AOS_MINIO_DOMAIN"

require_file \
    "${BOOTSTRAP_TEMPLATE}" \
    "ACME bootstrap Nginx template"


# PREPARE WEBROOT

sudo install \
    -d \
    -m 0755 \
    "${LETSENCRYPT_WEBROOT}"

sudo install \
    -d \
    -m 0755 \
    /etc/nginx/sites-available \
    /etc/nginx/sites-enabled


# INSTALL TEMPORARY HTTP CONFIGURATION

temporary_file="$(
    mktemp
)"

envsubst \
    '${AOS_API_DOMAIN} ${AOS_MAPS_DOMAIN} ${AOS_LIVEKIT_DOMAIN} ${AOS_MINIO_DOMAIN}' \
    < "${BOOTSTRAP_TEMPLATE}" \
    > "${temporary_file}"

sudo install \
    -m 0644 \
    "${temporary_file}" \
    "${BOOTSTRAP_AVAILABLE}"

rm -f "${temporary_file}"

sudo ln -sfn \
    "${BOOTSTRAP_AVAILABLE}" \
    "${BOOTSTRAP_ENABLED}"


# START OR RELOAD NGINX

sudo nginx -t

if sudo systemctl is-active --quiet nginx; then
    sudo systemctl reload nginx
else
    sudo systemctl enable --now nginx
fi


# VERIFY ACME WEBROOT LOCALLY

challenge_file="aos-acme-test-$$"

printf 'aos-acme-ok\n' |
sudo tee \
    "${LETSENCRYPT_WEBROOT}/${challenge_file}" \
    >/dev/null

for domain in \
    "${AOS_API_DOMAIN}" \
    "${AOS_MAPS_DOMAIN}" \
    "${AOS_LIVEKIT_DOMAIN}" \
    "${AOS_MINIO_DOMAIN}"
do
    response="$(
        curl \
            --fail \
            --silent \
            --show-error \
            --resolve "${domain}:80:127.0.0.1" \
            "http://${domain}/.well-known/acme-challenge/${challenge_file}"
    )"

    if [[ "${response}" != "aos-acme-ok" ]]; then
        echo "ACME challenge verification failed for:" >&2
        echo "  ${domain}" >&2
        exit 1
    fi
done

sudo rm -f \
    "${LETSENCRYPT_WEBROOT}/${challenge_file}"


# ISSUE CERTIFICATES

issue_certificate "${AOS_API_DOMAIN}"
issue_certificate "${AOS_MAPS_DOMAIN}"
issue_certificate "${AOS_LIVEKIT_DOMAIN}"
issue_certificate "${AOS_MINIO_DOMAIN}"


# REMOVE TEMPORARY CONFIGURATION

cleanup

sudo nginx -t
sudo systemctl reload nginx


# RESULT

echo
echo "Certificates issued successfully."
echo
echo "Certificates:"
echo "  ${AOS_API_DOMAIN}"
echo "  ${AOS_MAPS_DOMAIN}"
echo "  ${AOS_LIVEKIT_DOMAIN}"
echo "  ${AOS_MINIO_DOMAIN}"
echo
echo "Next:"
echo "  ./infra/nginx/install.sh"
echo "  ./infra/nginx/install-renewal-hook.sh"
