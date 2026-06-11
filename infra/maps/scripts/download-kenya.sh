#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_manifest
require_command curl
require_command sha256sum

DOWNLOAD_DIR="${ROOT_DIR}/maps/downloads"
DESTINATION="${DOWNLOAD_DIR}/${KENYA_PBF_FILENAME}"
TEMPORARY="${DESTINATION}.download"

mkdir -p "${DOWNLOAD_DIR}"

if [[ -f "${DESTINATION}" ]]; then
    existing_checksum="$(sha256_file "${DESTINATION}")"
    if [[ "${existing_checksum,,}" == "${KENYA_PBF_SHA256,,}" ]]; then
        info "Source PBF already exists and checksum is valid: ${DESTINATION}"
        exit 0
    fi
    info "Existing source checksum is invalid; replacing it."
fi

rm -f "${TEMPORARY}"
trap 'rm -f "${TEMPORARY}"' EXIT

info "Downloading pinned Kenya OSM snapshot"
curl --fail --location --retry 5 --retry-delay 5 --connect-timeout 20 \
    --output "${TEMPORARY}" \
    "${KENYA_PBF_URL}"

assert_nonempty_file "${TEMPORARY}"
actual_checksum="$(sha256_file "${TEMPORARY}")"

if [[ "${actual_checksum,,}" != "${KENYA_PBF_SHA256,,}" ]]; then
    fail "Source checksum mismatch. Expected ${KENYA_PBF_SHA256}, received ${actual_checksum}."
fi

atomic_replace "${TEMPORARY}" "${DESTINATION}"
trap - EXIT

info "Downloaded and verified: ${DESTINATION}"
ls -lh "${DESTINATION}"
