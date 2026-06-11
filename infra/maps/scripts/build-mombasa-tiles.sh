#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")"
    pwd
)"

# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_manifest
require_command docker

SOURCE="${ROOT_DIR}/maps/${MAP_REGION_ID}/${MOMBASA_PBF_FILENAME}"
OUTPUT_DIR="${ROOT_DIR}/maps/tiles"
DESTINATION="${OUTPUT_DIR}/${MOMBASA_MBTILES_FILENAME}"

# Keep the final .mbtiles extension so Planetiler can detect the archive type.
TEMPORARY="${OUTPUT_DIR}/.${MOMBASA_MBTILES_FILENAME}.tmp.mbtiles"
TEMPORARY_BASENAME="$(
    basename "${TEMPORARY}"
)"

TEMP_DIR="${ROOT_DIR}/maps/planetiler/tmp"

assert_nonempty_file "${SOURCE}"

mkdir -p \
    "${OUTPUT_DIR}" \
    "${TEMP_DIR}"

rm -f \
    "${TEMPORARY}" \
    "${DESTINATION}.tmp"

cleanup() {
    rm -f \
        "${TEMPORARY}" \
        "${DESTINATION}.tmp"
}

trap cleanup EXIT

ensure_image "${PLANETILER_IMAGE}"

info "Building deterministic vector tiles"

docker run \
    --rm \
    --user "$(id -u):$(id -g)" \
    --security-opt no-new-privileges:true \
    -e "JAVA_TOOL_OPTIONS=-Xmx${PLANETILER_JAVA_MEMORY:-4g}" \
    -v "${ROOT_DIR}/maps:/data:rw" \
    "${PLANETILER_IMAGE}" \
    --osm-path="/data/${MAP_REGION_ID}/${MOMBASA_PBF_FILENAME}" \
    --output="/data/tiles/${TEMPORARY_BASENAME}" \
    --bounds="${MAP_BBOX}" \
    --tmpdir=/data/planetiler/tmp \
    --download \
    --force

assert_nonempty_file "${TEMPORARY}"

# SQLite magic header: "SQLite format 3"
header="$(
    head -c 15 "${TEMPORARY}" || true
)"

if [[ "${header}" != "SQLite format 3" ]]; then
    fail "Generated MBTiles file is not a valid SQLite database."
fi

atomic_replace \
    "${TEMPORARY}" \
    "${DESTINATION}"

trap - EXIT

info "Vector tiles created: ${DESTINATION}"

ls -lh "${DESTINATION}"
