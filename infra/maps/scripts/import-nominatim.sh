#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_manifest
require_command docker

if [[ "${1:-}" != "--rebuild" ]]; then
    fail "This operation recreates the Nominatim database. Run explicitly with: $0 --rebuild"
fi

SOURCE="${ROOT_DIR}/maps/${MAP_REGION_ID}/${MOMBASA_PBF_FILENAME}"
assert_nonempty_file "${SOURCE}"

SERVICE="${NOMINATIM_SERVICE:-nominatim}"
VOLUME="${NOMINATIM_VOLUME:-aos_nominatim_data}"
TIMEOUT="${NOMINATIM_IMPORT_TIMEOUT_SECONDS:-3600}"

cd "${ROOT_DIR}"

affected_container="$(docker compose ps -q "${SERVICE}" 2>/dev/null || true)"
if [[ -n "${affected_container}" ]]; then
    info "Stopping existing Nominatim container"
    docker compose stop "${SERVICE}"
    docker compose rm -f "${SERVICE}"
fi

info "Removing Nominatim database volume: ${VOLUME}"
docker volume rm -f "${VOLUME}" >/dev/null

info "Starting a clean deterministic Nominatim import"
docker compose up -d "${SERVICE}"

start_time="$(date +%s)"
while true; do
    container_id="$(docker compose ps -q "${SERVICE}" 2>/dev/null || true)"
    [[ -n "${container_id}" ]] || fail "Nominatim container was not created."

    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)"

    case "${status}" in
        healthy)
            info "Nominatim import completed and service is healthy."
            exit 0
            ;;
        unhealthy|exited|dead)
            docker compose logs --tail=200 "${SERVICE}" >&2 || true
            fail "Nominatim import failed with container status: ${status}"
            ;;
    esac

    now="$(date +%s)"
    if (( now - start_time >= TIMEOUT )); then
        docker compose logs --tail=200 "${SERVICE}" >&2 || true
        fail "Timed out waiting for Nominatim import after ${TIMEOUT} seconds."
    fi

    printf 'Waiting for Nominatim import... status=%s\n' "${status:-unknown}"
    sleep 15
done
