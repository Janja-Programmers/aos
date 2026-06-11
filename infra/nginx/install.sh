#!/usr/bin/env bash

set -Eeuo pipefail


# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/../.."
  pwd
)"

ENV_FILE="${ROOT_DIR}/.env"
NGINX_DIR="${ROOT_DIR}/infra/nginx"

NGINX_SNIPPETS_DIR="/etc/nginx/snippets"
NGINX_CONF_D_DIR="/etc/nginx/conf.d"
NGINX_SITES_AVAILABLE_DIR="/etc/nginx/sites-available"
NGINX_SITES_ENABLED_DIR="/etc/nginx/sites-enabled"

LETSENCRYPT_WEBROOT="/var/www/letsencrypt"


# =============================================================================
# ERROR HANDLING
# =============================================================================

on_error() {
  local exit_code=$?
  local line_number="${1:-unknown}"

  echo >&2
  echo "Nginx installation failed." >&2
  echo "Line: ${line_number}" >&2
  echo "Exit code: ${exit_code}" >&2

  exit "${exit_code}"
}

trap 'on_error ${LINENO}' ERR


# =============================================================================
# HELPERS
# =============================================================================

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


validate_timeout() {
  local value="$1"
  local label="$2"

  if [[ "${value}" =~ ^[0-9]+(ms|s|m|h|d)?$ ]]; then
    return
  fi

  echo "${label} must be a valid Nginx duration, for example 10s or 5m:" >&2
  echo "  ${value}" >&2
  exit 1
}


validate_port() {
  local value="$1"
  local label="$2"

  if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
    echo "${label} must be an integer:" >&2
    echo "  ${value}" >&2
    exit 1
  fi

  if (( value < 1 || value > 65535 )); then
    echo "${label} must be between 1 and 65535:" >&2
    echo "  ${value}" >&2
    exit 1
  fi
}


render_file() {
  local source_file="$1"
  local destination_file="$2"
  local substitution_variables="$3"

  require_file "${source_file}" "Nginx template"

  local temporary_file

  temporary_file="$(
    mktemp
  )"

  envsubst "${substitution_variables}" \
    < "${source_file}" \
    > "${temporary_file}"

  sudo install \
    -m 0644 \
    "${temporary_file}" \
    "${destination_file}"

  rm -f "${temporary_file}"
}


render_site() {
  local source_file="$1"
  local destination_name="$2"

  local destination_file
  destination_file="${NGINX_SITES_AVAILABLE_DIR}/${destination_name}"

  render_file \
    "${source_file}" \
    "${destination_file}" \
    "${SITE_SUBSTITUTION_VARIABLES}"

  sudo ln -sfn \
    "${destination_file}" \
    "${NGINX_SITES_ENABLED_DIR}/${destination_name}"
}


render_proxy_common_snippet() {
  local source_file
  local destination_file

  source_file="${NGINX_DIR}/snippets/proxy-common.conf.template"
  destination_file="${NGINX_SNIPPETS_DIR}/aos-proxy-common.conf"

  render_file \
    "${source_file}" \
    "${destination_file}" \
    "${PROXY_SNIPPET_SUBSTITUTION_VARIABLES}"
}


install_static_snippet() {
  local source_file="$1"
  local destination_file="$2"
  local description="$3"

  require_file "${source_file}" "${description}"

  sudo install \
    -m 0644 \
    "${source_file}" \
    "${destination_file}"
}


validate_certificate() {
  local domain="$1"

  local certificate_dir
  certificate_dir="/etc/letsencrypt/live/${domain}"

  require_file \
    "${certificate_dir}/fullchain.pem" \
    "TLS certificate for ${domain}"

  require_file \
    "${certificate_dir}/privkey.pem" \
    "TLS private key for ${domain}"
}


# =============================================================================
# LOAD ENVIRONMENT
# =============================================================================

require_file "${ENV_FILE}" "environment file"

set -a

# shellcheck disable=SC1090
source "${ENV_FILE}"

set +a


# =============================================================================
# REQUIRED TOOLS
# =============================================================================

require_command \
  "sudo" \
  "Install sudo or run the script in an environment where sudo is available."

require_command \
  "nginx" \
  "Install Nginx first: sudo apt install -y nginx"

require_command \
  "envsubst" \
  "Install envsubst first: sudo apt install -y gettext-base"

require_command \
  "mktemp"

require_command \
  "systemctl"


# =============================================================================
# REQUIRED ENVIRONMENT VARIABLES
# =============================================================================

required_variables=(
  AOS_API_DOMAIN
  AOS_MAPS_DOMAIN
  AOS_LIVEKIT_DOMAIN
  AOS_MINIO_DOMAIN

  FRAPPE_BENCH_PATH
  FRAPPE_WEB_HOST
  FRAPPE_WEB_PORT
  FRAPPE_SOCKETIO_HOST
  FRAPPE_SOCKETIO_PORT

  TILESERVER_PORT
  LIVEKIT_PORT
  MINIO_API_PORT

  NGINX_CLIENT_MAX_BODY_SIZE
  NGINX_PROXY_CONNECT_TIMEOUT
  NGINX_PROXY_READ_TIMEOUT
  NGINX_PROXY_SEND_TIMEOUT
)

for variable_name in "${required_variables[@]}"; do
  require_variable "${variable_name}"
done


# =============================================================================
# VALUE VALIDATION
# =============================================================================

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

validate_port \
  "${FRAPPE_WEB_PORT}" \
  "FRAPPE_WEB_PORT"

validate_port \
  "${FRAPPE_SOCKETIO_PORT}" \
  "FRAPPE_SOCKETIO_PORT"

validate_port \
  "${TILESERVER_PORT}" \
  "TILESERVER_PORT"

validate_port \
  "${LIVEKIT_PORT}" \
  "LIVEKIT_PORT"

validate_port \
  "${MINIO_API_PORT}" \
  "MINIO_API_PORT"

validate_timeout \
  "${NGINX_PROXY_CONNECT_TIMEOUT}" \
  "NGINX_PROXY_CONNECT_TIMEOUT"

validate_timeout \
  "${NGINX_PROXY_READ_TIMEOUT}" \
  "NGINX_PROXY_READ_TIMEOUT"

validate_timeout \
  "${NGINX_PROXY_SEND_TIMEOUT}" \
  "NGINX_PROXY_SEND_TIMEOUT"

if [[ ! -d "${FRAPPE_BENCH_PATH}" ]]; then
  echo "Frappe bench directory does not exist:" >&2
  echo "  ${FRAPPE_BENCH_PATH}" >&2
  exit 1
fi


# =============================================================================
# REQUIRED SOURCE FILES
# =============================================================================

require_file \
  "${NGINX_DIR}/aos-api.conf.template" \
  "AOS API Nginx template"

require_file \
  "${NGINX_DIR}/maps.conf.template" \
  "AOS Maps Nginx template"

require_file \
  "${NGINX_DIR}/livekit.conf.template" \
  "AOS LiveKit Nginx template"

require_file \
  "${NGINX_DIR}/minio.conf.template" \
  "AOS MinIO Nginx template"

require_file \
  "${NGINX_DIR}/snippets/proxy-common.conf.template" \
  "proxy-common snippet template"

require_file \
  "${NGINX_DIR}/snippets/security-headers.conf" \
  "security headers snippet"

require_file \
  "${NGINX_DIR}/snippets/ssl.conf" \
  "TLS snippet"

require_file \
  "${NGINX_DIR}/snippets/websocket-map.conf" \
  "WebSocket map configuration"


# =============================================================================
# TLS CERTIFICATES
# =============================================================================

domains=(
  "${AOS_API_DOMAIN}"
  "${AOS_MAPS_DOMAIN}"
  "${AOS_LIVEKIT_DOMAIN}"
  "${AOS_MINIO_DOMAIN}"
)

for domain in "${domains[@]}"; do
  validate_certificate "${domain}"
done


# =============================================================================
# CREATE NGINX DIRECTORIES
# =============================================================================

sudo install \
  -d \
  -m 0755 \
  "${NGINX_SNIPPETS_DIR}" \
  "${NGINX_CONF_D_DIR}" \
  "${NGINX_SITES_AVAILABLE_DIR}" \
  "${NGINX_SITES_ENABLED_DIR}" \
  "${LETSENCRYPT_WEBROOT}"


# =============================================================================
# SUBSTITUTION VARIABLE SETS
# =============================================================================

# Restrict envsubst to these variables so native Nginx variables such as
# $host, $request_uri, $remote_addr, and $http_upgrade remain unchanged.
PROXY_SNIPPET_SUBSTITUTION_VARIABLES='
${NGINX_PROXY_CONNECT_TIMEOUT}
${NGINX_PROXY_READ_TIMEOUT}
${NGINX_PROXY_SEND_TIMEOUT}
'

SITE_SUBSTITUTION_VARIABLES='
${AOS_API_DOMAIN}
${AOS_MAPS_DOMAIN}
${AOS_LIVEKIT_DOMAIN}
${AOS_MINIO_DOMAIN}

${FRAPPE_BENCH_PATH}
${FRAPPE_WEB_HOST}
${FRAPPE_WEB_PORT}
${FRAPPE_SOCKETIO_HOST}
${FRAPPE_SOCKETIO_PORT}

${TILESERVER_PORT}
${LIVEKIT_PORT}
${MINIO_API_PORT}

${NGINX_CLIENT_MAX_BODY_SIZE}
${NGINX_PROXY_CONNECT_TIMEOUT}
${NGINX_PROXY_READ_TIMEOUT}
${NGINX_PROXY_SEND_TIMEOUT}
'


# =============================================================================
# INSTALL SHARED CONFIGURATION
# =============================================================================

render_proxy_common_snippet

install_static_snippet \
  "${NGINX_DIR}/snippets/security-headers.conf" \
  "${NGINX_SNIPPETS_DIR}/aos-security-headers.conf" \
  "security headers snippet"

install_static_snippet \
  "${NGINX_DIR}/snippets/ssl.conf" \
  "${NGINX_SNIPPETS_DIR}/aos-ssl.conf" \
  "TLS snippet"

install_static_snippet \
  "${NGINX_DIR}/snippets/websocket-map.conf" \
  "${NGINX_CONF_D_DIR}/aos-websocket-map.conf" \
  "WebSocket map configuration"


# =============================================================================
# RENDER SITE CONFIGURATIONS
# =============================================================================

render_site \
  "${NGINX_DIR}/aos-api.conf.template" \
  "aos-api.conf"

render_site \
  "${NGINX_DIR}/maps.conf.template" \
  "aos-maps.conf"

render_site \
  "${NGINX_DIR}/livekit.conf.template" \
  "aos-livekit.conf"

render_site \
  "${NGINX_DIR}/minio.conf.template" \
  "aos-minio.conf"


# =============================================================================
# REMOVE DEFAULT SITE
# =============================================================================

if [[ -L "${NGINX_SITES_ENABLED_DIR}/default" ]]; then
  sudo rm -f \
    "${NGINX_SITES_ENABLED_DIR}/default"
fi


# =============================================================================
# VERIFY RENDERING
# =============================================================================

if sudo grep -R \
  --line-number \
  --fixed-strings \
  '${NGINX_' \
  "${NGINX_SNIPPETS_DIR}/aos-proxy-common.conf" \
  "${NGINX_SITES_AVAILABLE_DIR}/aos-api.conf" \
  "${NGINX_SITES_AVAILABLE_DIR}/aos-maps.conf" \
  "${NGINX_SITES_AVAILABLE_DIR}/aos-livekit.conf" \
  "${NGINX_SITES_AVAILABLE_DIR}/aos-minio.conf"
then
  echo "Unresolved deployment variables remain in rendered Nginx files." >&2
  exit 1
fi


# =============================================================================
# VALIDATE AND RELOAD NGINX
# =============================================================================

echo "Validating Nginx configuration..."

sudo nginx -t

echo "Reloading Nginx..."

if sudo systemctl is-active --quiet nginx; then
  sudo systemctl reload nginx
else
  sudo systemctl enable --now nginx
fi


# =============================================================================
# RESULT
# =============================================================================

echo
echo "Nginx configurations installed successfully."
echo
echo "Configured endpoints:"
echo "  API:     https://${AOS_API_DOMAIN}"
echo "  Maps:    https://${AOS_MAPS_DOMAIN}"
echo "  LiveKit: https://${AOS_LIVEKIT_DOMAIN}"
echo "  Files:   https://${AOS_MINIO_DOMAIN}"
echo
echo "Rendered proxy snippet:"
echo "  ${NGINX_SNIPPETS_DIR}/aos-proxy-common.conf"