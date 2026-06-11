#!/usr/bin/env bash

set -Eeuo pipefail

# PATHS
SCRIPT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")"
    pwd
)"

ROOT_DIR="$(
    cd "${SCRIPT_DIR}/../../.."
    pwd
)"

MANIFEST_FILE="${MAP_MANIFEST_FILE:-${ROOT_DIR}/infra/maps/manifest.env}"

# LOGGING
fail() {
    echo "ERROR: $*" >&2
    exit 1
}


info() {
    echo "==> $*"
}


# REQUIREMENTS
require_command() {
    local command_name="$1"

    command -v "${command_name}" >/dev/null 2>&1 ||
        fail "Required command is not installed: ${command_name}"
}


# MANIFEST
load_manifest() {
    if [[ ! -f "${MANIFEST_FILE}" ]]; then
        fail \
            "Map manifest not found: ${MANIFEST_FILE}. " \
            "Copy infra/maps/manifest.env.example to " \
            "infra/maps/manifest.env and fill it."
    fi

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

    : "${OSMIUM_COMMAND:?OSMIUM_COMMAND is required}"

    : "${PLANETILER_IMAGE:?PLANETILER_IMAGE is required}"
    : "${VALHALLA_IMAGE:?VALHALLA_IMAGE is required}"

    validate_sha256 \
        "${KENYA_PBF_SHA256}" \
        "KENYA_PBF_SHA256"

    validate_digest_image \
        "${PLANETILER_IMAGE}" \
        "PLANETILER_IMAGE"

    validate_digest_image \
        "${VALHALLA_IMAGE}" \
        "VALHALLA_IMAGE"

    validate_osmium_command \
        "${OSMIUM_COMMAND}"
}


# VALIDATION
validate_sha256() {
    local value="$1"
    local label="$2"

    if [[ ! "${value}" =~ ^[a-fA-F0-9]{64}$ ]]; then
        fail \
            "${label} must be a 64-character SHA-256 checksum."
    fi
}


validate_digest_image() {
    local value="$1"
    local label="$2"

    if [[ ! "${value}" =~ ^[^[:space:]@]+@sha256:[a-f0-9]{64}$ ]]; then
        fail \
            "${label} must be pinned as " \
            "registry/image@sha256:<64 lowercase hex characters>."
    fi
}


validate_osmium_command() {
    local command_name="$1"

    if [[ "${command_name}" =~ [[:space:]] ]]; then
        fail \
            "OSMIUM_COMMAND must be a command name or absolute path " \
            "without arguments."
    fi
}


# DOCKER IMAGE HELPERS
ensure_image() {
    local image="$1"

    if docker image inspect "${image}" >/dev/null 2>&1; then
        return
    fi

    docker pull "${image}"
}


# FILE HELPERS
sha256_file() {
    local path="$1"

    sha256sum "${path}" |
        awk '{print $1}'
}


assert_nonempty_file() {
    local path="$1"

    if [[ ! -s "${path}" ]]; then
        fail \
            "Expected non-empty file was not created: ${path}"
    fi
}


atomic_replace() {
    local temporary="$1"
    local destination="$2"

    assert_nonempty_file "${temporary}"

    mkdir -p "$(
        dirname "${destination}"
    )"

    mv -f \
        "${temporary}" \
        "${destination}"
}
