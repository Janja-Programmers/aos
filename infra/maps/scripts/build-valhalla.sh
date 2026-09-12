#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
load_valhalla_manifest
require_command docker
source_file="${ROOT_DIR}/maps/downloads/${OSM_PLANET_FILENAME}"
assert_nonempty_file "${source_file}"
ensure_image "${VALHALLA_IMAGE}"
mkdir -p "${ROOT_DIR}/maps/valhalla"
info "Building optional global Valhalla graph outside request-serving containers"
docker run --rm --security-opt no-new-privileges:true \
    -e tile_urls="" -e use_tiles_ignore_pbf=False -e force_rebuild=True \
    -e build_admins=True -e build_time_zones=True -e build_elevation=False \
    -e build_transit=False -e build_tar=True -e server_threads="${VALHALLA_BUILD_THREADS:-8}" \
    -e tileset_name="${VALHALLA_TILESET_NAME:-aos_world_valhalla}" \
    -v "${ROOT_DIR}/maps/valhalla:/custom_files:rw" \
    -v "${source_file}:/custom_files/planet.osm.pbf:ro" \
    "${VALHALLA_IMAGE}"
info "Valhalla graph build completed"
