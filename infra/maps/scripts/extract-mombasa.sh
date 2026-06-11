#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")"
    pwd
)"

# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_manifest

OSMIUM_COMMAND="${OSMIUM_COMMAND:-osmium}"

require_command "${OSMIUM_COMMAND}"

SOURCE="${ROOT_DIR}/maps/downloads/${KENYA_PBF_FILENAME}"
OUTPUT_DIR="${ROOT_DIR}/maps/${MAP_REGION_ID}"
DESTINATION="${OUTPUT_DIR}/${MOMBASA_PBF_FILENAME}"
TEMPORARY="${OUTPUT_DIR}/${MOMBASA_PBF_FILENAME}.tmp"

assert_nonempty_file "${SOURCE}"

source_checksum="$(
    sha256_file "${SOURCE}"
)"

if [[ "${source_checksum,,}" != "${KENYA_PBF_SHA256,,}" ]]; then
    fail "Source PBF checksum does not match the manifest."
fi

mkdir -p "${OUTPUT_DIR}"
rm -f "${TEMPORARY}"

cleanup() {
    rm -f "${TEMPORARY}"
}

trap cleanup EXIT

info "Extracting ${MAP_REGION_DISPLAY_NAME:-${MAP_REGION_ID}} using bbox ${MAP_BBOX}"

"${OSMIUM_COMMAND}" extract \
    --bbox "${MAP_BBOX}" \
    --strategy "${MAP_EXTRACT_STRATEGY:-complete_ways}" \
    --set-bounds \
    --overwrite \
    --output "${TEMPORARY}" \
    "${SOURCE}"

assert_nonempty_file "${TEMPORARY}"

info "Validating extracted PBF"

"${OSMIUM_COMMAND}" fileinfo \
    --extended \
    "${TEMPORARY}" \
    >/dev/null

atomic_replace \
    "${TEMPORARY}" \
    "${DESTINATION}"

trap - EXIT

info "Extract created and validated: ${DESTINATION}"

ls -lh "${DESTINATION}"
