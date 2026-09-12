#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
load_manifest
require_command curl
require_command sha256sum

DIR="${ROOT_DIR}/maps/downloads"
DEST="${DIR}/${OSM_PLANET_FILENAME}"
TMP="${DEST}.partial"
mkdir -p "${DIR}"
if [[ -f "${DEST}" && "$(sha256_file "${DEST}")" == "${OSM_PLANET_SHA256,,}" ]]; then
    info "Pinned planet snapshot already present: ${DEST}"
    exit 0
fi
rm -f "${TMP}"
trap 'rm -f "${TMP}"' EXIT
info "Downloading pinned OSM planet snapshot"
curl --fail --location --retry 5 --retry-all-errors --connect-timeout 30 --output "${TMP}" "${OSM_PLANET_URL}"
assert_nonempty_file "${TMP}"
actual="$(sha256_file "${TMP}")"
[[ "${actual,,}" == "${OSM_PLANET_SHA256,,}" ]] || fail "Planet SHA-256 mismatch: ${actual}"
atomic_replace "${TMP}" "${DEST}"
trap - EXIT
info "Planet snapshot verified: ${DEST}"
