#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
load_basemap_manifest
require_command docker

SOURCE="${ROOT_DIR}/maps/downloads/${OSM_PLANET_FILENAME}"
OUT_DIR="${ROOT_DIR}/maps/basemap/${MAP_DATA_VERSION}"
DEST="${OUT_DIR}/${BASEMAP_PMTILES_FILENAME}"
TMP="${OUT_DIR}/.${BASEMAP_PMTILES_FILENAME}.partial.pmtiles"
TMP_REL="basemap/${MAP_DATA_VERSION}/$(basename "${TMP}")"
assert_nonempty_file "${SOURCE}"
mkdir -p "${OUT_DIR}" "${ROOT_DIR}/${PLANETILER_TMP_DIR:-maps/planetiler/tmp}"
rm -f "${TMP}"
trap 'rm -f "${TMP}"' EXIT
ensure_image "${PLANETILER_IMAGE}"
info "Building global OpenMapTiles-compatible PMTiles artifact; this is an offline build job"
docker_args=(--rm --user "$(id -u):$(id -g)" --security-opt no-new-privileges:true)
if [[ -n "${PLANETILER_BUILD_CPUS:-}" && "${PLANETILER_BUILD_CPUS}" != "0" ]]; then
    docker_args+=(--cpus "${PLANETILER_BUILD_CPUS}")
fi
docker run "${docker_args[@]}" \
    -e "JAVA_TOOL_OPTIONS=-Xmx${PLANETILER_JAVA_MEMORY:-64g}" \
    -v "${ROOT_DIR}/maps:/data:rw" "${PLANETILER_IMAGE}" \
    --osm-path="/data/downloads/${OSM_PLANET_FILENAME}" \
    --output="/data/${TMP_REL}" \
    --tmpdir="/data/planetiler/tmp" \
    --download --force
assert_nonempty_file "${TMP}"
[[ "$(head -c 7 "${TMP}" || true)" == "PMTiles" ]] || fail "Generated file is not PMTiles."
atomic_replace "${TMP}" "${DEST}"
trap - EXIT
sha256_file "${DEST}" > "${DEST}.sha256"
info "Basemap ready: ${DEST}"
