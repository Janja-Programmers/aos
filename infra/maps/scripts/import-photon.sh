#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
    cat >&2 <<'EOF'
Usage:
  infra/maps/scripts/import-photon.sh --rebuild

This script recreates the Photon Docker volume from a prepared Photon archive.
Set these values in infra/maps/manifest.env:

  PHOTON_DUMP_URL          optional URL to download a prepared Photon archive
  PHOTON_DUMP_LOCAL_PATH   optional local archive path, used when URL is empty
  PHOTON_DUMP_FILENAME     filename stored under maps/downloads when URL is used
  PHOTON_DUMP_SHA256       required SHA-256 checksum of the archive
  PHOTON_VOLUME            Docker volume name, default aos_photon_data
  PHOTON_SERVICE           Docker Compose service name, default photon

Supported archive formats:
  .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz, .zip
EOF
}

load_manifest
require_command docker
require_command find
require_command tar

if [[ "${1:-}" != "--rebuild" ]]; then
    usage
    fail "This operation recreates the Photon index volume. Run explicitly with: $0 --rebuild"
fi

if [[ $# -gt 1 ]]; then
    usage
    fail "Too many arguments."
fi

: "${PHOTON_IMAGE:?PHOTON_IMAGE is required}"
validate_digest_image "${PHOTON_IMAGE}" "PHOTON_IMAGE"

SERVICE="${PHOTON_SERVICE:-photon}"
VOLUME="${PHOTON_VOLUME:-aos_photon_data}"
TIMEOUT="${PHOTON_IMPORT_TIMEOUT_SECONDS:-21600}"
DUMP_URL="${PHOTON_DUMP_URL:-}"
DUMP_LOCAL_PATH="${PHOTON_DUMP_LOCAL_PATH:-}"
DUMP_FILENAME="${PHOTON_DUMP_FILENAME:-photon-${MAP_REGION_ID}.tar.gz}"
DUMP_SHA256="${PHOTON_DUMP_SHA256:-}"

[[ -n "${DUMP_SHA256}" ]] || fail "PHOTON_DUMP_SHA256 is required."
validate_sha256 "${DUMP_SHA256}" "PHOTON_DUMP_SHA256"

DOWNLOAD_DIR="${ROOT_DIR}/maps/downloads"
WORK_DIR="${ROOT_DIR}/maps/photon/import-work"
EXTRACT_DIR="${WORK_DIR}/extract"

mkdir -p "${DOWNLOAD_DIR}" "${WORK_DIR}"

if [[ -n "${DUMP_URL}" ]]; then
    require_command curl
    ARCHIVE="${DOWNLOAD_DIR}/${DUMP_FILENAME}"

    if [[ ! -s "${ARCHIVE}" ]]; then
        info "Downloading Photon archive: ${DUMP_URL}"
        curl \
            --fail \
            --location \
            --show-error \
            --connect-timeout 30 \
            --retry 5 \
            --retry-delay 10 \
            --output "${ARCHIVE}.tmp" \
            "${DUMP_URL}"
        atomic_replace "${ARCHIVE}.tmp" "${ARCHIVE}"
    else
        info "Using existing downloaded Photon archive: ${ARCHIVE}"
    fi
elif [[ -n "${DUMP_LOCAL_PATH}" ]]; then
    ARCHIVE="${DUMP_LOCAL_PATH}"
else
    usage
    fail "Set PHOTON_DUMP_URL or PHOTON_DUMP_LOCAL_PATH."
fi

assert_nonempty_file "${ARCHIVE}"

archive_checksum="$(sha256_file "${ARCHIVE}")"
if [[ "${archive_checksum,,}" != "${DUMP_SHA256,,}" ]]; then
    fail "Photon archive checksum does not match PHOTON_DUMP_SHA256."
fi

info "Extracting Photon archive"
rm -rf "${EXTRACT_DIR}"
mkdir -p "${EXTRACT_DIR}"

case "${ARCHIVE}" in
    *.zip)
        require_command unzip
        unzip -q "${ARCHIVE}" -d "${EXTRACT_DIR}"
        ;;
    *.tar|*.tar.gz|*.tgz|*.tar.bz2|*.tbz2|*.tar.xz|*.txz)
        tar -xf "${ARCHIVE}" -C "${EXTRACT_DIR}"
        ;;
    *)
        fail "Unsupported Photon archive format: ${ARCHIVE}"
        ;;
esac

# Prepared Photon archives are not always packaged with the same top-level
# folder name. Prefer a photon_data directory when present, otherwise use the
# first non-empty extracted directory.
PAYLOAD_DIR=""
while IFS= read -r candidate; do
    PAYLOAD_DIR="${candidate}"
    break
done < <(
    find "${EXTRACT_DIR}" \
        -type d \
        -name photon_data \
        -print
)

if [[ -z "${PAYLOAD_DIR}" ]]; then
    top_level_count="$(find "${EXTRACT_DIR}" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
    if [[ "${top_level_count}" == "1" ]]; then
        PAYLOAD_DIR="$(find "${EXTRACT_DIR}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
    else
        PAYLOAD_DIR="${EXTRACT_DIR}"
    fi
fi

if ! find "${PAYLOAD_DIR}" -mindepth 1 -print -quit | grep -q .; then
    fail "Photon archive extracted, but no index files were found."
fi

cd "${ROOT_DIR}"

existing_container="$(docker compose ps -q "${SERVICE}" 2>/dev/null || true)"
if [[ -n "${existing_container}" ]]; then
    info "Stopping existing Photon container"
    docker compose stop "${SERVICE}" || true
    docker compose rm -f "${SERVICE}" || true
fi

info "Recreating Photon data volume: ${VOLUME}"
docker volume rm -f "${VOLUME}" >/dev/null || true
docker volume create "${VOLUME}" >/dev/null

info "Copying prepared Photon index into Docker volume"
docker run \
    --rm \
    --entrypoint /bin/sh \
    -v "${VOLUME}:/photon/photon_data:rw" \
    -v "${PAYLOAD_DIR}:/import:ro" \
    "${PHOTON_IMAGE}" \
    -c 'set -e; rm -rf /photon/photon_data/* /photon/photon_data/.[!.]* /photon/photon_data/..?* 2>/dev/null || true; cp -a /import/. /photon/photon_data/; test -n "$(find /photon/photon_data -mindepth 1 -print -quit)"'

info "Starting Photon service"
docker compose up -d "${SERVICE}"

start_time="$(date +%s)"
while true; do
    container_id="$(docker compose ps -q "${SERVICE}" 2>/dev/null || true)"
    [[ -n "${container_id}" ]] || fail "Photon container was not created."

    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)"

    case "${status}" in
        healthy)
            info "Photon import completed and service is healthy."
            exit 0
            ;;
        unhealthy|exited|dead)
            docker compose logs --tail=200 "${SERVICE}" >&2 || true
            fail "Photon failed with container status: ${status}"
            ;;
    esac

    now="$(date +%s)"
    if (( now - start_time >= TIMEOUT )); then
        docker compose logs --tail=200 "${SERVICE}" >&2 || true
        fail "Timed out waiting for Photon after ${TIMEOUT} seconds."
    fi

    printf 'Waiting for Photon... status=%s\n' "${status:-unknown}"
    sleep 10
done
