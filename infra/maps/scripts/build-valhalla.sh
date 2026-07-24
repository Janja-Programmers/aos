#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_manifest
require_command docker
require_command rsync

SOURCE="${ROOT_DIR}/maps/${MAP_REGION_ID}/${REGION_PBF_FILENAME}"
LIVE_DIR="${ROOT_DIR}/maps/valhalla"
STAGING_DIR="${ROOT_DIR}/maps/valhalla.build"
BACKUP_DIR="${ROOT_DIR}/maps/valhalla.previous"

assert_nonempty_file "${SOURCE}"
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}"
cp "${SOURCE}" "${STAGING_DIR}/${REGION_PBF_FILENAME}"

ensure_image "${VALHALLA_IMAGE}"

cleanup() {
    rm -rf "${STAGING_DIR}"
}
trap cleanup EXIT

info "Building Valhalla routing artifacts in staging directory"
docker run --rm \
    --security-opt no-new-privileges:true \
    -e tile_urls="" \
    -e use_tiles_ignore_pbf="False" \
    -e force_rebuild="True" \
    -e build_admins="True" \
    -e build_time_zones="True" \
    -e build_elevation="False" \
    -e build_transit="False" \
    -e build_tar="True" \
    -e serve_tiles="False" \
    -e server_threads="${VALHALLA_BUILD_THREADS:-2}" \
    -e tileset_name="${VALHALLA_TILESET_NAME:-kenya_valhalla_tiles}" \
    -e update_existing_config="True" \
    -e use_default_speeds_config="True" \
    -v "${STAGING_DIR}:/custom_files:rw" \
    "${VALHALLA_IMAGE}"

config_file="${STAGING_DIR}/valhalla.json"
[[ -s "${config_file}" ]] || fail "Valhalla build did not create valhalla.json."

if ! find "${STAGING_DIR}" -maxdepth 3 -type f \( -name '*.tar' -o -name '*.gph' \) -print -quit | grep -q .; then
    fail "Valhalla build did not create routing tiles or a tile archive."
fi

rm -rf "${BACKUP_DIR}"
if [[ -d "${LIVE_DIR}" ]]; then
    mv "${LIVE_DIR}" "${BACKUP_DIR}"
fi
mv "${STAGING_DIR}" "${LIVE_DIR}"
trap - EXIT

info "Valhalla artifacts installed atomically: ${LIVE_DIR}"
find "${LIVE_DIR}" -maxdepth 2 -type f -printf '%P\n' | sort | head -40
