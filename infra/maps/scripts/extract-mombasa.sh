#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_manifest
require_command docker

SOURCE="${ROOT_DIR}/maps/downloads/${KENYA_PBF_FILENAME}"
OUTPUT_DIR="${ROOT_DIR}/maps/${MAP_REGION_ID}"
DESTINATION="${OUTPUT_DIR}/${MOMBASA_PBF_FILENAME}"
TEMPORARY="${OUTPUT_DIR}/${MOMBASA_PBF_FILENAME}.tmp"

assert_nonempty_file "${SOURCE}"
source_checksum="$(sha256_file "${SOURCE}")"
[[ "${source_checksum,,}" == "${KENYA_PBF_SHA256,,}" ]] || fail "Source PBF checksum does not match the manifest."

mkdir -p "${OUTPUT_DIR}"
rm -f "${TEMPORARY}"
trap 'rm -f "${TEMPORARY}"' EXIT

ensure_image "${OSMIUM_IMAGE}"

info "Extracting ${MAP_REGION_DISPLAY_NAME:-${MAP_REGION_ID}} using bbox ${MAP_BBOX}"
docker run --rm \
    --user "$(id -u):$(id -g)" \
    --security-opt no-new-privileges:true \
    -v "${ROOT_DIR}/maps:/data:rw" \
    "${OSMIUM_IMAGE}" \
    osmium extract \
        --bbox "${MAP_BBOX}" \
        --strategy "${MAP_EXTRACT_STRATEGY:-complete_ways}" \
        --set-bounds \
        --overwrite \
        --output "/data/${MAP_REGION_ID}/${MOMBASA_PBF_FILENAME}.tmp" \
        "/data/downloads/${KENYA_PBF_FILENAME}"

assert_nonempty_file "${TEMPORARY}"

docker run --rm \
    --security-opt no-new-privileges:true \
    -v "${ROOT_DIR}/maps:/data:ro" \
    "${OSMIUM_IMAGE}" \
    osmium fileinfo --extended "/data/${MAP_REGION_ID}/${MOMBASA_PBF_FILENAME}.tmp" >/dev/null

atomic_replace "${TEMPORARY}" "${DESTINATION}"
trap - EXIT

info "Extract created and validated: ${DESTINATION}"
ls -lh "${DESTINATION}"
