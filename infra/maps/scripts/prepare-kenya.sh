#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_manifest
require_command sha256sum

SOURCE="${ROOT_DIR}/maps/downloads/${KENYA_PBF_FILENAME}"
OUTPUT_DIR="${ROOT_DIR}/maps/${MAP_REGION_ID}"
DESTINATION="${OUTPUT_DIR}/${REGION_PBF_FILENAME}"
TEMPORARY="${OUTPUT_DIR}/.${REGION_PBF_FILENAME}.tmp.osm.pbf"

assert_nonempty_file "${SOURCE}"

source_checksum="$(
    sha256_file "${SOURCE}"
)"

if [[ "${source_checksum,,}" != "${KENYA_PBF_SHA256,,}" ]]; then
    fail "Source PBF checksum does not match the manifest."
fi

mkdir -p "${OUTPUT_DIR}"
rm -f "${TEMPORARY}"
trap 'rm -f "${TEMPORARY}"' EXIT

info "Preparing ${MAP_REGION_DISPLAY_NAME} PBF"
cp "${SOURCE}" "${TEMPORARY}"

assert_nonempty_file "${TEMPORARY}"
atomic_replace "${TEMPORARY}" "${DESTINATION}"
trap - EXIT

info "Region PBF ready: ${DESTINATION}"
ls -lh "${DESTINATION}"
