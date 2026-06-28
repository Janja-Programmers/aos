#!/usr/bin/env bash

set -Eeuo pipefail

# SETUP
SCRIPT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")"
    pwd
)"

# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_manifest
require_command docker


# ARGUMENTS
VERIFY_SERVICES=false

case "${1:-}" in
    "")
        ;;

    --services)
        VERIFY_SERVICES=true
        ;;

    *)
        fail "Unknown argument: ${1}. Supported argument: $0 [--services]"
        ;;
esac

if [[ $# -gt 1 ]]; then
    fail "Too many arguments. Supported usage: $0 [--services]"
fi


# ARTIFACT PATHS
SOURCE="${ROOT_DIR}/maps/downloads/${KENYA_PBF_FILENAME}"
REGION_PBF="${ROOT_DIR}/maps/${MAP_REGION_ID}/${REGION_PBF_FILENAME}"
MBTILES="${ROOT_DIR}/maps/tiles/${REGION_MBTILES_FILENAME}"
VALHALLA_DIR="${ROOT_DIR}/maps/valhalla"

VALHALLA_CONFIG="${VALHALLA_DIR}/valhalla.json"
PHOTON_VOLUME_NAME="${PHOTON_VOLUME:-aos_photon_data}"


# STATIC ARTIFACT VERIFICATION
info "Verifying static map artifacts"

assert_nonempty_file "${SOURCE}"
assert_nonempty_file "${REGION_PBF}"
assert_nonempty_file "${MBTILES}"
assert_nonempty_file "${VALHALLA_CONFIG}"


# Verify the downloaded Kenya source against the committed manifest value.
source_checksum="$(
    sha256_file "${SOURCE}"
)"

if [[ "${source_checksum,,}" != "${KENYA_PBF_SHA256,,}" ]]; then
    fail "Downloaded source checksum does not match the manifest."
fi


# MBTiles is an SQLite database and must start with the SQLite file header.
mbtiles_header="$(
    head -c 15 "${MBTILES}" || true
)"

if [[ "${mbtiles_header}" != "SQLite format 3" ]]; then
    fail "MBTiles artifact is not a valid SQLite database."
fi


# Verify that Valhalla generated routing data exists.
if ! find "${VALHALLA_DIR}" \
    -maxdepth 4 \
    -type f \
    \( \
        -name "*.tar" \
        -o -name "*.gph" \
    \) \
    -print \
    -quit |
    grep -q .
then
    fail "Valhalla routing artifacts are missing."
fi


# Ensure Docker Compose can resolve the current environment and configuration.
cd "${ROOT_DIR}"

docker compose config >/dev/null


# Photon uses a Docker volume, not a committed file artifact. Verify that the
# volume exists and is not empty before runtime checks pass.
if ! docker volume inspect "${PHOTON_VOLUME_NAME}" >/dev/null 2>&1; then
    fail "Photon data volume does not exist: ${PHOTON_VOLUME_NAME}. Run import-photon.sh --rebuild."
fi

if [[ -z "${PHOTON_IMAGE:-}" ]]; then
    fail "PHOTON_IMAGE is required to verify the Photon data volume."
fi

if ! docker run \
    --rm \
    --entrypoint /bin/sh \
    -v "${PHOTON_VOLUME_NAME}:/photon/photon_data:ro" \
    "${PHOTON_IMAGE}" \
    -c 'find /photon/photon_data -mindepth 1 -maxdepth 3 -print -quit | grep -q .'
then
    fail "Photon data volume is empty: ${PHOTON_VOLUME_NAME}. Run import-photon.sh --rebuild."
fi


# STATIC VERIFICATION RESULT
info "Static map artifacts are valid."

printf 'Source PBF:      %s\n' "${SOURCE}"
printf 'Region PBF:      %s\n' "${REGION_PBF}"
printf 'MBTiles:         %s\n' "${MBTILES}"
printf 'Valhalla config: %s\n' "${VALHALLA_CONFIG}"
printf 'Valhalla data:   %s\n' "${VALHALLA_DIR}"
printf 'Photon volume:   %s\n' "${PHOTON_VOLUME_NAME}"


# OPTIONAL RUNTIME SERVICE VERIFICATION
if [[ "${VERIFY_SERVICES}" == true ]]; then
    require_command curl

    wait_http() {
        local url="$1"
        local label="$2"
        local attempts="${3:-60}"
        local sleep_seconds="${4:-5}"

        local attempt

        for ((attempt = 1; attempt <= attempts; attempt++)); do
            if curl \
                --fail \
                --silent \
                --show-error \
                --connect-timeout 5 \
                --max-time 15 \
                "${url}" \
                >/dev/null
            then
                info "${label} is healthy"
                return 0
            fi

            if ((attempt < attempts)); then
                printf \
                    'Waiting for %s (%d/%d)...\n' \
                    "${label}" \
                    "${attempt}" \
                    "${attempts}"

                sleep "${sleep_seconds}"
            fi
        done

        fail "${label} did not become healthy: ${url}"
    }

    info "Starting map services for runtime verification"

    docker compose up \
        -d \
        tileserver \
        valhalla \
        nominatim \
        photon

    info "Verifying TileServer production style"

    wait_http \
        "http://127.0.0.1:${TILESERVER_PORT:-8080}/styles/aos/style.json" \
        "TileServer AOS style"

    info "Verifying TileServer Kenya data source"

    wait_http \
        "http://127.0.0.1:${TILESERVER_PORT:-8080}/data/kenya.json" \
        "TileServer Kenya data source"

    info "Verifying Valhalla"

    wait_http \
        "http://127.0.0.1:${VALHALLA_PORT:-8002}/status" \
        "Valhalla"

    info "Verifying Nominatim"

    wait_http \
        "http://127.0.0.1:${NOMINATIM_PORT:-8081}/status?format=json" \
        "Nominatim"

    info "Verifying Photon"

    wait_http \
        "http://127.0.0.1:${PHOTON_PORT:-2322}/api?q=Nairobi&limit=1" \
        "Photon"

    info "All map services passed runtime verification."
fi
