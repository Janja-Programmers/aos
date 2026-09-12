#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
load_basemap_manifest
require_command sha256sum
source_file="${ROOT_DIR}/maps/downloads/${OSM_PLANET_FILENAME}"
artifact="${ROOT_DIR}/maps/basemap/${MAP_DATA_VERSION}/${BASEMAP_PMTILES_FILENAME}"
assert_nonempty_file "${source_file}"
assert_nonempty_file "${artifact}"
[[ "$(sha256_file "${source_file}")" == "${OSM_PLANET_SHA256,,}" ]] || fail "Planet checksum mismatch"
[[ "$(head -c 7 "${artifact}" || true)" == "PMTiles" ]] || fail "Basemap PMTiles header is invalid"
if [[ "${1:-}" == "--services" ]]; then
    require_command curl
    curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${PHOTON_PORT:-2322}/status" >/dev/null
fi
info "Global Maps data verification passed"
