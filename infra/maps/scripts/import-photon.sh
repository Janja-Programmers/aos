#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
    cat >&2 <<'EOF'
Usage:
  infra/maps/scripts/import-photon.sh --rebuild [--from-nominatim|--from-archive]

Modes:
  --from-nominatim   Build Photon index from the running Nominatim PostgreSQL DB.
  --from-archive     Recreate Photon volume from a prepared Photon archive.
  auto/default       Use archive when PHOTON_DUMP_URL or PHOTON_DUMP_LOCAL_PATH is set,
                     otherwise build from Nominatim DB.

Archive settings in infra/maps/manifest.env:
  PHOTON_DUMP_URL          optional URL to download a prepared Photon archive
  PHOTON_DUMP_LOCAL_PATH   optional local archive path, used when URL is empty
  PHOTON_DUMP_FILENAME     filename stored under maps/downloads when URL is used
  PHOTON_DUMP_SHA256       required SHA-256 checksum of the archive
  PHOTON_VOLUME            Docker volume name, default aos_photon_data
  PHOTON_SERVICE           Docker Compose service name, default photon

Nominatim import settings:
  NOMINATIM_PASSWORD             loaded from .env or environment
  PHOTON_IMPORT_DB_HOST          default aos-nominatim
  PHOTON_IMPORT_DB_PORT          default 5432
  PHOTON_IMPORT_DB_USER          default nominatim
  PHOTON_IMPORT_DB_NAME          default nominatim
  PHOTON_IMPORT_DB_PASSWORD      default NOMINATIM_PASSWORD
  PHOTON_IMPORT_COUNTRY_CODES    default ke
  PHOTON_IMPORT_LANGUAGES        default en,sw
  PHOTON_IMPORT_THREADS          default 2
  PHOTON_IMPORT_MEMORY_LIMIT     default PHOTON_MEMORY_LIMIT or 4g

Examples:
  infra/maps/scripts/import-photon.sh --rebuild
  infra/maps/scripts/import-photon.sh --rebuild --from-nominatim
  infra/maps/scripts/import-photon.sh --rebuild --from-archive
EOF
}

load_dotenv_if_present() {
    local env_file="${ROOT_DIR}/.env"

    if [[ -f "${env_file}" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "${env_file}"
        set +a
    fi
}

get_compose_service_network() {
    local service="$1"
    local container_id network_name

    container_id="$(docker compose ps -q "${service}" 2>/dev/null || true)"
    [[ -n "${container_id}" ]] || fail "Docker Compose service '${service}' is not running."

    network_name="$(
        docker inspect \
            --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
            "${container_id}" 2>/dev/null | head -n 1
    )"

    [[ -n "${network_name}" ]] || fail "Could not determine Docker network for service '${service}'."
    printf '%s\n' "${network_name}"
}

stop_existing_photon() {
    local service="$1"

    existing_container="$(docker compose ps -q "${service}" 2>/dev/null || true)"
    if [[ -n "${existing_container}" ]]; then
        info "Stopping existing Photon container"
        docker compose stop "${service}" || true
        docker compose rm -f "${service}" || true
    fi
}

wait_for_photon_health() {
    local service="$1"
    local timeout="$2"
    local start_time now container_id status

    start_time="$(date +%s)"

    while true; do
        container_id="$(docker compose ps -q "${service}" 2>/dev/null || true)"
        [[ -n "${container_id}" ]] || fail "Photon container was not created."

        status="$(
            docker inspect \
                --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
                "${container_id}" 2>/dev/null || true
        )"

        case "${status}" in
            healthy)
                info "Photon service is healthy."
                exit 0
                ;;
            unhealthy|exited|dead)
                docker compose logs --tail=200 "${service}" >&2 || true
                fail "Photon failed with container status: ${status}"
                ;;
        esac

        now="$(date +%s)"
        if (( now - start_time >= timeout )); then
            docker compose logs --tail=200 "${service}" >&2 || true
            fail "Timed out waiting for Photon after ${timeout} seconds."
        fi

        printf 'Waiting for Photon... status=%s\n' "${status:-unknown}"
        sleep 10
    done
}

import_from_archive() {
    local service="$1"
    local volume="$2"
    local timeout="$3"

    local dump_url dump_local_path dump_filename dump_sha256
    local download_dir work_dir extract_dir archive archive_checksum
    local payload_dir top_level_count

    dump_url="${PHOTON_DUMP_URL:-}"
    dump_local_path="${PHOTON_DUMP_LOCAL_PATH:-}"
    dump_filename="${PHOTON_DUMP_FILENAME:-photon-${MAP_REGION_ID}.tar.gz}"
    dump_sha256="${PHOTON_DUMP_SHA256:-}"

    [[ -n "${dump_sha256}" ]] || fail "PHOTON_DUMP_SHA256 is required for archive import."
    validate_sha256 "${dump_sha256}" "PHOTON_DUMP_SHA256"

    require_command find
    require_command tar

    download_dir="${ROOT_DIR}/maps/downloads"
    work_dir="${ROOT_DIR}/maps/photon/import-work"
    extract_dir="${work_dir}/extract"

    mkdir -p "${download_dir}" "${work_dir}"

    if [[ -n "${dump_url}" ]]; then
        require_command curl
        archive="${download_dir}/${dump_filename}"

        if [[ ! -s "${archive}" ]]; then
            info "Downloading Photon archive: ${dump_url}"
            curl \
                --fail \
                --location \
                --show-error \
                --connect-timeout 30 \
                --retry 5 \
                --retry-delay 10 \
                --output "${archive}.tmp" \
                "${dump_url}"
            atomic_replace "${archive}.tmp" "${archive}"
        else
            info "Using existing downloaded Photon archive: ${archive}"
        fi
    elif [[ -n "${dump_local_path}" ]]; then
        archive="${dump_local_path}"
    else
        fail "Set PHOTON_DUMP_URL or PHOTON_DUMP_LOCAL_PATH for archive import."
    fi

    assert_nonempty_file "${archive}"

    archive_checksum="$(sha256_file "${archive}")"
    if [[ "${archive_checksum,,}" != "${dump_sha256,,}" ]]; then
        fail "Photon archive checksum does not match PHOTON_DUMP_SHA256."
    fi

    info "Extracting Photon archive"
    rm -rf "${extract_dir}"
    mkdir -p "${extract_dir}"

    case "${archive}" in
        *.zip)
            require_command unzip
            unzip -q "${archive}" -d "${extract_dir}"
            ;;
        *.tar|*.tar.gz|*.tgz|*.tar.bz2|*.tbz2|*.tar.xz|*.txz)
            tar -xf "${archive}" -C "${extract_dir}"
            ;;
        *)
            fail "Unsupported Photon archive format: ${archive}"
            ;;
    esac

    payload_dir=""
    while IFS= read -r candidate; do
        payload_dir="${candidate}"
        break
    done < <(
        find "${extract_dir}" \
            -type d \
            -name photon_data \
            -print
    )

    if [[ -z "${payload_dir}" ]]; then
        top_level_count="$(find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
        if [[ "${top_level_count}" == "1" ]]; then
            payload_dir="$(find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
        else
            payload_dir="${extract_dir}"
        fi
    fi

    if ! find "${payload_dir}" -mindepth 1 -print -quit | grep -q .; then
        fail "Photon archive extracted, but no index files were found."
    fi

    cd "${ROOT_DIR}"

    stop_existing_photon "${service}"

    info "Recreating Photon data volume: ${volume}"
    docker volume rm -f "${volume}" >/dev/null || true
    docker volume create "${volume}" >/dev/null

    info "Copying prepared Photon index into Docker volume"
    docker run \
        --rm \
        --entrypoint /bin/sh \
        -v "${volume}:/photon/photon_data:rw" \
        -v "${payload_dir}:/import:ro" \
        "${PHOTON_IMAGE}" \
        -c 'set -e; rm -rf /photon/photon_data/* /photon/photon_data/.[!.]* /photon/photon_data/..?* 2>/dev/null || true; cp -a /import/. /photon/photon_data/; test -n "$(find /photon/photon_data -mindepth 1 -print -quit)"'

    info "Starting Photon service"
    docker compose up -d "${service}"

    wait_for_photon_health "${service}" "${timeout}"
}

import_from_nominatim() {
    local service="$1"
    local volume="$2"
    local timeout="$3"

    local nominatim_service network_name
    local db_host db_port db_user db_name db_password
    local country_codes languages threads memory_limit cpu_limit
    local docker_limits=()

    nominatim_service="${NOMINATIM_SERVICE:-nominatim}"

    db_host="${PHOTON_IMPORT_DB_HOST:-aos-nominatim}"
    db_port="${PHOTON_IMPORT_DB_PORT:-5432}"
    db_user="${PHOTON_IMPORT_DB_USER:-nominatim}"
    db_name="${PHOTON_IMPORT_DB_NAME:-nominatim}"
    db_password="${PHOTON_IMPORT_DB_PASSWORD:-${NOMINATIM_PASSWORD:-}}"

    country_codes="${PHOTON_IMPORT_COUNTRY_CODES:-ke}"
    languages="${PHOTON_IMPORT_LANGUAGES:-en,sw}"
    threads="${PHOTON_IMPORT_THREADS:-2}"
    memory_limit="${PHOTON_IMPORT_MEMORY_LIMIT:-${PHOTON_MEMORY_LIMIT:-4g}}"
    cpu_limit="${PHOTON_IMPORT_CPU_LIMIT:-${PHOTON_CPU_LIMIT:-}}"

    [[ -n "${db_password}" ]] || fail "NOMINATIM_PASSWORD or PHOTON_IMPORT_DB_PASSWORD is required for Photon Nominatim import."

    cd "${ROOT_DIR}"

    network_name="$(get_compose_service_network "${nominatim_service}")"

    stop_existing_photon "${service}"

    info "Recreating Photon data volume: ${volume}"
    docker volume rm -f "${volume}" >/dev/null || true
    docker volume create "${volume}" >/dev/null

    if [[ -n "${memory_limit}" ]]; then
        docker_limits+=(--memory "${memory_limit}")
    fi

    if [[ -n "${cpu_limit}" ]]; then
        docker_limits+=(--cpus "${cpu_limit}")
    fi

    info "Building Photon index from Nominatim database"
    info "Photon import network: ${network_name}"
    info "Photon import DB host: ${db_host}:${db_port}/${db_name}"
    info "Photon import filters: country_codes=${country_codes}, languages=${languages}, threads=${threads}"

    docker run \
        --rm \
        --network "${network_name}" \
        "${docker_limits[@]}" \
        -v "${volume}:/photon/photon_data:rw" \
        "${PHOTON_IMAGE}" \
        import \
        -data-dir /photon/photon_data \
        -host "${db_host}" \
        -port "${db_port}" \
        -user "${db_user}" \
        -password "${db_password}" \
        -database "${db_name}" \
        -country-codes "${country_codes}" \
        -languages "${languages}" \
        -j "${threads}"

    info "Starting Photon service"
    docker compose up -d "${service}"

    wait_for_photon_health "${service}" "${timeout}"
}

load_manifest
load_dotenv_if_present

require_command docker

if [[ "${1:-}" != "--rebuild" ]]; then
    usage
    fail "This operation recreates the Photon index volume. Run explicitly with: $0 --rebuild"
fi

shift

MODE="auto"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from-nominatim)
            MODE="nominatim"
            ;;
        --from-archive)
            MODE="archive"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            fail "Unknown argument: $1"
            ;;
    esac
    shift
done

: "${PHOTON_IMAGE:?PHOTON_IMAGE is required}"

validate_photon_image "${PHOTON_IMAGE}" "PHOTON_IMAGE"
ensure_photon_image "${PHOTON_IMAGE}"

SERVICE="${PHOTON_SERVICE:-photon}"
VOLUME="${PHOTON_VOLUME:-aos_photon_data}"
TIMEOUT="${PHOTON_IMPORT_TIMEOUT_SECONDS:-21600}"

DUMP_URL="${PHOTON_DUMP_URL:-}"
DUMP_LOCAL_PATH="${PHOTON_DUMP_LOCAL_PATH:-}"

if [[ "${MODE}" == "auto" ]]; then
    if [[ -n "${DUMP_URL}" || -n "${DUMP_LOCAL_PATH}" ]]; then
        MODE="archive"
    else
        MODE="nominatim"
    fi
fi

case "${MODE}" in
    archive)
        import_from_archive "${SERVICE}" "${VOLUME}" "${TIMEOUT}"
        ;;
    nominatim)
        import_from_nominatim "${SERVICE}" "${VOLUME}" "${TIMEOUT}"
        ;;
    *)
        fail "Invalid Photon import mode: ${MODE}"
        ;;
esac