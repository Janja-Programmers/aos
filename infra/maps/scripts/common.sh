#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MANIFEST_FILE="${MAP_MANIFEST_FILE:-${ROOT_DIR}/infra/maps/manifest.env}"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

info() {
    echo "==> $*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command is not installed: $1"
}

load_manifest() {
    [[ -f "${MANIFEST_FILE}" ]] || fail "Map manifest not found: ${MANIFEST_FILE}. Copy infra/maps/manifest.env.example to infra/maps/manifest.env and fill it."

    set -a
    # shellcheck disable=SC1090
    source "${MANIFEST_FILE}"
    set +a

    : "${MAP_REGION_ID:?MAP_REGION_ID is required}"
    : "${MAP_BBOX:?MAP_BBOX is required}"
    : "${KENYA_PBF_URL:?KENYA_PBF_URL is required}"
    : "${KENYA_PBF_SHA256:?KENYA_PBF_SHA256 is required}"
    : "${KENYA_PBF_FILENAME:?KENYA_PBF_FILENAME is required}"
    : "${MOMBASA_PBF_FILENAME:?MOMBASA_PBF_FILENAME is required}"
    : "${MOMBASA_MBTILES_FILENAME:?MOMBASA_MBTILES_FILENAME is required}"
    : "${OSMIUM_IMAGE:?OSMIUM_IMAGE is required}"
    : "${PLANETILER_IMAGE:?PLANETILER_IMAGE is required}"
    : "${VALHALLA_IMAGE:?VALHALLA_IMAGE is required}"

    validate_sha256 "${KENYA_PBF_SHA256}" "KENYA_PBF_SHA256"
    validate_digest_image "${OSMIUM_IMAGE}" "OSMIUM_IMAGE"
    validate_digest_image "${PLANETILER_IMAGE}" "PLANETILER_IMAGE"
    validate_digest_image "${VALHALLA_IMAGE}" "VALHALLA_IMAGE"
}

validate_sha256() {
    local value="$1"
    local label="$2"
    [[ "${value}" =~ ^[a-fA-F0-9]{64}$ ]] || fail "${label} must be a 64-character SHA-256 checksum."
}

validate_digest_image() {
    local value="$1"
    local label="$2"
    [[ "${value}" =~ ^[^[:space:]@]+@sha256:[a-f0-9]{64}$ ]] || fail "${label} must be pinned as registry/image@sha256:<64 lowercase hex characters>."
}

ensure_image() {
    local image="$1"
    docker image inspect "${image}" >/dev/null 2>&1 || docker pull "${image}"
}

sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}

assert_nonempty_file() {
    local path="$1"
    [[ -s "${path}" ]] || fail "Expected non-empty file was not created: ${path}"
}

atomic_replace() {
    local temporary="$1"
    local destination="$2"
    assert_nonempty_file "${temporary}"
    mkdir -p "$(dirname "${destination}")"
    mv -f "${temporary}" "${destination}"
}
